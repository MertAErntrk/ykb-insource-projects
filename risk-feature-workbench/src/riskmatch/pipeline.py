"""Aday havuzu, gösterim kapısı, kanıt değerlendirmesi ve satır bazında yeniden devam."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from time import monotonic, sleep
import hashlib
import json
import os
from pathlib import Path

from . import __version__
from .config import DEFAULT_EXCLUDE_OBJECTS, load_config, signature_config, validate_exclude_objects
from .errors import ConfigError, IntegrationError, StaleIndexError, OracleOperationError, CatalogScanError, AnalysisCancelled, InputError, ReportWriteError
from .excelio import read_requests, write_results
from .derivation import plan_tiers, resolve_row

INDEX_SETTINGS=("pool_size","lexical_k","semantic_k","shortlist_embed","background_size","table_k","table_embed")
# End-of-job model check: two canaries catch a swapped model; the full set runs when the fingerprint expires.
VERIFY_SAMPLE=2
# derivation.REASON_TEXT'te technical_error şablonu yok; metin burada tutulur.
TECHNICAL_REASON="Teknik sorun veya giriş hatası nedeniyle inceleme tamamlanamadı."
MODEL_CHANGED_REASON="Embedding modeli analiz sırasında değişti; bu satırın anlamsal sonucu yeniden çalıştırılmalı."

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()

def atomic_json(path, value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+".writing")
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    temporary.replace(path)

def _try_json(path, value, notes):
    # Checkpoints and run logs are caches: a full disk, lock or permission error must not discard searched rows.
    try:
        atomic_json(path,value)
    except OSError:
        notes.append("Ara durum dosyası yazılamadı (disk dolu, kilit veya izin); arama sonuçları etkilenmedi.")

def _source_version():
    root=Path(__file__).parent
    return digest({str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*.py"))})

def index_settings(config):
    """HybridIndex arguments; analysis jobs and the background indexer must share them to share one index."""
    return {k:config["retrieval"][k] for k in INDEX_SETTINGS}

def catalog_key(oracle,credentials=None):
    """Catalog cache key for one login and scan scope. User and DSN are hashed, never stored."""
    from .catalog_cache import cache_key
    keys=credentials if credentials is not None else {k:os.environ.get(oracle.get(k+"_env") or "","") for k in ("user","dsn")}
    scope="|".join(str(oracle.get(k,"")) for k in ("source_database","environment","container","max_records"))
    # Dışlama kuralı (varsayılan dahil) değişirse katalog yeniden taranır.
    rules=oracle.get("exclude_objects")
    scope+="|"+json.dumps(validate_exclude_objects(DEFAULT_EXCLUDE_OBJECTS if rules is None else rules),sort_keys=True)
    return cache_key(keys.get("user",""),keys.get("dsn",""),scope,oracle.get("schemas") or [])

def _physical(name,snapshot):
    if any(str(r["column"]).casefold()==name.casefold() for r in snapshot["records"]):
        return "present"
    return "absent_in_scope" if snapshot["complete"] else "unknown"

def _blank_result(req,snapshot):
    return {"row_id":req["row_id"],"physical_name":_physical(req["name"],snapshot),"semantic_status":"incomplete","row_status":"not_evaluated","production_status":"not_evaluated","reasons":[],"candidates":[],"rejected_candidates":[],"dependencies":[],"errors":[],"expert_decision":"",
            "verdict":"karar_verilemedi","verdict_reasons":[],"derivation":None,"tier_trace":[]}

def _technical_reason():
    return {"code":"technical_error","text":TECHNICAL_REASON}

def _semantic_status(req,candidates,row_status):
    # Legacy summary kept for dependency checks and older readers; row_status is the primary outcome.
    if req["definition"].get("issues"):
        return "insufficient"
    if any(c["status"]=="supported" for c in candidates):
        return "supported"
    if any(c["status"]=="insufficient" for c in candidates):
        return "insufficient"
    if candidates:
        return "conflict"
    return "not_found_in_scope" if row_status=="none_in_scope" else "incomplete"

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

# Tam indeks kapısı: en çok bu kadar başlatma; bildirim aralığı ve tek uyku dilimi (iptal için ≤1 sn).
INDEX_MAX_STARTS=3
INDEX_POLL_SECONDS=2.0
_INDEX_SLICE_SECONDS=0.5
_sleep=sleep

def _thousands(value):
    return format(int(value),",").replace(",",".")

def _queue_counts(index):
    """(eksik, toplam) tablo + kolon belgesi."""
    if hasattr(index,"queue_counts"):
        return index.queue_counts()
    missing=len(index.index_queue())
    return missing,missing

def _wait_stage(status,documents=0):
    """documents: katalogdaki tüm tablo + kolon belgesi. Bu koşudan önce depoda olanlar da sayılır;
    yeniden başlatmada yüzde sıfıra dönmez."""
    done,run_total=int(status.get("done") or 0),int(status.get("total") or 0)
    total=max(int(documents or 0),run_total)
    stored=total-run_total+done
    percent=int(stored*100/total) if total else 0
    stage="İndeks tamamlanması bekleniyor · %%%d · %s/%s"%(percent,_thousands(stored),_thousands(total))
    eta=status.get("eta_s")
    if eta is not None and status.get("state")=="running":
        stage+=" · tahmini kalan %d dk"%max(1,round(eta/60))
    return stage

def _await_full_index(indexer,notify,check_cancelled,*,max_starts=INDEX_MAX_STARTS,poll=INDEX_POLL_SECONDS):
    """Arka plan indeksleyici bütün tablo ve kolon belgelerini vektör deposuna yazana kadar bekler.

    Eksik yoksa hemen döner; çalışıyorsa bekler; durmuş ve eksikse yeniden başlatır. İkinci başarısızlıkta
    ya da max_starts başlatmadan sonra eksik kalırsa IntegrationError; iptal AnalysisCancelled ile çıkar.
    """
    starts=failures=0
    last_notice=documents=None
    while True:
        check_cancelled()
        status=indexer.status()
        if status.get("state")=="running":
            if last_notice is None or monotonic()-last_notice>=poll:
                if documents is None:documents=_queue_counts(indexer.index)[1]
                notify(_wait_stage(status,documents),phase="index_wait")
                last_notice=monotonic()
            _sleep(min(_INDEX_SLICE_SECONDS,poll))
            continue
        # Bitmek üzere olan iş parçacığı start()'ı yutmasın.
        waiter=getattr(indexer,"wait",None)
        if waiter is not None:
            waiter(1.0)
        missing,total=_queue_counts(indexer.index)
        if missing==0:
            return
        if status.get("state")=="failed":
            failures+=1
        if failures>=2 or starts>=max_starts:
            percent=int((total-missing)*100/total) if total else 0
            message="İndeks %%%d'de kaldı (%s belge eklenemedi); arama yapılmadı. İndeksi yeniden başlatıp tekrar deneyin."%(percent,_thousands(missing))
            if status.get("message"):
                message+=" "+str(status["message"])
            raise IntegrationError(message)
        indexer.start()
        starts+=1
        last_notice=None

def _select(requests,row_ids):
    """Rows to search in this run; the rest of the workbook stays in the report as not searched."""
    if row_ids is None:
        return requests
    if not isinstance(row_ids,(list,tuple)) or not row_ids or any(not isinstance(v,str) for v in row_ids):
        raise InputError("Aranacak satırlar boş olmayan bir liste olmalı.")
    wanted=set(row_ids)
    selected=[r for r in requests if r["row_id"] in wanted]
    if len(selected)!=len(wanted):
        raise InputError("Seçili satırların bir kısmı dosyada bulunamadı. Dosyayı yeniden yükleyin.")
    return selected

def analyze(input_path,output_path,**options):
    """Run one analysis. On cancellation the raised AnalysisCancelled carries .partial={"results": [...]}.

    resources: shared riskmatch.resources.Resources (catalog cache, vector store, index reuse). Without it a
    real run opens one under state_dir and closes it at the end. index_epoch: callers that split one job into
    several analyze calls pass the same value so every call searches the same frozen semantic index.
    """
    collected=[]
    own=None
    if options.get("resources") is None and options.get("mode","demo")!="demo":
        from .resources import Resources
        state_dir=Path(options.get("state_dir") or Path(output_path).parent/"runtime")
        own=options["resources"]=Resources(state_dir/"resources")
    try:
        return _analyze(input_path,output_path,_collected=collected,**options)
    except AnalysisCancelled as error:
        error.partial={"results":deepcopy(collected)}
        raise
    finally:
        if own is not None:
            own.close()

def _analyze(input_path,output_path,*,mode="demo",config_path=None,state_dir=None,resume=True,strategy="hybrid",config_data=None,credentials=None,column_mapping=None,progress=None,result_callback=None,cancelled=None,row_ids=None,resources=None,write_report=True,index_epoch=None,require_full_index=False,_collected=None):
    from .semantics import parse_definition,build_dependencies,assess_production,prepare_production_catalog
    from .retrieval import HybridIndex
    from .ranking import rank,gate_config
    from .demo import demo_snapshot,DemoEmbedding
    started=datetime.now(timezone.utc).isoformat()
    clock_started=monotonic()
    results=_collected if _collected is not None else []
    current_name=""
    def check_cancelled():
        if cancelled and cancelled():
            raise AnalysisCancelled("Analiz kullanıcı tarafından durduruldu.")
    check_cancelled()
    config=load_config(config_path,mode,**({"data":config_data} if config_data is not None else {}))
    gate=gate_config(config["retrieval"].get("gate"))
    settings=index_settings(config)
    requests=read_requests(input_path,column_mapping)
    selected=_select(requests,row_ids)
    def notify(stage,completed=0,phase="analysis"):
        check_cancelled()
        if progress:progress({"stage":stage,"completed":completed,"total":len(selected),"phase":phase,
                              "elapsed_seconds":round(monotonic()-clock_started,1),"variable":current_name})
    def catalog_progress(value):
        check_cancelled()
        if progress:progress({**value,"completed":0,"total":len(selected),"phase":"catalog"})
    def model_progress(value):
        check_cancelled()
        if progress:progress({**value,"completed":len(results),"total":len(selected),
                              "elapsed_seconds":round(monotonic()-clock_started,1),"variable":current_name})
    notify("Oracle kataloğu okunuyor",phase="catalog")
    for req in requests:
        req["definition"]=parse_definition(req["name"],req.get("description_for_parse",req["description"]),config["naming"])
    state_dir=Path(state_dir or Path(output_path).parent/"runtime")
    errors=[]
    warnings=[]
    llm=None
    embedding=None
    ctx=None
    catalog_info={"source":"demo" if mode=="demo" else "scan"}
    if mode=="demo":
        snapshot=demo_snapshot()
        embedding=DemoEmbedding()
    else:
        from .adapters.oracle import OracleAdapter, oracle_error_code
        from .adapters.qwen import EmbeddingAdapter,LLMAdapter
        use_models=config.get("use_models",True)
        keys=credentials or {}
        embedding=EmbeddingAdapter(config["embedding"],**({"api_key":keys.get("embedding","")} if credentials is not None else {})) if use_models else None
        llm=LLMAdapter(config["llm"],**({"api_key":keys.get("llm","")} if credentials is not None else {})) if use_models and config.get("llm_review",True) else None
        if not use_models and strategy in {"hybrid","embedding"}:strategy="lexical"
        try:
            oracle_credentials=keys.get("oracle",{}) if credentials is not None else None
            adapter=OracleAdapter(config["oracle"],**({"credentials":oracle_credentials} if credentials is not None else {}))
            snapshot,catalog_info=resources.catalog(adapter,catalog_key(config["oracle"],oracle_credentials),progress=catalog_progress)
        except AnalysisCancelled:
            raise
        except Exception as error:
            check_cancelled()
            errors.append(_safe_error(error))
            oracle=config["oracle"]
            snapshot={"records":[],"scope":{k:oracle.get(k) for k in ("source_database","environment","container","schemas")},"complete":False,"errors":errors.copy(),"version":"scan_failed","captured_at":started}
            snapshot["checks"]=[{"name":"Oracle katalog taraması","status":"failed","elapsed_ms":0,
                                 "message":errors[-1],"code":oracle_error_code(error) or "CATALOG_UNAVAILABLE"}]
    check_cancelled()
    # Scope enforcement also protects exact-name checks and dependency evidence. Copy: the snapshot may be cached.
    scope=snapshot["scope"]
    snapshot={**snapshot,"records":[r for r in snapshot["records"] if r.get("schema") in scope.get("schemas",[]) and all(r.get(k)==scope.get(k) for k in ("source_database","environment","container"))]}
    catalog_diagnostics={"ok":bool(snapshot["complete"]),"checks":snapshot.get("checks",[]),
                         "record_count":len(snapshot["records"]),"scope":scope,
                         "stats":snapshot.get("scan_stats",{}),"source":catalog_info.get("source"),
                         "errors":list(dict.fromkeys(errors+snapshot.get("errors",[])))}
    if mode=="real" and not snapshot["complete"] and not snapshot["records"]:
        notify("Oracle kataloğu okunamadı",phase="catalog")
        message=" ".join(catalog_diagnostics["errors"]) or "Seçili şemalarda okunabilen katalog kaydı yok. Şema seçimini ve katalog erişimini kontrol edin."
        catalog_diagnostics["message"]=message
        atomic_json(state_dir/"catalog-diagnostics.json",catalog_diagnostics)
        code=next((check["code"] for check in catalog_diagnostics["checks"]
                   if check.get("status")=="failed" and check.get("code")),"CATALOG_UNAVAILABLE")
        raise CatalogScanError(message,catalog_diagnostics,code)
    if mode!="demo" and embedding is not None and strategy in {"hybrid","embedding"}:
        notify("Embedding modeli doğrulanıyor",phase="model_check")
        try:
            # Stored vectors are reused only under this fingerprint; an unknown or swapped model gets a new namespace.
            ctx=resources.embedding(embedding)
        except AnalysisCancelled:
            raise
        except Exception:
            check_cancelled()
            if require_full_index:
                raise IntegrationError("Embedding servisine ulaşılamadı; indeks tamamlanamadığı için arama yapılmadı.") from None
            ctx=None
            strategy="lexical"
            warnings.append("Embedding servisine ulaşılamadı veya model parmak izi alınamadı; ad ve metin aramasıyla devam edildi.")
        if require_full_index:
            # Web ile aynı anahtar (snapshot sürümü, namespace, index_settings): aynı indeksleyici paylaşılır.
            _await_full_index(resources.indexer(snapshot,ctx,settings),notify,check_cancelled)
    reviewed_manifest=None
    if config.get("reviewed_definitions_path"):
        from .reviewed import apply_reviewed
        reviewed_manifest=apply_reviewed(config["reviewed_definitions_path"],requests,snapshot)
    graph=build_dependencies(requests)
    # Include actual contents, not just a self-reported metadata version.
    content_records=[{k:v for k,v in record.items() if k!="captured_at"} for record in snapshot["records"]]
    snapshot_signature=digest({"records":content_records,"scope":scope,"complete":snapshot["complete"],"version":snapshot["version"]})
    namespace_id=ctx.namespace.id if ctx is not None else None
    state_key=digest({"input":hashlib.sha256(Path(input_path).read_bytes()).hexdigest(),"catalog":snapshot_signature,"requests":[r["definition"] for r in requests],"review":reviewed_manifest,"config":signature_config(config),"source":_source_version(),"strategy":strategy,"model":namespace_id})
    checkpoint=state_dir/"checkpoints"/(state_key+".json")
    index=None
    stats_before={}
    notify("Ad ve açıklama araması hazırlanıyor",phase="index")
    try:
        if mode=="demo":
            index=HybridIndex(snapshot,embedding=embedding if strategy in {"hybrid","embedding"} else None,progress=model_progress,cancelled=cancelled,**settings)
            index.embed_all()
            new_epoch=True
        else:
            if strategy=="embedding" and ctx is None:strategy="lexical"
            index=resources.index(snapshot,ctx,settings,refresh=False)
            # One frozen semantic set per job: rows in later groups must not see vectors stored by earlier rows.
            new_epoch=index_epoch is None or getattr(index,"job_epoch",None)!=index_epoch
            if new_epoch:
                index.refresh_index()
                index.job_epoch=index_epoch
        stats_before=index.begin_job(progress=model_progress,cancelled=cancelled,reset_circuit=new_epoch)
        index.assert_fresh(snapshot)
    except AnalysisCancelled:
        raise
    except Exception as error:
        index=None
        errors.append(_safe_error(error))
    coverage_now=index.index_coverage if index is not None else None
    prior={}
    if resume and checkpoint.exists():
        try:
            saved=json.loads(checkpoint.read_text(encoding="utf-8"))
            if saved.get("key")==state_key:
                # Retry omitted model work; reuse semantic results only from the same index coverage.
                # Rows saved before the verdict model have no "verdict" and are searched again.
                prior={r["row_id"]:r for r in saved.get("results",[])
                       if "verdict" in r and not r.get("errors") and not r.get("model_changed")
                       and not r.get("retrieval_info",{}).get("embedding_skipped",False)
                       and (r.get("retrieval_info",{}).get("semantic_scope") in ("full",None) or ctx is None
                            or r.get("retrieval_info",{}).get("index_coverage")==coverage_now)
                       and not (llm is not None and r.get("llm_requested") and not r.get("model_review"))}
        except (OSError,ValueError,KeyError,TypeError):
            errors.append("Eski checkpoint okunamadı; satırlar yeniden işleniyor.")
    resumed=0
    llm_calls=0
    llm_failed=False
    checkpoint_at=monotonic();io_notes=[]
    # Schema priority: DM_CAD first, then DM, then EDW; a later tier is searched only without a direct match.
    tier_plan=plan_tiers((config.get("oracle") or {}).get("schemas") or scope.get("schemas") or [],config["retrieval"].get("schema_tiers"))
    schema_complete=snapshot.get("schema_complete") or {}
    def run(tier_req,tier):
        records=index.search(tier_req,strategy=strategy)
        info=deepcopy(getattr(index,"last_search_info",{}))
        tier_complete=all(schema_complete.get(s,snapshot["complete"]) for s in tier["schemas"])
        ranked=rank(tier_req,records,gate=gate,semantic_scope=info.get("semantic_scope","none"),catalog_complete=bool(tier_complete))
        return records,info,ranked
    for req in selected:
        check_cancelled()
        current_name=req["name"]
        notify("Kaynak adayları aranıyor",len(results))
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
                    resolved=resolve_row(req,tier_plan["tiers"],run,unselected=tier_plan["unselected"],schema_complete=schema_complete,
                                         catalog_complete=bool(snapshot["complete"]),naming=config["naming"])
                    outcome=resolved["outcome"]
                    ranked,records=outcome["ranked"],outcome["records"]
                    # The weakest tier scope is kept so resume never trusts a partially searched tier.
                    scope_used=outcome["semantic_scope"]
                    info={**outcome["info"],"tier":outcome["tier"],"semantic_scope":scope_used}
                    candidates=ranked.get("candidates") or []
                    result.update({"candidates":candidates,"rejected_candidates":ranked.get("rejected_candidates") or [],
                                   "verdict":resolved["verdict"],"verdict_reasons":resolved["verdict_reasons"],
                                   "derivation":resolved["derivation"],"tier_trace":resolved["tier_trace"],
                                   "row_status":ranked.get("row_status","not_evaluated"),"retrieval_info":info,
                                   "pool_ids":[r["catalog_id"] for r in records],
                                   "coverage":{"catalog_complete":bool(snapshot["complete"]),"semantic_scope":scope_used,
                                               "index_coverage":info.get("index_coverage"),"pool_size":len(records),
                                               "catalog_records":len(snapshot["records"])}})
                    result["retrieval_warnings"]=warnings+([info["warning"]] if info.get("warning") else [])
                    result["semantic_status"]=_semantic_status(req,candidates,result["row_status"])
                    result["reasons"].extend(req["definition"].get("issues",[]))
                    if not snapshot["complete"]:
                        result["reasons"].append("Katalog taraması eksik; kurum geneline ilişkin yokluk sonucu verilemez.")
                    result["reasons"].extend(ranked.get("notes") or [])
                    if candidates:
                        result["reasons"].extend(candidates[0].get("reasons",[]))
                    result["reasons"].extend(r["text"] for r in result["verdict_reasons"])
                    result["reasons"].extend(result["retrieval_warnings"])
                    # The model is consulted only where the evidence is ambiguous; it never adds or removes candidates.
                    if llm and candidates and not any(c["meaning"]=="strong" for c in candidates):
                        result["llm_requested"]=True
                        if llm_failed:
                            result["reasons"].append("LLM yorumu önceki servis hatası nedeniyle atlandı; kaynak adayları korundu.")
                        else:
                            notify("LLM aday yorumu bekleniyor",len(results),phase="llm")
                            try:
                                llm_calls+=1
                                result["model_review"]=llm.evaluate(req,[{**c["source"],"catalog_id":c["catalog_id"]} for c in candidates])
                            except AnalysisCancelled:
                                raise
                            except Exception:
                                llm_failed=True
                                result["reasons"].append("LLM yorumu alınamadı; kaynak adayları korundu. Kalan yorum çağrıları atlandı.")
                            check_cancelled()
                except AnalysisCancelled:
                    raise
                except Exception as error:
                    result["semantic_status"]="incomplete"
                    result["row_status"]="not_evaluated"
                    result.update({"verdict":"karar_verilemedi","verdict_reasons":[_technical_reason()],"derivation":None})
                    result["errors"].append(_safe_error(error))
            else:
                result["verdict_reasons"]=[_technical_reason()]
                result["reasons"].append(TECHNICAL_REASON)
        results.append(result)
        notify("Değişkenler inceleniyor",len(results))
        if result_callback:
            result_callback(deepcopy(result))
        if len(results)==1 or len(results)%10==0 or monotonic()-checkpoint_at>=5:
            _try_json(checkpoint,{"key":state_key,"status":"running","results":results},io_notes)
            checkpoint_at=monotonic()
    model_check="not_needed"
    if ctx is not None and index is not None and index.stats.get("embedding_calls",0)>stats_before.get("embedding_calls",0):
        notify("Embedding modeli yeniden doğrulanıyor",len(results),phase="model_check")
        try:
            model_check="same" if resources.verify(ctx,sample=VERIFY_SAMPLE) else "changed"
        except AnalysisCancelled:
            raise
        except Exception:
            model_check="unverified"
        if model_check=="changed":
            for result in results:
                result["model_changed"]=True
                result["reasons"].append(MODEL_CHANGED_REASON)
    results_by_id={r["row_id"]:r for r in results}
    notify("Kaynak kanıtları ve bağımlılıklar değerlendiriliyor",len(results),phase="evidence")
    production_catalog=prepare_production_catalog(snapshot["records"])
    for req in selected:
        check_cancelled()
        result=results_by_id[req["row_id"]]
        # Recompute dependencies on every resume so a previous partial graph cannot be trusted.
        if result["errors"]:
            continue
        production=assess_production(req,results_by_id,graph,snapshot["records"],catalog_context=production_catalog)
        result["production_status"]=production["status"]
        result["dependencies"]=production.get("dependencies",[])
        result["reasons"]=list(dict.fromkeys(result["reasons"]+production.get("reasons",[])+graph["issues"].get(req["row_id"],[])))
    complete=bool(snapshot["complete"] and not errors and not warnings and model_check not in ("changed","unverified") and all(not r["errors"] and not (r.get("retrieval_info") or {}).get("embedding_skipped") for r in results))
    stats_after=dict(getattr(index,"stats",{}))
    job_stats={k:(v-stats_before.get(k,0) if isinstance(v,(int,float)) and not isinstance(v,bool) else v) for k,v in stats_after.items()}
    scopes=Counter((r.get("coverage") or {}).get("semantic_scope","none") for r in results)
    if mode=="demo":
        model_info="Sentetik embedding, Qwen çağrısı yok"
    elif config.get("use_models",True):
        model_info={k:{f:config[k].get(f) for f in ("model","model_version","dimensions")} for k in (("embedding","llm") if llm else ("embedding",))}
        model_info["embedding"].update({"model_selection":getattr(embedding,"model_selection","explicit"),
                                        "identity_status":getattr(embedding,"identity_status","configured"),
                                        "fingerprint":({"namespace":ctx.namespace.id[:12],"status":ctx.fingerprint.get("status"),"dims":ctx.fingerprint.get("dims")}
                                                       if ctx is not None else None),
                                        "end_of_job_check":model_check})
    else:
        model_info="Model kullanılmadı; canlı Oracle kataloğunda ad ve metin araması"
    run_info={"Mod":"DEMO — sentetik, banka entegrasyonu doğrulanmadı" if mode=="demo" else "GERÇEK — yapılandırılmış kurum servisleri", "Tamamlanma":"Tamamlandı" if complete else "Eksik / teknik hata", "Uygulama sürümü":__version__,"Kod imzası":_source_version(),"Katalog sürümü":snapshot["version"],"Katalog içerik imzası":snapshot_signature,"Kapsam":scope,"Tarama tamamlandı":snapshot["complete"],"Metadata alım zamanı":snapshot["captured_at"],"Başlangıç UTC":started,"Bitiş UTC":datetime.now(timezone.utc).isoformat(),"Talep sayısı":len(requests),"Aranan satır":len(selected),"Devam edilen satır":resumed,"Arama yaklaşımı":strategy,"Sonuç dağılımı":dict(Counter(r.get("row_status","not_evaluated") for r in results)),"Hatalar":errors+snapshot.get("errors",[]),"Model doğrulaması":model_info,"Yorum":"Arama skorları doğruluk yüzdesi değildir. Uzman kararı kullanıcı tarafından doldurulur. Görüş ve hazırlama süresi korunur.","Girdi koruması":"Girdi sayfaları değer/metin ve hücre biçimleriyle kopyalandı. Formüller güvenli metin olarak tutulur; çalıştırılmaz. Grafik/makro/harici bağlantı yürütülmez.","İşlenen sayfalar":list(dict.fromkeys(r["sheet"] for r in requests))}
    run_info["Hatalar"]=list(dict.fromkeys(errors+snapshot.get("errors",[])))
    run_info["Uyarılar"]=list(dict.fromkeys(warnings+(["Embedding modeli analiz sırasında değişti; anlamsal sonuçlar yeniden çalıştırılmalı."] if model_check=="changed" else [])
                                            +(["İş sonunda embedding modeli yeniden doğrulanamadı."] if model_check=="unverified" else [])+io_notes))
    run_info["Katalog kaynağı"]={"source":catalog_info.get("source"),"age_seconds":catalog_info.get("age_seconds"),
                                 "signature_checked":catalog_info.get("signature_checked")}
    run_info["Arama biçimi"]=("Ad, kısaltma ve metin araması ile anlamsal arama" if strategy in {"hybrid","embedding"} and index is not None and index.embed is not None
                              else "Ad, kısaltma ve metin araması (anlamsal arama yok)")
    run_info["Anlamsal kapsam"]={"index_coverage":round(coverage_now,4) if coverage_now is not None else None,
                                 "rows":dict(scopes)}
    run_info["Arama istatistikleri"]={**job_stats,"llm_calls":llm_calls,"elapsed_seconds":round(monotonic()-clock_started,1)}
    run_info["Arama sınırlamaları"]=("Her değişken için aynı kanallarla en çok "+str(settings["pool_size"])+" kayıtlık arka plan havuzu kurulur; "
                                    "kapıdan geçen en çok "+str(gate["max_shown"])+" aday gösterilir. Anlamsal arama yalnız indekslenmiş katalog "
                                    "metinlerinde ve satırın kendi kısa listesinde yapılır; indeks tam değilse kelime olarak benzemeyen adaylar kaçabilir.")
    run_info["Gösterim kapısı"]=gate
    run_info["LLM yorumu"]="Yalnız güçlü olmayan adaylı satırlarda isteğe bağlı yorum" if llm else "Kapalı; kaynak araması ve kanıt değerlendirmesi yapıldı"
    run_info["Katalog tanılama"]=catalog_diagnostics
    run_info["Önbellek ve devam politikası"]={
        "persistent_embedding_cache":ctx is not None,
        "checkpoint_resume":bool(resume),
        "reason":("Saklanan vektörler yalnız model parmak izi (8 kanarya metni) eşleşirse kullanılır; iş sonunda "
                  +str(VERIFY_SAMPLE)+" kanaryayla yeniden doğrulanır." if ctx is not None else
                  "Anlamsal vektör kullanılmadı."),
    }
    check_cancelled()
    notify("Excel raporu hazırlanıyor",len(results),phase="export")
    output,report_error=None,None
    if write_report:
        try:
            output=write_results(input_path,output_path,requests,results,run_info)
        except ReportWriteError as error:
            # Results stay usable; the caller may retry the report without searching again.
            report_error={"kind":error.kind,"message":str(error)}
    _try_json(state_dir/"feature_dependencies.json",graph,io_notes)
    if model_check=="changed":
        try:
            checkpoint.unlink(missing_ok=True)
        except OSError:
            io_notes.append("Eski checkpoint silinemedi; sonraki çalıştırma onu model kimliği nedeniyle kullanmaz.")
    else:
        _try_json(checkpoint,{"key":state_key,"status":"complete" if complete else "partial","results":results},io_notes)
    _try_json(state_dir/"last_run.json",{"run_info":run_info,"results":results},io_notes)
    run_info["Uyarılar"]=list(dict.fromkeys(run_info["Uyarılar"]+io_notes))
    return {"results":results,"requests":requests,"run_info":run_info,"output":output,"report_error":report_error}
