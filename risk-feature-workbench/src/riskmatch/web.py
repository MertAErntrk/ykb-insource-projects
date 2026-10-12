"""Loopback-only web workspace. Credentials are passed in memory, never stored."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import re
import secrets
import shutil
import threading
import time

from flask import Flask, abort, jsonify, render_template, request, send_file, session
from werkzeug.exceptions import HTTPException

from .config import DEFAULT_SCHEMA_TIERS
from .derivation import VERDICT_LABELS
from .errors import ConfigError, InputError, IntegrationError, CatalogScanError, AnalysisCancelled, ReportWriteError
from .excelio import LABELS, ROW_STATUS_LABELS, NAME_MATCH_LABELS, MEANING_LABELS, SCOPE_LABELS, reason_text, write_results, verdict_source_text, derivation_method_text, RELATION_LABELS, component_state_text
from .semantics import _window_text
from .pipeline import analyze, MODEL_CHANGED_REASON, INDEX_POLL_SECONDS, _queue_counts, _wait_stage
from . import web_services as services
from . import __version__
from .model_diagnostics import discover_model
from .oracle_diagnostics import diagnose_selected_schemas
from .deployment import login_settings, LLM_BASE_URL, EMBEDDING_BASE_URL

ACTIVE=("running","cancelling","reporting")
JOB_PRIVATE=("owner","cancel_event","partial_results","row_positions","report_lock","run_info_base",
             "mapping","requests","hold","groups_complete","raw","generation")
JOB_FOLDER=re.compile("[0-9a-f]{32}")


def default_resources(root_dir):
    # Imported lazily: shared catalog/index resources are optional for the web shell.
    try:from .resources import Resources
    except ImportError:return None
    return Resources(root_dir)


def annotate(source,original):
    row=deepcopy(source)
    row["request"]={k:original.get(k,"") for k in ("name","description","category","data_type","reported","sheet","excel_row")}
    row["labels"]={k:LABELS.get(row.get(k),row.get(k)) for k in ("physical_name","semantic_status","production_status")}
    row["reason_texts"]=[reason_text(v) for v in row.get("reasons",[])]
    row["row_status_label"]=ROW_STATUS_LABELS.get(row.get("row_status"),row.get("row_status") or "")
    row["verdict"]=row.get("verdict") or None
    row["verdict_label"]=VERDICT_LABELS.get(row["verdict"],"") if row["verdict"] else ""
    row["verdict_reasons"]=row.get("verdict_reasons") or []
    row["derivation"]=row.get("derivation") or None
    row["tier_trace"]=row.get("tier_trace") or []
    row["verdict_source"]=verdict_source_text(row)
    row["derivation_method"]=derivation_method_text(row)
    if row["derivation"]:
        row["derivation"]["relationship_label"]=RELATION_LABELS.get(row["derivation"].get("relationship"),"")
        for c in row["derivation"].get("components") or []:
            c["state_label"]=component_state_text(c)
            c["window_label"]=_window_text(c["window"]) if c.get("window") else ""
    for step in row["tier_trace"]:
        step["row_status_label"]=ROW_STATUS_LABELS.get(step.get("row_status"),"")
        step["meaning_label"]=MEANING_LABELS.get(step.get("meaning"),"")
        step["scope_label"]=SCOPE_LABELS.get(step.get("semantic_scope"),"")
    for candidate in row.get("candidates",[]):
        candidate["label"]=LABELS.get(candidate.get("status"),candidate.get("status"))
        candidate["reason_texts"]=[reason_text(v) for v in candidate.get("reasons",[])]
        candidate["name_match_label"]=NAME_MATCH_LABELS.get(candidate.get("name_match"),candidate.get("name_match") or "")
        candidate["meaning_label"]=MEANING_LABELS.get(candidate.get("meaning"),candidate.get("meaning") or "")
    return row


def connection_scope(result,fixed):
    # Taranan şemalar sabittir (katman sırası: DM_CAD, DM, EDW); kullanıcı seçmez, erişilemeyenler bildirilir.
    value=dict(result)
    # Probe'un "şema seçtikten sonra" uyarısı artık geçersiz; yalnız eksik katman uyarısı gösterilir.
    value.pop("warning",None)
    available=set(value.get("schemas") or [])
    value["scan_schemas"]=[n for n in fixed if n in available]
    value["missing_schemas"]=[n for n in fixed if n not in available]
    missing=value["missing_schemas"]
    if not value["scan_schemas"]:
        return {"ok":False,"scan_schemas":[],"missing_schemas":missing,
                "error":"Taranacak şemaların hiçbiri ("+", ".join(fixed)+") bu kullanıcıyla erişilebilir listede yok. "
                        "Kullanıcı yetkilerini kontrol edin."}
    if len(missing)==1:
        value["warning"]=(missing[0]+" şeması bu kullanıcıyla erişilebilir listede yok; bu katmanda arama yapılamaz, "
                          "ilgili satırlar 'karar verilemedi' olur.")
    elif missing:
        value["warning"]=(", ".join(missing)+" şemaları bu kullanıcıyla erişilebilir listede yok; bu katmanlarda arama "
                          "yapılamaz, ilgili satırlar 'karar verilemedi' olur.")
    return value


def create_app(runtime_dir=None, *, analyzer=None, resources_factory=None, scan_schemas=None):
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
    starting=set()
    app.extensions["riskmatch"]={"executor":executor,"jobs":jobs,"uploads":uploads,"connections":connections,"guard":guard}
    run=analyzer or analyze
    make_resources=resources_factory or default_resources
    fixed=list(dict.fromkeys(scan_schemas if scan_schemas is not None
                             else [n for tier in DEFAULT_SCHEMA_TIERS for n in tier["schemas"]]))

    def sweep():
        # Finished job folders older than a day are removed; uploads, shared caches and running jobs stay.
        limit=time.time()-86400
        with guard:active={k for k,j in jobs.items() if j["status"] in ACTIVE}
        for folder in root.iterdir():
            try:
                if (folder.is_dir() and JOB_FOLDER.fullmatch(folder.name) and folder.name not in active
                        and folder.stat().st_mtime<limit):
                    shutil.rmtree(folder,ignore_errors=True)
                    with guard:jobs.pop(folder.name,None)
            except OSError:pass
    sweep()

    def release(current):
        # Callers hold guard and pass the result to shut(); resources used by a running job close when it ends.
        current["settings"]["oracle"].clear()
        if current.get("jobs"):current["closing"]=True
        return [(key,current.pop(key)) for key in ("indexer","resources")
                if current.get(key) is not None and not (key=="resources" and current.get("jobs"))]

    def shut(items):
        # Called outside guard: stopping an indexer waits for its thread.
        for key,value in items:
            try:value.stop() if key=="indexer" else value.close()
            except Exception:pass

    def hold(owner):
        # Callers hold guard; a job keeps one Resources for all groups, even across disconnect/connect.
        current=connections.get(owner)
        if not current:return None
        if current.get("resources") is None:current["resources"]=make_resources(root)
        current["jobs"]=current.get("jobs",0)+1
        return current

    def unhold(current):
        if current is None:return
        with guard:
            current["jobs"]-=1
            items=[] if current["jobs"] or not current.get("closing") else release(current)
        shut(items)

    def wipe(credentials):
        for value in credentials.values():
            if isinstance(value,dict):value.clear()
        credentials.clear()

    @app.before_request
    def protect():
        # Credentials stay in process memory, never in the signed browser cookie.
        items=[]
        with guard:
            for owner in list(connections):
                if time.monotonic()-connections[owner]["touched"]>3600:
                    items+=release(connections.pop(owner))
        shut(items)
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
        if not value or value["owner"]!=session.get("owner"):abort(404)
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
            items=release(previous) if previous else []
        shut(items)
        settings=login_settings(data.get("user"),data.get("password"))
        result=services.probe_oracle(settings)
        if not result.get("ok"):
            settings["oracle"].clear()
            return jsonify(result)
        scope=connection_scope(result,fixed)
        if not scope.get("ok"):
            # Hiçbir sabit şemaya erişim yoksa bağlantı saklanmaz.
            settings["oracle"].clear()
            return jsonify(scope)
        settings["oracle"].update(container=result["container"])
        with guard:
            if len(connections)>=30:raise InputError("Açık oturum sınırına ulaşıldı.")
            connections[session["owner"]]={"settings":settings,"scope":scope,"scan_schemas":list(scope["scan_schemas"]),
                                           "touched":time.monotonic()}
        return jsonify(scope)

    @app.get("/api/connection")
    def connection_status():
        with guard:
            current=connections.get(session.get("owner"))
            return jsonify(current["scope"] if current else {"ok":False})

    @app.post("/api/disconnect")
    def disconnect():
        with guard:
            current=connections.pop(session["owner"],None)
            items=release(current) if current else []
        shut(items)
        return jsonify(ok=True)

    def run_settings(data):
        with guard:
            current=connections.get(session.get("owner"))
            if not current:raise ConfigError("Önce EDWLIVE kullanıcı adı ve parolanızla bağlanın. Oturum 1 saat kullanılmadığında kapanır.")
            # İstemcinin gönderdiği "schemas" yok sayılır; bağlantıda belirlenen sabit şemalar taranır.
            settings=deepcopy(current["settings"])
            settings["oracle"]["schemas"]=list(current["scan_schemas"])
            current["touched"]=time.monotonic()
        # The browser can configure optional model contracts, never the DB address/context.
        models=data.get("settings",{})
        if not isinstance(models,dict):raise ConfigError("Model ayarları geçersiz.")
        settings.update({key:models[key] for key in ("use_models","llm","embedding","search_mode","llm_review") if key in models})
        return services.build_run(settings)

    def owner_resources(owner):
        with guard:
            current=connections.get(owner)
            if not current:return None
            if current.get("resources") is None:current["resources"]=make_resources(root)
            return current["resources"]

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
        config,credentials=run_settings({})
        try:
            return jsonify(diagnose_selected_schemas(config["oracle"],credentials["oracle"],config["oracle"]["schemas"]))
        finally:
            credentials["oracle"].clear()

    @app.post("/api/models/<name>")
    def models(name):
        if name not in ("llm","embedding"):raise InputError("Bilinmeyen model türü.")
        return jsonify(services.list_models(body(),name))

    def workspace(identity,owner,path,filename,mapping=None,requests=None):
        return {"id":identity,"owner":owner,"path":path,"filename":filename,"mapping":mapping,
                "requests":requests,"results":{},"raw":{},"generation":0,"created":time.time()}

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
            uploads[identity]=workspace(identity,session["owner"],path,Path(file.filename).name)
        return jsonify(id=identity,filename=Path(file.filename).name,sheets=sheets)

    def report(job):
        # One writer per job; the workbook shows this job's rows plus rows searched earlier under the same
        # column mapping. Rows from an older or newer mapping never mix into this job's report.
        space=uploads[job["upload_id"]]
        with job["report_lock"]:
            with guard:
                found={**job["raw"],**space["raw"]} if space["generation"]==job["generation"] else job["raw"]
                raw=[found[r["row_id"]] for r in job["requests"] if r["row_id"] in found]
                own=sum(r["row_id"] in job["raw"] for r in job["requests"])
                # Completion is the job's, not the last group's: stopped, failed or partial work is incomplete.
                complete=(job["status"] not in ("cancelled","cancelling","failed") and job["groups_complete"]
                          and own==job["progress"].get("total"))
                info={**job["run_info_base"],"Tamamlanma":"Tamamlandı" if complete else "Eksik",
                      "Sonuç dağılımı":dict(Counter(r.get("row_status","not_evaluated") for r in job["raw"].values())),
                      "Aranan satır":len(raw),"Bu işte aranan":own}
                job["run_info"]=info
            try:
                write_results(space["path"],root/job["id"]/"sonuclar.xlsx",job["requests"],raw,info)
                error=None
            except ReportWriteError as failure:
                error={"kind":failure.kind,"message":str(failure)}
            except Exception:
                error={"kind":"other","message":ReportWriteError.MESSAGES["other"]}
            with guard:
                job["report_error"]=error
                if error is None:job["report_ready"]=True
                job["updated"]=time.time()
            return error

    def model_check(info):
        value=info.get("Model doğrulaması")
        if not isinstance(value,dict):return None
        return value.get("end_of_job_check") or (value.get("embedding") or {}).get("end_of_job_check")

    def worker(identity,config,credentials,groups):
        job=jobs[identity]
        space=uploads[job["upload_id"]]
        # Mapping, requests and Resources are fixed at job start; groups never re-read the workspace.
        request_map={r["row_id"]:r for r in job["requests"]}
        mapping=job["mapping"]
        resources=job["hold"]["resources"] if job["hold"] else None
        total=sum(len(g) for g in groups)
        # Model açıkken tarama yalnız indeks %100 tamamlandıktan sonra yapılır.
        full_index=uses_full_index(config)
        offset=[0,0]
        merged={"errors":[],"warnings":[],"changed":False}
        def merge(info):
            info=dict(info or {})
            with guard:
                merged["errors"]=list(dict.fromkeys(merged["errors"]+list(info.get("Hatalar") or [])))
                merged["warnings"]=list(dict.fromkeys(merged["warnings"]+list(info.get("Uyarılar") or [])))
                if info.get("Tamamlanma")!="Tamamlandı":job["groups_complete"]=False
                if model_check(info)=="changed":merged["changed"]=True;job["groups_complete"]=False
                base={**info,"Hatalar":merged["errors"],"Uyarılar":merged["warnings"]}
                checked=deepcopy(base.get("Model doğrulaması"))
                if merged["changed"] and isinstance(checked,dict):
                    # The job-level check is "changed" if any group saw a change, not just the last one.
                    target=checked["embedding"] if isinstance(checked.get("embedding"),dict) else checked
                    target["end_of_job_check"]="changed"
                    base["Model doğrulaması"]=checked
                job["run_info_base"]=base
        def mark_changed():
            # A model change seen in any group invalidates the semantic result of every row in this job.
            if not merged["changed"]:return
            with guard:
                current=space["generation"]==job["generation"]
                for index,prepared in enumerate(job["partial_results"]):
                    row=job["raw"].get(prepared["row_id"])
                    if row is None or (row.get("model_changed") and MODEL_CHANGED_REASON in row.get("reasons",[])):continue
                    row["model_changed"]=True
                    row["reasons"]=list(row.get("reasons") or [])+[MODEL_CHANGED_REASON]
                    job["partial_results"][index]=annotate(row,request_map[row["row_id"]])
                    if current:
                        space["results"][row["row_id"]]=job["partial_results"][index]
                        space["raw"][row["row_id"]]=deepcopy(row)
                job["updated"]=time.time()
        def settle():
            try:mark_changed()
            except Exception:pass
        def progress(value):
            with guard:
                job["progress"]={**value,"completed":offset[0]+value.get("completed",0),"total":total,
                                 "group":offset[1]+1,"groups":len(groups)}
                job["updated"]=time.time()
        def store(row):
            prepared=annotate(row,request_map[row["row_id"]])
            with guard:
                # A final dependency pass may replace an earlier row without adding duplicates.
                index=job["row_positions"].get(prepared["row_id"])
                if index is None:
                    job["row_positions"][prepared["row_id"]]=len(job["partial_results"])
                    job["partial_results"].append(prepared)
                else:job["partial_results"][index]=prepared
                job["raw"][row["row_id"]]=deepcopy(row)
                if space["generation"]==job["generation"]:
                    space["results"][row["row_id"]]=prepared
                    space["raw"][row["row_id"]]=deepcopy(row)
                job["updated"]=time.time()
        try:
            for number,group in enumerate(groups):
                if job["cancel_event"].is_set():raise AnalysisCancelled("Analiz kullanıcı tarafından durduruldu.")
                offset[1]=number
                result=run(space["path"],root/identity/"sonuclar.xlsx",mode="real",config_data=config,
                           credentials=credentials,column_mapping=deepcopy(mapping),state_dir=root/identity/"state",progress=progress,
                           result_callback=store,cancelled=job["cancel_event"].is_set,row_ids=group,
                           resources=resources,write_report=False,index_epoch=identity,require_full_index=full_index)
                for row in result.get("results",[]):store(row)
                offset[0]+=len(group)
                merge(result.get("run_info"))
                mark_changed()
            with guard:
                job["progress"]={**job["progress"],"completed":total,"total":total}
                job.update(status="reporting",updated=time.time())
            error=report(job)
            with guard:
                job.update(status="report_failed" if error else "complete",finished=time.time(),updated=time.time())
                if error:job.update(error_kind=error["kind"],error=error["message"])
        except AnalysisCancelled as error:
            for row in (getattr(error,"partial",None) or {}).get("results",[]):store(row)
            settle()
            with guard:
                job.update(status="cancelled",finished=time.time(),updated=time.time())
        except CatalogScanError as error:
            source="Excel okundu" if job["input_type"]=="excel" else "Girdi alındı"
            settle()
            with guard:
                job.update(status="failed",failure_stage="catalog",code=error.code,
                           error=source+"; "+str(total)+" değişken hazır. Katalog okunamadığı için arama başlatılamadı. "+str(error),
                           catalog_diagnostics=error.diagnostics,
                           progress={"stage":"Oracle kataloğu okunamadı","phase":"catalog","completed":offset[0],"total":total},
                           finished=time.time())
        except Exception:
            # Rows already searched stay visible and can still be written as an incomplete report.
            settle()
            with guard:job.update(status="failed",error="Analiz tamamlanamadı. Dosya erişimini ve bağlantı ayarlarını kontrol edin.",finished=time.time())
        finally:
            wipe(credentials)
            unhold(job["hold"])

    def selection(data,requests):
        size=data.get("group_size",25)
        if isinstance(size,bool) or not isinstance(size,int) or not 1<=size<=100:raise InputError("Grup boyutu 1–100 olmalı.")
        known=[r["row_id"] for r in requests]
        wanted=data.get("row_ids")
        if wanted is None:wanted=known
        elif not isinstance(wanted,list) or any(not isinstance(v,str) for v in wanted):raise InputError("Seçilen satırlar geçersiz.")
        wanted=list(dict.fromkeys(wanted))
        if not wanted:raise InputError("Aranacak en az bir satır seçin.")
        if set(wanted)-set(known):raise InputError("Seçilen satırlardan bazıları bu dosyada yok. Listeyi yenileyip yeniden seçin.")
        order={v:i for i,v in enumerate(known)}
        wanted.sort(key=order.get)
        return [wanted[i:i+size] for i in range(0,len(wanted),size)]

    @app.post("/api/analyze")
    def start():
        data=body()
        manual=data.get("input_type")=="manual"
        space=None if manual else owned(uploads,data.get("upload_id"))
        owner=session["owner"]
        # Checked and reserved before the workspace or any manual input file is touched.
        with guard:
            if owner in starting or any(j["owner"]==owner and j["status"] in ACTIVE for j in jobs.values()):
                return jsonify(error="Bir analiz devam ediyor. Tamamlandıktan sonra yeniden deneyin."),409
            starting.add(owner)
        try:
            config,credentials=run_settings(data)
            try:
                identity=secrets.token_hex(16)
                if manual:
                    with guard:
                        if len(uploads)>=30:raise InputError("Bu oturumdaki dosya sınırına ulaşıldı. Uygulamayı yeniden başlatın.")
                    key=secrets.token_hex(16)
                    path=root/"uploads"/key/"ekrandan-eklenenler.xlsx"
                    mapping=services.write_manual_request(path,data.get("variables"))
                    space=workspace(key,owner,path,"Ekrandan eklenen değişkenler",mapping,services.validate_mapping(path,mapping))
                else:
                    mapping=data.get("mapping")
                    if mapping is None:mapping=space["mapping"] or services.automatic_mapping(space["path"])
                    if space["requests"] is None or mapping!=space["mapping"]:
                        # A new column mapping changes row identities, so earlier results no longer apply.
                        requests=services.validate_mapping(space["path"],mapping)
                        with guard:space.update(mapping=mapping,requests=requests,results={},raw={},generation=space["generation"]+1)
                groups=selection(data,space["requests"])
                sweep()
                with guard:
                    if len(jobs)>=20:
                        done=sorted((j for j in jobs.values() if j["status"] not in ACTIVE),key=lambda j:j["created"])
                        if not done:raise InputError("Analiz sınırına ulaşıldı. Çalışan işlerin bitmesini bekleyin.")
                        jobs.pop(done[0]["id"])
                        shutil.rmtree(root/done[0]["id"],ignore_errors=True)
                    if manual:uploads[space["id"]]=space
                    total=sum(len(g) for g in groups)
                    jobs[identity]={"id":identity,"owner":owner,"status":"running","upload_id":space["id"],
                        "filename":space["filename"],"input_type":"manual" if manual else "excel",
                        "created":time.time(),"updated":time.time(),"cancel_event":threading.Event(),"report_lock":threading.Lock(),
                        "partial_results":[],"row_positions":{},"run_info_base":{},"report_ready":False,"report_error":None,
                        "mapping":deepcopy(space["mapping"]),"requests":space["requests"],"hold":hold(owner),"groups_complete":True,
                        "raw":{},"generation":space["generation"],
                        "search_mode":config.get("retrieval",{}).get("search_mode","fast"),
                        "progress":{"stage":"Analiz hazırlanıyor","completed":0,"total":total,"group":1,"groups":len(groups)}}
            except BaseException:
                wipe(credentials)
                raise
        finally:
            with guard:starting.discard(owner)
        if uses_full_index(config):
            threading.Thread(target=submit_after_index,args=(identity,config,credentials,groups),
                             name="riskmatch-index-wait",daemon=True).start()
        else:executor.submit(worker,identity,config,credentials,groups)
        return jsonify(id=identity,upload_id=space["id"]),202

    def uses_full_index(config):
        return bool(config.get("use_models") and "embedding" in config)

    def submit_after_index(identity,config,credentials,groups):
        # Bağlantının indeksi çalışırken iş tek analiz yuvasını tutmaz; indeks bitince sıraya girer.
        # Kesin kapı pipeline içindedir: indeks durmuş ya da eksikse orada yeniden başlatılır.
        job=jobs[identity]
        try:
            documents=None
            while not job["cancel_event"].is_set():
                with guard:
                    current=connections.get(job["owner"])
                    indexer=current.get("indexer") if current else None
                status=indexer.status() if indexer is not None else {}
                if status.get("state")!="running":break
                if documents is None:documents=_queue_counts(indexer.index)[1]
                with guard:
                    job["progress"]={**job["progress"],"stage":_wait_stage(status,documents),"phase":"index_wait"}
                    job["updated"]=time.time()
                job["cancel_event"].wait(INDEX_POLL_SECONDS)
        except Exception:
            pass
        try:executor.submit(worker,identity,config,credentials,groups)
        except RuntimeError:
            # Uygulama kapanıyor: iş başlatılamaz.
            wipe(credentials);unhold(job["hold"])
            with guard:job.update(status="failed",error="Uygulama kapanırken analiz başlatılamadı.",finished=time.time())

    @app.get("/api/jobs/latest")
    def latest():
        with guard:
            mine=[j for j in jobs.values() if j["owner"]==session.get("owner")]
            if not mine:return jsonify({})
            job=max(mine,key=lambda j:j["created"])
            return jsonify(id=job["id"],status=job["status"],upload_id=job["upload_id"])

    @app.get("/api/jobs/<identity>")
    def status(identity):
        since=request.args.get("since","0")
        since=int(since) if since.isdigit() else 0
        with guard:
            job=owned(jobs,identity)
            data={k:deepcopy(v) for k,v in job.items() if k not in JOB_PRIVATE}
            data["results"]=deepcopy(job["partial_results"][since:])
            data["result_count"]=len(job["partial_results"])
            now=time.time()
            data["elapsed_seconds"]=round(max(0,job.get("finished",now)-job["created"]),1)
            data["last_update_seconds"]=round(max(0,now-job["updated"]),1)
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

    @app.post("/api/jobs/<identity>/report")
    def rewrite(identity):
        with guard:
            job=owned(jobs,identity)
            # A failed job that already searched rows can still be written as an incomplete report.
            if not (job["status"] in ("complete","report_failed","cancelled") or
                    (job["status"]=="failed" and job["partial_results"])):raise InputError("Rapor bu iş durumunda yazılamaz.")
        error=report(job)
        with guard:
            if not error and job["status"]=="report_failed":
                job["status"]="complete"
                job.pop("error_kind",None);job.pop("error",None)
            return jsonify(id=identity,status=job["status"],report_ready=job["report_ready"],report_error=error)

    @app.get("/api/jobs/<identity>/download")
    def download(identity):
        job=owned(jobs,identity)
        if not job.get("report_ready"):raise InputError("Rapor henüz hazır değil.")
        return send_file(root/identity/"sonuclar.xlsx",as_attachment=True,download_name="degisken-inceleme-sonuclari.xlsx")

    @app.get("/api/uploads/<identity>/rows")
    def rows(identity):
        space=owned(uploads,identity)
        if space["requests"] is None:
            mapping=space["mapping"] or services.automatic_mapping(space["path"])
            requests=services.validate_mapping(space["path"],mapping)
            with guard:space.update(mapping=mapping,requests=requests,generation=space["generation"]+1)
        with guard:
            requests=space["requests"]
            values=[]
            for r in requests:
                found=space["results"].get(r["row_id"])
                state=found.get("row_status") if found else "not_searched"
                values.append({"row_id":r["row_id"],"sheet":r.get("sheet"),"excel_row":r.get("excel_row"),
                               "name":r.get("name",""),"description":r.get("description",""),"category":r.get("category",""),
                               "searched":found is not None,"row_status":state,
                               "row_status_label":ROW_STATUS_LABELS.get(state,state or "")})
        return jsonify(rows=values,total=len(values),searched=sum(v["searched"] for v in values))

    def index_inputs(data):
        config,credentials=run_settings(data)
        resources=owner_resources(session["owner"])
        if resources is None:
            credentials.clear()
            raise IntegrationError("Katalog ve indeks hizmeti bu kurulumda kullanılamıyor.")
        from .adapters.oracle import OracleAdapter
        oracle=config["oracle"]
        # Same key and index settings as analyze(), so the indexer and later jobs share one cache entry.
        from .pipeline import catalog_key
        key=catalog_key(oracle,credentials["oracle"])
        return config,credentials,resources,OracleAdapter(oracle,credentials=credentials["oracle"]),key

    @app.post("/api/index/start")
    def index_start():
        data=body()
        models=data.get("settings",{})
        if not isinstance(models,dict) or not models.get("use_models") or not isinstance(models.get("embedding"),dict):
            raise InputError("İndeks için embedding servisini açın ve ayarlayın.")
        config,credentials,resources,adapter,key=index_inputs(data)
        try:
            if "embedding" not in config:raise InputError("İndeks için embedding servisini açın ve ayarlayın.")
            from .adapters.qwen import EmbeddingAdapter
            snapshot,_=resources.catalog(adapter,key)
            context=resources.embedding(EmbeddingAdapter(config["embedding"],api_key=credentials.get("embedding","")))
            from .config import load_config
            from .pipeline import index_settings
            indexer=resources.indexer(snapshot,context,index_settings(load_config(None,"real",data=config)))
        finally:
            credentials["oracle"].clear()
        with guard:
            current=connections.get(session["owner"])
            previous=current.pop("indexer",None) if current else None
            if current:current["indexer"]=indexer
        if current is None:
            # Disconnected while the indexer was prepared: its resources are closing, so it is never started.
            return jsonify(error="Bağlantı kapandı. Yeniden bağlanıp indekslemeyi başlatın."),409
        # Önbellekten aynı indeksleyici dönebilir; onu durdurmak çalışan kapıyı da keserdi.
        if previous is not None and previous is not indexer:
            try:previous.stop()
            except Exception:pass
        indexer.start()
        return jsonify(index_snapshot(indexer)),202

    def index_snapshot(indexer):
        value=dict(indexer.status())
        # Coverage of the whole catalog in the store, not just of this indexer run's missing list.
        try:value["index_coverage"]=round(indexer.index.stored_coverage(),4)
        except Exception:value["index_coverage"]=None
        value["complete"]=index_complete(indexer,value)
        return value

    def index_complete(indexer,value):
        # Kapıyla aynı ölçüt: tablo ve kolon belgelerinin hepsi depoda.
        if value.get("state")!="done":return False
        if hasattr(indexer.index,"queue_counts"):
            try:return indexer.index.queue_counts()[0]==0
            except Exception:return False
        return (value.get("index_coverage") or 0)>=1.0

    @app.get("/api/index/status")
    def index_status():
        with guard:
            current=connections.get(session.get("owner"))
            indexer=current.get("indexer") if current else None
        return jsonify(index_snapshot(indexer) if indexer is not None else {"state":"idle"})

    @app.post("/api/index/stop")
    def index_stop():
        with guard:
            current=connections.get(session.get("owner"))
            indexer=current.get("indexer") if current else None
        if indexer is None:return jsonify(state="idle")
        indexer.stop()
        return jsonify(index_snapshot(indexer))

    @app.post("/api/catalog/refresh")
    def catalog_refresh():
        data=body()
        config,credentials,resources,adapter,key=index_inputs({})
        try:snapshot,info=resources.catalog(adapter,key,refresh=True)
        finally:credentials["oracle"].clear()
        # Yeni katalog eski indeksleyicinin listesini geçersiz kılar; istemci indekslemeyi yeniden başlatır.
        with guard:
            current=connections.get(session["owner"])
            indexer=current.pop("indexer",None) if current else None
        if indexer is not None:
            try:indexer.stop()
            except Exception:pass
        source=info.get("source") if isinstance(info,dict) else info
        return jsonify(columns=len(snapshot.get("records",[])),source=source)

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
        # Close per-owner resources so background indexers stop and the SQLite vector store is released.
        for current in list(app.extensions["riskmatch"]["connections"].values()):
            try:current.get("resources") and current["resources"].close()
            except Exception:pass
