"""Kanıt kapısı, kontrollü arama genişletme ve satır bazında yeniden devam."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from . import __version__
from .config import load_config, signature_config
from .errors import ConfigError, IntegrationError, StaleIndexError, OracleOperationError
from .excelio import read_requests, write_results

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()

def atomic_json(path, value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+".writing")
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    temporary.replace(path)

def _source_version():
    root=Path(__file__).parent
    return digest({str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*.py"))})

def _physical(name,snapshot):
    if any(str(r["column"]).casefold()==name.casefold() for r in snapshot["records"]):
        return "present"
    return "absent_in_scope" if snapshot["complete"] else "unknown"

def _blank_result(req,snapshot):
    return {"row_id":req["row_id"],"physical_name":_physical(req["name"],snapshot),"semantic_status":"incomplete","production_status":"not_evaluated","reasons":[],"candidates":[],"dependencies":[],"errors":[],"expert_decision":""}

def _safe_error(error):
    # Never persist raw network, SQL, driver, credential or filesystem exception strings.
    if isinstance(error,OracleOperationError):
        return str(error)
    if isinstance(error,StaleIndexError):
        return "İndeks sürümü katalogla uyuşmuyor; yeniden indeksleme gerekli."
    if isinstance(error,ConfigError):
        return "Bağlantı yapılandırması geçersiz veya eksik. Ayarları kontrol edin."
    if isinstance(error,IntegrationError):
        return "Entegrasyon tamamlanamadı (erişim, süre aşımı veya geçersiz servis yanıtı)."
    return "Satır işlenirken teknik hata oluştu; ayrıntılar hassas veri koruması için kayda alınmadı."

def analyze(input_path,output_path,*,mode="demo",config_path=None,state_dir=None,resume=True,strategy="hybrid",config_data=None,credentials=None,column_mapping=None,progress=None):
    from .semantics import parse_definition,evaluate_candidate,build_dependencies,assess_production
    from .retrieval import HybridIndex
    from .demo import demo_snapshot,DemoEmbedding
    started=datetime.now(timezone.utc).isoformat()
    config=load_config(config_path,mode,**({"data":config_data} if config_data is not None else {}))
    requests=read_requests(input_path,column_mapping)
    def notify(stage,completed=0):
        if progress:progress({"stage":stage,"completed":completed,"total":len(requests)})
    notify("Oracle kataloğu okunuyor")
    for req in requests:
        req["definition"]=parse_definition(req["name"],req.get("description_for_parse",req["description"]),config["naming"])
    state_dir=Path(state_dir or Path(output_path).parent/"runtime")
    errors=[]
    llm=None
    if mode=="demo":
        snapshot=demo_snapshot()
        embedding=DemoEmbedding()
    else:
        from .adapters.oracle import OracleAdapter
        from .adapters.qwen import EmbeddingAdapter,LLMAdapter
        use_models=config.get("use_models",True)
        keys=credentials or {}
        embedding=EmbeddingAdapter(config["embedding"],**({"api_key":keys.get("embedding","")} if credentials is not None else {})) if use_models else None
        llm=LLMAdapter(config["llm"],**({"api_key":keys.get("llm","")} if credentials is not None else {})) if use_models else None
        if not use_models and strategy in {"hybrid","embedding"}:strategy="bm25"
        try:
            snapshot=OracleAdapter(config["oracle"],**({"credentials":keys.get("oracle",{})} if credentials is not None else {})).scan()
        except Exception as error:
            errors.append(_safe_error(error))
            oracle=config["oracle"]
            snapshot={"records":[],"scope":{k:oracle.get(k) for k in ("source_database","environment","container","schemas")},"complete":False,"errors":errors.copy(),"version":"scan_failed","captured_at":started}
    # Scope enforcement also protects exact-name checks and dependency evidence.
    scope=snapshot["scope"]
    snapshot["records"]=[r for r in snapshot["records"] if r.get("schema") in scope.get("schemas",[]) and all(r.get(k)==scope.get(k) for k in ("source_database","environment","container"))]
    reviewed_manifest=None
    if config.get("reviewed_definitions_path"):
        from .reviewed import apply_reviewed
        reviewed_manifest=apply_reviewed(config["reviewed_definitions_path"],requests,snapshot)
    graph=build_dependencies(requests)
    # Include actual contents, not just a self-reported metadata version.
    content_records=[{k:v for k,v in record.items() if k!="captured_at"} for record in snapshot["records"]]
    snapshot_signature=digest({"records":content_records,"scope":scope,"complete":snapshot["complete"],"version":snapshot["version"]})
    state_key=digest({"input":hashlib.sha256(Path(input_path).read_bytes()).hexdigest(),"catalog":snapshot_signature,"requests":[r["definition"] for r in requests],"review":reviewed_manifest,"config":signature_config(config),"source":_source_version(),"strategy":strategy})
    checkpoint=state_dir/"checkpoints"/(state_key+".json")
    prior={}
    if resume and checkpoint.exists():
        try:
            saved=json.loads(checkpoint.read_text(encoding="utf-8"))
            if saved.get("key")==state_key:
                prior={r["row_id"]:r for r in saved.get("results",[]) if not r.get("errors")}
        except (OSError,ValueError,KeyError,TypeError):
            errors.append("Eski checkpoint okunamadı; satırlar yeniden işleniyor.")
    index=None
    notify("Arama indeksi hazırlanıyor")
    try:
        index=HybridIndex(snapshot,embedding=embedding if strategy in {"hybrid","embedding"} else None,cache_dir=state_dir/"index")
        index.assert_fresh(snapshot)
    except Exception as error:
        errors.append(_safe_error(error))
    results=[]
    resumed=0
    for req in requests:
        row_id=req["row_id"]
        if row_id in prior and not errors:
            result=prior[row_id]
            resumed+=1
        else:
            result=_blank_result(req,snapshot)
            result["errors"].extend(req.get("input_errors",[]))
            result["errors"].extend(errors)
            if not result["errors"] and index is not None:
                try:
                    records=index.search(req,limit=config["retrieval"]["limit"],strategy=strategy)
                    def evaluate(records):
                        candidates=[]
                        for record in records:
                            c=evaluate_candidate(req,record)
                            c["score"]=record.get("_score",0)
                            c["retrieval"]=record.get("_retrieval",[])
                            c["source"]={k:record.get(k,"") for k in ("schema","object","column","data_type","column_comment","table_comment")}
                            candidates.append(c)
                        return candidates
                    candidates=evaluate(records)
                    expanded=not any(c["status"]=="supported" for c in candidates)
                    if expanded:
                        records=index.search(req,limit=config["retrieval"]["expanded_limit"],strategy=strategy,expanded=True)
                        candidates=evaluate(records)
                        result["reasons"].append("İlk kısa liste sonrası kontrollü genişletilmiş arama yapıldı.")
                    result["candidates"]=candidates
                    if llm and records:
                        # Advice is stored separately and cannot set semantic_status or modify facts.
                        result["model_review"]=llm.evaluate(req,records[:config["llm"].get("max_candidates",8)])
                    issues=req["definition"].get("issues",[])
                    if issues:
                        result["semantic_status"]="insufficient"
                        result["reasons"].extend(issues)
                    elif any(c["status"]=="supported" for c in candidates):
                        result["semantic_status"]="supported"
                    elif any(c["status"]=="insufficient" for c in candidates):
                        result["semantic_status"]="insufficient"
                    elif candidates:
                        result["semantic_status"]="conflict"
                    else:
                        result["semantic_status"]="not_found_in_scope" if snapshot["complete"] else "incomplete"
                    if not snapshot["complete"]:
                        result["reasons"].append("Katalog taraması eksik; kurum geneline ilişkin yokluk sonucu verilemez.")
                    if not candidates:
                        result["reasons"].append("Taranan kapsamda kanıtlı aday bulunamadı. Bu, bankada alanın kesinlikle bulunmadığı anlamına gelmez.")
                    else:
                        best=next((c for c in candidates if c["status"]=="supported"),candidates[0])
                        result["reasons"].extend(best.get("reasons",[]))
                except Exception as error:
                    result["semantic_status"]="incomplete"
                    result["errors"].append(_safe_error(error))
            else:
                result["reasons"].append("Teknik sorun veya giriş hatası nedeniyle inceleme tamamlanamadı.")
        results.append(result)
        notify("Değişkenler inceleniyor",len(results))
        atomic_json(checkpoint,{"key":state_key,"status":"running","results":results})
    results_by_id={r["row_id"]:r for r in results}
    for req in requests:
        result=results_by_id[req["row_id"]]
        # Recompute dependencies on every resume so a previous partial graph cannot be trusted.
        if result["errors"]:
            continue
        production=assess_production(req,results_by_id,graph,snapshot["records"])
        result["production_status"]=production["status"]
        result["dependencies"]=production.get("dependencies",[])
        result["reasons"]=list(dict.fromkeys(result["reasons"]+production.get("reasons",[])+graph["issues"].get(req["row_id"],[])))
    complete=bool(snapshot["complete"] and not errors and all(not r["errors"] for r in results))
    run_info={"Mod":"DEMO — sentetik, banka entegrasyonu doğrulanmadı" if mode=="demo" else "GERÇEK — yapılandırılmış kurum servisleri", "Tamamlanma":"Tamamlandı" if complete else "Eksik / teknik hata", "Uygulama sürümü":__version__,"Kod imzası":_source_version(),"Katalog sürümü":snapshot["version"],"Katalog içerik imzası":snapshot_signature,"Kapsam":scope,"Tarama tamamlandı":snapshot["complete"],"Metadata alım zamanı":snapshot["captured_at"],"Başlangıç UTC":started,"Bitiş UTC":datetime.now(timezone.utc).isoformat(),"Talep sayısı":len(requests),"Devam edilen satır":resumed,"Arama yaklaşımı":strategy,"Sonuç dağılımı":dict(Counter(r["semantic_status"] for r in results)),"Hatalar":errors+snapshot.get("errors",[]),"Model doğrulaması":"Sentetik embedding, Qwen çağrısı yok" if mode=="demo" else ({k:{f:config[k].get(f) for f in ("model","model_version","dimensions")} for k in ("llm","embedding")} if config.get("use_models",True) else "Model kullanılmadı; canlı Oracle kataloğunda ad ve metin araması"),"Yorum":"Arama skorları doğruluk yüzdesi değildir. Uzman kararı kullanıcı tarafından doldurulur. Görüş ve hazırlama süresi korunur.","Girdi koruması":"Girdi sayfaları değer/metin ve hücre biçimleriyle kopyalandı. Formüller güvenli metin olarak tutulur; çalıştırılmaz. Grafik/makro/harici bağlantı yürütülmez.","İşlenen sayfalar":list(dict.fromkeys(r["sheet"] for r in requests))}
    output=write_results(input_path,output_path,requests,results,run_info)
    atomic_json(state_dir/"feature_dependencies.json",graph)
    atomic_json(checkpoint,{"key":state_key,"status":"complete" if complete else "partial","results":results})
    atomic_json(state_dir/"last_run.json",{"run_info":run_info,"results":results})
    return {"results":results,"run_info":run_info,"output":output}
