"""Loopback-only web workspace. Credentials are passed in memory, never stored."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import secrets
import threading
import time

from flask import Flask, jsonify, render_template, request, send_file, session
from werkzeug.exceptions import HTTPException

from .errors import ConfigError, InputError, IntegrationError, CatalogScanError, AnalysisCancelled
from .excelio import LABELS, reason_text
from .pipeline import analyze
from . import web_services as services
from . import __version__
from .model_diagnostics import discover_model
from .oracle_diagnostics import diagnose_selected_schemas
from .deployment import login_settings, LLM_BASE_URL, EMBEDDING_BASE_URL


def create_app(runtime_dir=None, *, analyzer=None):
    app=Flask(__name__)
    app.config.update(SECRET_KEY=secrets.token_hex(32), MAX_CONTENT_LENGTH=12*1024*1024,
                      MAX_FORM_MEMORY_SIZE=100000, MAX_FORM_PARTS=10,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      TRUSTED_HOSTS=["localhost","127.0.0.1"])
    root=Path(runtime_dir or "runtime/web").resolve()
    root.mkdir(parents=True,exist_ok=True)
    uploads,jobs,connections={},{},{}
    guard=threading.Lock()
    executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix="riskmatch")
    app.extensions["riskmatch"]={"executor":executor,"jobs":jobs,"uploads":uploads,"connections":connections}
    run=analyzer or analyze

    @app.before_request
    def protect():
        # Credentials stay in process memory, never in the signed browser cookie.
        with guard:
            for owner in list(connections):
                if time.monotonic()-connections[owner]["touched"]>3600:
                    connections.pop(owner)["settings"]["oracle"].clear()
        if request.method not in ("GET","HEAD","OPTIONS"):
            origin=request.headers.get("Origin")
            if origin and origin!=request.host_url.rstrip("/"):
                return jsonify(error="Bu sayfayı uygulamanın kendi adresinden açın."),403
            if not session.get("csrf") or not secrets.compare_digest(request.headers.get("X-CSRF-Token",""),session["csrf"]):
                return jsonify(error="Oturum doğrulanamadı. Sayfayı yenileyin."),403

    @app.after_request
    def headers(response):
        response.headers.update({"Cache-Control":"no-store", "X-Content-Type-Options":"nosniff",
            "Referrer-Policy":"no-referrer", "X-Frame-Options":"DENY",
            "Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.errorhandler(Exception)
    def error_handler(error):
        if isinstance(error,(ConfigError,InputError,IntegrationError)):
            return jsonify(error=str(error)),400
        if isinstance(error,HTTPException):
            message="Dosya en fazla 12 MB olabilir." if error.code==413 else "İstek veya adres geçersiz."
            return jsonify(error=message),error.code
        # Raw driver / network exceptions can contain passwords or private URLs.
        return jsonify(error="İşlem tamamlanamadı. Bağlantı ayarlarını ve dosyanızı kontrol edin."),500

    def body():
        if request.content_length and request.content_length>1000000:raise InputError("Ayar isteği çok büyük.")
        value=request.get_json()
        if not isinstance(value,dict):raise InputError("İstek nesne olmalı.")
        return value

    def owned(items,identity):
        value=items.get(identity)
        if not value or value["owner"]!=session.get("owner"):
            from flask import abort
            abort(404)
        return value

    @app.get("/")
    def home():
        session.setdefault("owner",secrets.token_hex(24))
        session.setdefault("csrf",secrets.token_hex(32))
        return render_template("index.html",csrf=session["csrf"],llm_base=LLM_BASE_URL,embedding_base=EMBEDDING_BASE_URL,app_version=__version__)

    @app.post("/api/connect")
    def connect():
        data=body()
        with guard:
            previous=connections.pop(session["owner"],None)
            if previous:previous["settings"]["oracle"].clear()
        settings=login_settings(data.get("user"),data.get("password"))
        result=services.probe_oracle(settings)
        if not result.get("ok"):
            settings["oracle"].clear()
            return jsonify(result)
        settings["oracle"].update(container=result["container"])
        with guard:
            if len(connections)>=30:raise InputError("Açık oturum sınırına ulaşıldı.")
            connections[session["owner"]]={"settings":settings,"scope":result,"touched":time.monotonic()}
        return jsonify(result)

    @app.get("/api/connection")
    def connection_status():
        with guard:
            current=connections.get(session.get("owner"))
            return jsonify(current["scope"] if current else {"ok":False})

    @app.post("/api/disconnect")
    def disconnect():
        with guard:
            current=connections.pop(session["owner"],None)
            if current:current["settings"]["oracle"].clear()
        return jsonify(ok=True)

    def run_settings(data):
        with guard:
            current=connections.get(session.get("owner"))
            if not current:raise ConfigError("Önce EDWLIVE kullanıcı adı ve parolanızla bağlanın. Oturum 1 saat kullanılmadığında kapanır.")
            schemas=data.get("schemas")
            if (not isinstance(schemas,list) or not 1<=len(schemas)<=30 or
                any(not isinstance(name,str) or name not in current["scope"]["schemas"] for name in schemas)):
                raise ConfigError("Açılır listeden taranacak 1–30 şemayı seçin.")
            settings=deepcopy(current["settings"])
            settings["oracle"]["schemas"]=list(dict.fromkeys(schemas))
            current["touched"]=time.monotonic()
        # The browser can configure optional model contracts, never the DB address/context.
        models=data.get("settings",{})
        if not isinstance(models,dict):raise ConfigError("Model ayarları geçersiz.")
        settings.update({key:models[key] for key in ("use_models","llm","embedding","search_mode","llm_review") if key in models})
        return services.build_run(settings)

    @app.post("/api/profile")
    def profile():return jsonify(services.clean_profile(body()))

    @app.post("/api/probe/<name>")
    def probe(name):
        data=body()
        if name=="oracle":return jsonify(services.probe_oracle(data))
        if name in ("llm","embedding"):return jsonify(discover_model(data,name))
        raise InputError("Bilinmeyen bağlantı türü.")

    @app.post("/api/diagnostics/oracle-schemas")
    def schema_diagnostics():
        data=body()
        config,credentials=run_settings({"schemas":data.get("schemas")})
        try:
            return jsonify(diagnose_selected_schemas(config["oracle"],credentials["oracle"],config["oracle"]["schemas"]))
        finally:
            credentials["oracle"].clear()

    @app.post("/api/models/<name>")
    def models(name):
        if name not in ("llm","embedding"):raise InputError("Bilinmeyen model türü.")
        return jsonify(services.list_models(body(),name))

    @app.post("/api/upload")
    def upload():
        file=request.files.get("file")
        if not file or not file.filename.lower().endswith(".xlsx"):raise InputError(".xlsx uzantılı Excel dosyası seçin.")
        with guard:
            if len(uploads)>=30:raise InputError("Bu oturumdaki dosya sınırına ulaşıldı. Uygulamayı yeniden başlatın.")
            identity=secrets.token_hex(16)
            path=root/"uploads"/(identity+".xlsx")
            path.parent.mkdir(exist_ok=True)
            file.save(path)
            try:sheets=services.inspect_workbook(path)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            uploads[identity]={"owner":session["owner"],"path":path,"filename":Path(file.filename).name}
        return jsonify(id=identity,filename=Path(file.filename).name,sheets=sheets)

    def worker(identity,path,mapping,config,credentials,requests):
        job=jobs[identity]
        request_map={r["row_id"]:r for r in requests}
        def progress(value):
            with guard:
                job["progress"]=value
                job["updated"]=time.time()
        def annotate(source):
            row=deepcopy(source)
            original=request_map[row["row_id"]]
            row["request"]={k:original.get(k,"") for k in ("name","description","category","data_type","reported","sheet","excel_row")}
            row["labels"]={k:LABELS.get(row[k],row[k]) for k in ("physical_name","semantic_status","production_status")}
            row["reason_texts"]=[reason_text(v) for v in row["reasons"]]
            for candidate in row["candidates"]:
                candidate["label"]=LABELS.get(candidate["status"],candidate["status"])
                candidate["reason_texts"]=[reason_text(v) for v in candidate.get("reasons",[])]
            return row
        def result_callback(row):
            prepared=annotate(row)
            with guard:
                # A final dependency pass may replace an earlier row without adding duplicates.
                index=job["row_positions"].get(prepared["row_id"])
                if index is None:
                    job["row_positions"][prepared["row_id"]]=len(job["partial_results"])
                    job["partial_results"].append(prepared)
                else:job["partial_results"][index]=prepared
                job["updated"]=time.time()
        try:
            result=run(path,root/identity/"sonuclar.xlsx",mode="real",config_data=config,
                       credentials=credentials,column_mapping=mapping,state_dir=root/identity/"state",progress=progress,
                       result_callback=result_callback,cancelled=job["cancel_event"].is_set)
            result["results"]=[annotate(row) for row in result["results"]]
            with guard:
                job.update(status="cancelled" if job["cancel_event"].is_set() else "complete",result=result,
                           finished=time.time(),updated=time.time())
        except AnalysisCancelled:
            with guard:
                job.update(status="cancelled",finished=time.time(),updated=time.time())
        except CatalogScanError as error:
            source="Excel okundu" if job["input_type"]=="excel" else "Girdi alındı"
            with guard:
                job.update(status="failed",failure_stage="catalog",code=error.code,
                           error=source+"; "+str(len(requests))+" değişken hazır. Katalog okunamadığı için arama başlatılamadı. "+str(error),
                           catalog_diagnostics=error.diagnostics,
                           progress={"stage":"Oracle kataloğu okunamadı","phase":"catalog","completed":0,"total":len(requests)},
                           finished=time.time())
        except Exception:
            with guard:job.update(status="failed",error="Analiz tamamlanamadı. Dosya erişimini ve bağlantı ayarlarını kontrol edin.",finished=time.time())
        finally:
            for value in credentials.values():
                if isinstance(value,dict):value.clear()
            credentials.clear()

    @app.post("/api/analyze")
    def start():
        data=body()
        manual=data.get("input_type")=="manual"
        upload=None if manual else owned(uploads,data.get("upload_id"))
        config,credentials=run_settings(data)
        identity=secrets.token_hex(16)
        if manual:
            path=root/identity/"ekrandan-eklenenler.xlsx"
            mapping=services.write_manual_request(path,data.get("variables"))
            filename="Ekrandan eklenen değişkenler"
        else:
            path=upload["path"]
            mapping=data.get("mapping")
            if mapping is None:mapping=services.automatic_mapping(path)
            filename=upload["filename"]
        requests=services.validate_mapping(path,mapping)
        with guard:
            if any(j["status"] in ("running","cancelling") for j in jobs.values()):
                return jsonify(error="Bir analiz devam ediyor. Tamamlandıktan sonra yeniden deneyin."),409
            if len(jobs)>=20:raise InputError("Analiz sınırına ulaşıldı. Uygulamayı yeniden başlatın.")
            jobs[identity]={"id":identity,"owner":session["owner"],"status":"running",
                "filename":filename,"input_type":"manual" if manual else "excel",
                "created":time.time(),"updated":time.time(),"cancel_event":threading.Event(),
                "partial_results":[],"row_positions":{},"search_mode":config.get("retrieval",{}).get("search_mode","fast"),
                "progress":{"stage":"Analiz hazırlanıyor","completed":0,"total":len(requests)}}
        executor.submit(worker,identity,path,mapping,config,credentials,requests)
        return jsonify(id=identity),202

    @app.get("/api/jobs/<identity>")
    def status(identity):
        with guard:
            job=owned(jobs,identity)
            data={k:v for k,v in job.items() if k not in ("owner","result","cancel_event","partial_results","row_positions")}
            data["results"]=deepcopy(job["partial_results"])
            now=time.time()
            data["elapsed_seconds"]=round(max(0,job.get("finished",now)-job["created"]),1)
            data["last_update_seconds"]=round(max(0,now-job["updated"]),1)
            if job.get("result"):
                data.update(results=job["result"]["results"],run_info=job["result"]["run_info"])
        return jsonify(data)

    @app.post("/api/jobs/<identity>/cancel")
    def cancel(identity):
        with guard:
            job=owned(jobs,identity)
            if job["status"] in ("running","cancelling"):
                job["cancel_event"].set()
                job["status"]="cancelling"
                job["updated"]=time.time()
        return jsonify(id=identity,status=job["status"])

    @app.get("/api/jobs/<identity>/download")
    def download(identity):
        job=owned(jobs,identity)
        if job["status"]!="complete":raise InputError("Sonuç henüz hazır değil.")
        return send_file(root/identity/"sonuclar.xlsx",as_attachment=True,download_name="degisken-inceleme-sonuclari.xlsx")

    return app


def serve(port=8765,open_browser=True):
    import webbrowser
    from waitress import create_server
    app=create_app()
    try:
        server=create_server(app,host="127.0.0.1",port=port,threads=6,max_request_body_size=12*1024*1024)
    except OSError:
        raise ConfigError("Web portu kullanılıyor veya erişilemiyor. Açık uygulamayı kontrol edin ya da serve --port 8766 kullanın.") from None
    url="http://127.0.0.1:"+str(port)
    print("Risk Feature Workbench: "+url+"\nKapatmak için Ctrl+C. Kurum bağlantısı yalnız ekrandan başlatılır.",flush=True)
    if open_browser:webbrowser.open(url)
    try:server.run()
    except KeyboardInterrupt:pass
    finally:
        server.close()
        app.extensions["riskmatch"]["executor"].shutdown(wait=True)
