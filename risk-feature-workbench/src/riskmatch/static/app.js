"use strict";
const $=id=>document.getElementById(id);
const state={upload:null,job:null,results:[],mapping:{},running:false,connection:null,schemas:new Set(),nextVariable:0,reports:{},reportGeneration:{},schemaTesting:false,modelVerified:{},discovered:{},rows:[],picked:new Set(),index:null,timer:null,rendered:false};
const keys={llm:["base_url","endpoint","model","model_version","contract","request_template","response_path","models_endpoint","ca_file","api_key"],embedding:["base_url","endpoint","model","model_version","model_selection","max_batch","dimensions","request_template","response_path","models_endpoint","ca_file","api_key"]};
const fields={name:"Alan adı *",description:"Açıklama *",category:"Kategori",data_type:"Alan tipi",reported:"Halihazırda bildiriliyor mu",row_id:"Talep kimliği (varsa)"};
function node(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;}
function notice(message,success=false){const el=$("notice");el.textContent=message;el.className="notice"+(success?" success":"");el.hidden=false;el.focus();}
function page(name){document.querySelectorAll(".page").forEach(el=>el.hidden=el.id!==name);document.querySelectorAll(".nav-item").forEach(el=>el.classList.toggle("active",el.dataset.page===name));$("page-label").textContent={workspace:"Değişken inceleme",connections:"Model bağlantıları",results:"Sonuçlar"}[name];$("notice").hidden=true;updateScope();}
document.querySelectorAll("[data-page]").forEach(el=>el.addEventListener("click",()=>page(el.dataset.page)));
async function api(path,data){const options=data===undefined?{}:{method:"POST",headers:{"X-CSRF-Token":document.querySelector('meta[name="csrf-token"]').content},body:data instanceof FormData?data:JSON.stringify(data)};if(data!==undefined&&!(data instanceof FormData))options.headers["Content-Type"]="application/json";let response;try{response=await fetch(path,options);}catch{throw new Error("Uygulamaya ulaşılamıyor. Başlatma penceresinin açık olduğunu kontrol edin.");}const result=await response.json();if(!response.ok)throw new Error(result.error||"İşlem tamamlanamadı.");return result;}
async function action(button,fn){if(button.disabled)return;button.disabled=true;const previous=button.textContent;button.textContent="İşleniyor…";$("notice").hidden=true;try{await fn();}catch(error){notice(error.message);}finally{button.disabled=false;button.textContent=previous;updateScope();}}
function settings(force=false,only=null){const result={mode:"real",use_models:$("use-models").checked,llm_review:$("llm-review").checked};if(!result.use_models&&!force)return result;for(const [section,items] of Object.entries(keys)){if(only&&section!==only)continue;if(!force&&section==="llm"&&!result.llm_review)continue;result[section]={};for(const key of items){let value=$(section+"-"+key).value;if(key==="request_template"){if(value.trim()){try{value=JSON.parse(value);}catch{throw new Error((section==="llm"?"LLM":"Embedding")+" istek şablonu geçerli JSON olmalı.");}}else continue;}if(key==="dimensions"||key==="max_batch")value=Number(value);result[section][key]=value;}}return result;}
function updateScope(){
    const dbReady=!!state.connection&&state.schemas.size>0;
    const enabled=$("use-models").checked,review=enabled&&$("llm-review").checked;
    const modelsReady=!enabled||["embedding",...(review?["llm"]:[])].every(name=>state.modelVerified[name]===modelSignature(name));
    const ready=dbReady&&modelsReady,selected=[...state.schemas];
    $("test-schemas").disabled=!dbReady||state.schemaTesting;$("scope-name").textContent=state.connection?"EDWLIVE":"Bağlantı bekleniyor";
    $("scope-detail").textContent=selected.length?"Taranacak şemalar: "+selected.join(" → "):"Bağlandığınızda taranacak şemalar burada görünür.";
    $("scan-schemas").textContent=selected.length?"Taranacak şemalar: "+selected.join(" → "):"Taranacak şema bilgisi alınamadı.";
    $("index-panel").hidden=!indexVisible();
    $("llm-review").disabled=!enabled||state.running;$("use-models").disabled=state.running;
    $("mode-note").textContent=!enabled?"Oracle kataloğunda alan adı ve açıklama araması yapılır.":"Embedding ile anlam karşılaştırması."+(review?" Her değişken için ayrıca LLM yorumu istenir.":" LLM yorumu kapalı.");
    $("manual-run").disabled=!ready||state.running;$("run-button").disabled=!ready||!state.upload||!Object.keys(state.mapping).length||state.running||(state.rows.length>0&&!state.picked.size);
    const idx=state.index||{},pct=Number.isFinite(idx.index_coverage)?Math.round(idx.index_coverage*100):(idx.total>0?Math.round(100*(idx.done||0)/idx.total):0);
    document.querySelectorAll(".readiness").forEach(el=>el.textContent=state.running?"Analiz devam ediyor; sonuçlardan ilerlemeyi izleyebilir veya durdurabilirsiniz.":!dbReady?"Aramak için kullanıcı adı ve parolanızla bağlanın.":!modelsReady?(review?"LLM ve embedding testlerini başarıyla tamamlayın.":"Embedding bağlantısı doğrulanmadı; Bağlantılar sayfasından test edin veya embedding desteğini kapatın."):(enabled&&!idx.complete)?"Arama başlatılabilir; eşleştirme indeks tamamlanınca başlar (%"+pct+").":enabled?"İndeks tamam · "+selected.join(", ")+" şemalarında aranacak.":selected.join(", ")+" şemalarında aranacak.");
}
function showConnection(data){invalidateDiagnostics(["oracle","schemas"]);state.connection=data.ok?data:null;state.schemas.clear();state.index=null;$("login-form").hidden=data.ok;$("connected-area").hidden=!data.ok;$("connection-badge").textContent=data.ok?"Bağlandı · "+data.db_name:"Bağlantı bekleniyor";$("oracle-probe-status").textContent="";if(data.ok){for(const schema of (data.scan_schemas||[]))state.schemas.add(schema);const missing=data.missing_schemas||[];$("oracle-probe-status").textContent=state.schemas.size?(missing.length?(data.warning||"Bulunamayan şemalar: "+missing.join(", ")+"."):""):(data.warning||"Taranacak şema bulunamadı. Test ayrıntılarını kontrol edin.");}updateScope();updateIndexPanel();}
$("login-form").addEventListener("submit",event=>{event.preventDefault();action($("probe-oracle"),async()=>{showConnection({ok:false});const data=await diagnostic("oracle",()=>api("/api/connect",{user:$("oracle-user").value,password:$("oracle-password").value}));if(!data.ok){notice(data.error||"Oracle testindeki başarısız adımı inceleyin.");return;}$("oracle-password").value="";const oracleReport=state.reports.oracle;showConnection(data);state.reports.oracle=oracleReport;renderDiagnostics();await autoEmbedding();});});
$("test-schemas").addEventListener("click",event=>action(event.currentTarget,async()=>{state.schemaTesting=true;try{await diagnostic("schemas",()=>api("/api/diagnostics/oracle-schemas",{schemas:[...state.schemas]}));}finally{state.schemaTesting=false;}}));
$("disconnect").addEventListener("click",event=>action(event.currentTarget,async()=>{await api("/api/disconnect",{});showConnection({ok:false});$("oracle-user").focus();}));
$("use-models").addEventListener("change",()=>{updateScope();updateIndexPanel();});
$("llm-review").addEventListener("change",updateScope);
$("model-form").addEventListener("submit",event=>event.preventDefault());
function modelSignature(name){return JSON.stringify(keys[name].map(key=>$(name+"-"+key).value));}
function invalidateDiagnostics(names){let changed=false;for(const name of names){state.reportGeneration[name]=(state.reportGeneration[name]||0)+1;if(state.reports[name]){delete state.reports[name];changed=true;}}if(changed)renderDiagnostics(false);}
for(const name of ["llm","embedding"]){for(const key of keys[name]){$(name+"-"+key).addEventListener("input",()=>{
    if(key==="base_url"&&state.discovered[name]){for(const derived of ["endpoint","models_endpoint","model","model_version","request_template","response_path",...(name==="embedding"?["dimensions"]:["contract"])])$(name+"-"+derived).value="";state.discovered[name]=false;if(name==="embedding"){$("embedding-model_selection").value="explicit";$("embedding-max_batch").value="16";}}
    if(key==="model"&&$(name+"-model_version").value.startsWith("deployment:"))$(name+"-model_version").value="";
    if(name==="embedding"&&key==="model"){if($("embedding-model_selection").value==="server_default"){for(const derived of ["endpoint","models_endpoint","model_version","request_template","response_path","dimensions"])$("embedding-"+derived).value="";state.discovered.embedding=false;}$("embedding-model_selection").value="explicit";$("embedding-max_batch").value="16";}
    $(name+"-probe-status").dataset.status="untested";
    delete state.modelVerified[name];invalidateDiagnostics([name]);$(name+"-probe-status").textContent="Ayarlar değişti; bağlantıyı yeniden test edin.";updateScope();
});}}
async function probeModel(name){
    const inputs=keys[name].map(key=>$(name+"-"+key));
    if(name==="embedding")inputs.push($("embedding-preset"));
    const auto=!$(name+"-endpoint").value.trim()||state.discovered[name];
    inputs.forEach(el=>el.disabled=true);delete state.modelVerified[name];updateScope();
    $(name+"-probe-status").dataset.status="running";
    $(name+"-probe-status").textContent="Servis ve küçük doğrulama çağrısı test ediliyor…";
    try{
        const data=await diagnostic(name,()=>api("/api/probe/"+name,settings(true,name)));
        if(data.models)$(name+"-models").replaceChildren(...data.models.map(id=>{const option=node("option");option.value=id;return option;}));
        if(data.ok&&data.settings){for(const key of keys[name]){if(key==="api_key"||!(key in data.settings))continue;const value=data.settings[key];$(name+"-"+key).value=key==="request_template"?JSON.stringify(value,null,2):value;}state.discovered[name]=auto;state.modelVerified[name]=modelSignature(name);}
        $(name+"-probe-status").dataset.status=data.ok?"passed":"failed";
        $(name+"-probe-status").textContent=data.ok?data.message||"Bağlantı doğrulandı; ayarlar dolduruldu.":data.error||data.message||"Test tamamlanamadı; test sonuçlarını inceleyin.";
    }finally{inputs.forEach(el=>el.disabled=false);updateScope();}
    return state.modelVerified[name]===modelSignature(name);
}
document.querySelectorAll("[data-probe-model]").forEach(button=>button.addEventListener("click",()=>action(button,()=>probeModel(button.dataset.probeModel))));
async function autoEmbedding(){
    if(!$("use-models").checked||!state.connection||!state.schemas.size)return;
    let ok=false;try{ok=await probeModel("embedding");}catch{ok=false;}
    if(ok)await autoStartIndex();else notice("Embedding bağlantısı kurulamadı; Bağlantılar sayfasından kontrol edin.");
}
$("embedding-preset").addEventListener("change",event=>{delete state.modelVerified.embedding;invalidateDiagnostics(["embedding"]);$("embedding-probe-status").textContent="İstek biçimi değişti; yeniden test edin.";updateScope();const value=event.target.value;if(value==="openai"){$("embedding-request_template").value=JSON.stringify($("embedding-model").value.trim()?{model:"{model}",input:"{texts}"}:{input:"{texts}"},null,2);$("embedding-response_path").value="data[*].embedding";}if(value==="tei"){$("embedding-request_template").value=JSON.stringify({inputs:"{texts}"},null,2);$("embedding-response_path").value="$";}});
document.querySelectorAll("[data-input]").forEach(button=>button.addEventListener("click",()=>{const manual=button.dataset.input==="manual";$("manual-form").hidden=!manual;$("excel-panel").hidden=manual;document.querySelectorAll("[data-input]").forEach(el=>{const active=el===button;el.classList.toggle("active",active);el.setAttribute("aria-pressed",String(active));});}));
function renumberVariables(){const rows=[...$("manual-rows").children];$("manual-count").textContent=rows.length+" değişken";rows.forEach((row,i)=>{row.querySelector("strong").textContent="Değişken "+(i+1);const remove=row.querySelector("button");remove.disabled=rows.length===1;remove.setAttribute("aria-label","Değişken "+(i+1)+" sil");});$("add-variable").disabled=rows.length>=100;}
function addVariable(focus=true){if($("manual-rows").children.length>=100)return;const id=++state.nextVariable;const row=node("section",undefined,"variable-row");const head=node("div",undefined,"card-title");const remove=node("button","Sil","text-button remove-variable");remove.type="button";remove.addEventListener("click",()=>{row.remove();renumberVariables();$("add-variable").focus();});head.append(node("strong"),remove);row.append(head);for(const [field,title,tag,max] of [["name","Alan / kolon adı","input",256],["description","Açıklama","textarea",8000]]){const label=node("label",title);const input=node(tag);input.id="variable-"+id+"-"+field;input.name=field;input.required=true;input.maxLength=max;input.dataset.field=field;if(tag==="textarea"){input.rows=3;input.placeholder="Bu alan neyi ifade ediyor? Dönem, ölçüm ve hesaplama bilgilerini ekleyin.";}else input.placeholder="Aradığınız alanın adı";label.append(input);row.append(label);}$("manual-rows").append(row);renumberVariables();if(focus)row.querySelector("input").focus();}
$("add-variable").addEventListener("click",()=>addVariable());
function rowCountText(){$("row-selected").textContent=state.picked.size+" / "+state.rows.length+" satır seçili";$("row-count").textContent=state.rows.length+" satır · "+state.rows.filter(r=>r.searched).length+" aranmış";}
function renderRowList(){
    $("row-list").replaceChildren(...state.rows.map(row=>{const tr=node("tr"),pick=node("td"),box=node("input");box.type="checkbox";box.checked=state.picked.has(row.row_id);box.setAttribute("aria-label",row.name+" satırını seç");box.addEventListener("change",()=>{if(box.checked)state.picked.add(row.row_id);else state.picked.delete(row.row_id);rowCountText();updateScope();});pick.append(box);const st=node("td");if(row.searched)st.append(node("span",row.row_status_label,"badge rs-"+row.row_status));else st.textContent="Aranmadı";tr.append(pick,node("td",(row.sheet||"")+" · "+row.excel_row),node("td",row.name),st);return tr;}));
    rowCountText();$("row-picker").hidden=!state.rows.length;
}
async function loadRows(){
    if(!state.upload)return;const id=state.upload.id;
    try{const data=await api("/api/uploads/"+id+"/rows");if(!state.upload||state.upload.id!==id)return;const first=!state.rows.length;state.rows=data.rows;state.picked=first?new Set(state.rows.map(r=>r.row_id)):new Set([...state.picked].filter(x=>state.rows.some(r=>r.row_id===x)));renderRowList();updateScope();}
    catch(error){notice(error.message);}
}
function pickRows(kind){const rows=state.rows;let chosen=rows;if(kind==="first1")chosen=rows.slice(0,1);else if(kind==="first5")chosen=rows.slice(0,5);else if(kind==="first10")chosen=rows.slice(0,10);else if(kind==="next10")chosen=rows.filter(r=>!r.searched).slice(0,10);state.picked=new Set(chosen.map(r=>r.row_id));renderRowList();updateScope();}
document.querySelectorAll("[data-pick]").forEach(button=>button.addEventListener("click",()=>pickRows(button.dataset.pick)));
async function startAnalysis(payload){
    const job=await api("/api/analyze",{...payload,schemas:[...state.schemas],settings:settings()});
    attachJob(job.id);state.rendered=false;$("result-count").textContent="0";$("cancel-analysis").hidden=false;$("cancel-analysis").disabled=false;$("cancel-analysis").textContent="Durdur";$("progress-title").textContent="Analiz hazırlanıyor";$("progress-detail").textContent="İlk işlem başlatılıyor…";$("progress-time").textContent="";$("progress-warning").hidden=true;$("report-message").textContent="";$("retry-report").hidden=true;$("partial-report").hidden=true;$("failure-card").hidden=true;invalidateDiagnostics(["catalog"]);page("results");$("progress-card").hidden=false;$("empty-results").hidden=true;$("results-body").hidden=true;$("download").hidden=true;
    autoStartIndex();poll();
}
$("manual-form").addEventListener("submit",event=>{event.preventDefault();action($("manual-run"),async()=>{const variables=[...$("manual-rows").children].map(row=>Object.fromEntries([...row.querySelectorAll("[data-field]")].map(el=>[el.dataset.field,el.value])));await startAnalysis({input_type:"manual",variables});});});
$("run-button").addEventListener("click",event=>action(event.currentTarget,()=>{const all=state.picked.size===state.rows.length;return startAnalysis({input_type:"excel",upload_id:state.upload.id,mapping:state.mapping,row_ids:all?null:[...state.picked],group_size:Number($("group-size").value)});}));
$("excel-file").addEventListener("change",async event=>{const input=event.target,file=input.files[0];state.upload=null;state.mapping={};state.rows=[];state.picked=new Set();$("row-picker").hidden=true;$("mapping-card").hidden=true;$("file-label").textContent=file?.name||"Excel dosyasını seçin";updateScope();if(!file)return;input.disabled=true;$("upload-status").textContent="Excel okunuyor…";try{if(file.size>12*1024*1024)throw new Error("Excel dosyası en fazla 12 MB olabilir.");const form=new FormData();form.append("file",file);const result=await api("/api/upload",form);state.upload=result;renderMapping(result.sheets);$("mapping-card").hidden=false;$("mapping-details").open=result.sheets.some(s=>!s.suggested.name||!s.suggested.description);mappingSummary();$("upload-status").textContent="Dosya okundu · "+result.sheets.length+" sayfa";loadRows();}catch(error){$("upload-status").textContent="Dosya okunamadı.";notice(error.message);}finally{input.disabled=false;updateScope();}});
function mappingSummary(){const count=Object.keys(state.mapping).length;$("mapping-summary").textContent=count?count+" sayfa seçildi · sütunlar eşleştirildi":"Alan adı ve açıklama sütunlarını seçin";updateScope();}
function renderMapping(sheets){const container=$("mapping");container.replaceChildren();for(const sheet of sheets){const block=node("section",undefined,"sheet-map");const enable=node("input");enable.type="checkbox";enable.checked=!!sheet.suggested.name&&!!sheet.suggested.description;const label=node("label",undefined,"check");label.append(enable,node("span",sheet.name+" · "+sheet.rows+" satır"));block.append(label);const form=node("div",undefined,"mapping-grid");const headerLabel=node("label","Başlık satırı");const header=node("input");header.type="number";header.min="1";header.max="30";header.value=sheet.header_row;headerLabel.append(header);form.append(headerLabel);const selects={};for(const [key,title] of Object.entries(fields)){const field=node("label",title);const select=node("select");select.setAttribute("aria-label",sheet.name+" · "+title);const blank=node("option","Seçilmedi");blank.value="";select.append(blank);for(const item of sheet.headers){const option=node("option",item.index+" · "+item.label);option.value=item.index;select.append(option);}select.value=sheet.suggested[key]||"";selects[key]=select;field.append(select);form.append(field);}const update=()=>{if(enable.checked)state.mapping[sheet.name]={header_row:Number(header.value),columns:Object.fromEntries(Object.entries(selects).map(([key,el])=>[key,el.value?Number(el.value):null]))};else delete state.mapping[sheet.name];form.querySelectorAll("input,select").forEach(el=>el.disabled=!enable.checked);mappingSummary();};enable.addEventListener("change",update);form.addEventListener("change",update);update();block.append(form);const preview=node("details");preview.append(node("summary","İlk satırları göster"));const wrap=node("div",undefined,"preview");const table=node("table");const head=node("tr");sheet.headers.forEach(h=>head.append(node("th",h.label)));table.append(head);for(const row of sheet.preview){const tr=node("tr");row.forEach(v=>tr.append(node("td",v)));table.append(tr);}wrap.append(table);preview.append(wrap);block.append(preview);container.append(block);}}
function catalogReport(report){
    if(!report)return;
    state.reports.catalog={ok:!!report.ok,checked_at:new Date().toISOString(),message:report.message||(report.errors||[]).join(" "),checks:report.checks||[]};
    renderDiagnostics(false,"catalog");
}
function showJobFailure(job){
    $("progress-card").hidden=false;$("empty-results").hidden=true;$("download").hidden=true;
    $("failure-card").hidden=false;$("failure-title").textContent=job.failure_stage==="catalog"?"Katalog okunamadı; arama başlatılamadı":"Analiz tamamlanamadı";
    $("failure-message").textContent=job.error||"Analiz tamamlanamadı.";
    $("failure-input").textContent=(job.progress?.total||0)+" değişken hazır · "+state.results.length+" değişken incelendi";
    $("results-body").hidden=!state.results.length;
    if(state.results.length)renderResults(job);else{$("result-count").textContent="0";$("result-rows").replaceChildren();}
    catalogReport(job.catalog_diagnostics);
}
function duration(seconds){const value=Math.max(0,Math.floor(Number(seconds)||0));return value>=60?Math.floor(value/60)+" dk "+value%60+" sn":value+" sn";}
const activeStatuses=["running","cancelling","reporting"];
const jobTitles={cancelling:"Durduruluyor…",reporting:"Excel raporu yazılıyor",complete:"Tamamlandı",report_failed:"Sonuçlar hazır; Excel raporu yazılamadı",cancelled:"Analiz durduruldu",failed:"Analiz tamamlanamadı"};
function renderProgress(job){
    const progress=job.progress||{},active=activeStatuses.includes(job.status);
    $("progress-card").hidden=false;
    $("progress-title").textContent=jobTitles[job.status]||progress.stage||"Analiz sürüyor";
    if(job.status==="reporting"||(active&&!(progress.total>0)))$("progress").removeAttribute("value");
    else{$("progress").max=Math.max(progress.total||1,1);$("progress").value=job.status==="complete"?(progress.total||1):(progress.completed||0);}
    const parts=[];
    if(progress.stage&&job.status==="running")parts.push(progress.stage);
    if(progress.groups>1)parts.push("Grup "+(progress.group||1)+" / "+progress.groups);
    if(progress.total>0)parts.push((progress.completed||0)+" / "+progress.total+" değişken");
    if(progress.variable&&job.status==="running")parts.push("İşlenen: "+progress.variable);
    if(job.status==="cancelling")parts.push("Devam eden servis çağrısı bittiğinde duracak. Tamamlanan sonuçlar korunur.");
    if(job.status==="cancelled")parts.push("Kısmi sonuçlar aşağıdadır. Girdileriniz korunuyor.");
    $("progress-detail").textContent=parts.join(" · ");
    $("progress-time").textContent="Geçen süre: "+duration(job.elapsed_seconds);
    const stalled=active&&job.status!=="reporting"&&Number(job.last_update_seconds)>60;
    $("progress-warning").hidden=!stalled;$("progress-warning").textContent=stalled?"Servis yanıtı bekleniyor. Son işlem güncellemesi "+duration(job.last_update_seconds)+" önce.":"";
    $("report-message").textContent=job.status==="report_failed"?(job.error||job.report_error||"Excel raporu yazılamadı. Sonuçlar ekranda görünür."):job.status==="cancelled"?"Kısmi sonuçlar için Excel raporu isteğe bağlı oluşturulabilir.":"";
    $("retry-report").hidden=job.status!=="report_failed";
    $("partial-report").hidden=!(job.status==="cancelled"&&state.results.length&&!job.report_ready);
    $("cancel-analysis").hidden=!["running","cancelling"].includes(job.status);$("cancel-analysis").disabled=job.status!=="running";
    $("cancel-analysis").textContent=job.status==="cancelling"?"Durduruluyor…":"Durdur";
    const ready=!!job.report_ready;$("download").hidden=!ready;if(ready)$("download").href="/api/jobs/"+job.id+"/download";
}
$("cancel-analysis").addEventListener("click",async()=>{
    const button=$("cancel-analysis");button.disabled=true;
    try{await api("/api/jobs/"+state.job+"/cancel",{});button.textContent="Durduruluyor…";$("progress-title").textContent="Durduruluyor…";}
    catch(error){button.disabled=false;notice(error.message);}
});
for(const id of ["retry-report","partial-report"])$(id).addEventListener("click",event=>action(event.currentTarget,async()=>{await api("/api/jobs/"+state.job+"/report",{});poll();}));
function attachJob(id){clearTimeout(state.timer);state.job=id;state.results=[];state.running=true;state.timer=null;updateScope();}
async function poll(){
    clearTimeout(state.timer);const id=state.job;
    try{
        const job=await api("/api/jobs/"+id+"?since="+state.results.length);
        if(id!==state.job)return;
        if(Number.isFinite(job.result_count)&&job.result_count<state.results.length){state.results=[];state.timer=setTimeout(poll,0);return;}
        const added=(job.results||[]).length;state.results.push(...(job.results||[]));
        const active=activeStatuses.includes(job.status);
        state.running=active;
        $("empty-results").hidden=true;
        renderProgress(job);
        if(state.results.length&&(added||!active||!state.rendered))renderResults(job);
        state.rendered=state.results.length>0;
        if(active){state.timer=setTimeout(poll,1000);return;}
        updateScope();
        if(job.upload_id&&state.upload&&state.upload.id===job.upload_id)loadRows();
        if(job.status==="failed"){showJobFailure(job);return;}
        $("failure-card").hidden=true;
        if(job.status==="complete")catalogReport(job.run_info?.["Katalog tanılama"]);
    }catch(error){notice(error.message+" Analiz durumuna yeniden ulaşılmaya çalışılıyor.");if(state.running)state.timer=setTimeout(poll,3000);}
}
const statusOrder=[["found","Aday bulundu"],["weak_only","Yalnız zayıf"],["none_in_scope","Güvenilir aday yok"],["none_partial","Kapsam eksik"],["not_evaluated","İncelenemedi"]];
function renderResults(job){
    $("results-body").hidden=false;$("result-count").textContent=state.results.length;
    $("metrics").replaceChildren(...statusOrder.map(([key,label])=>{const el=node("div",undefined,"metric");el.append(node("strong",state.results.filter(r=>r.row_status===key).length),node("span",label));return el;}));
    const info=job.run_info||{},scope=info.Kapsam;
    const pieces=[state.results.length+" satır incelendi",jobTitles[job.status]||"Analiz devam ediyor · Kısmi sonuçlar"];
    if(scope)pieces.push(scope.source_database+" / "+scope.container,"Şemalar: "+scope.schemas.join(", "));
    if(job.status!=="complete")pieces.push("Tamamlanan değişkenler gösteriliyor; bütün incelemenin nihai sonucu değildir.");
    pieces.push(...(info.Hatalar||[]));$("run-summary").textContent=pieces.join(" · ");renderRows();
}
function sourcePath(c){const s=c.source||{};return [s.schema,s.object,s.column].filter(Boolean).join(".")||c.path||"";}
function renderRows(){const query=$("result-search").value.toLocaleLowerCase("tr"),filter=$("result-filter").value,verdictFilter=$("verdict-filter").value;const rows=state.results.filter(row=>(!filter||row.row_status===filter)&&(!verdictFilter||row.verdict===verdictFilter)&&(!query||[row.request.name,row.request.description,...(row.candidates||[]).map(sourcePath)].join(" ").toLocaleLowerCase("tr").includes(query)));$("visible-count").textContent=rows.length+" / "+state.results.length+" sonuç";$("result-rows").replaceChildren(...rows.map(row=>{const tr=node("tr");const first=node("td");const description=row.request.description||"";first.append(node("strong",row.request.name),node("small",description.slice(0,160)+(description.length>160?"…":"")));const source=node("td"),best=(row.candidates||[])[0];if(row.verdict_source)source.append(node("code",row.verdict_source));else if(best)source.append(node("code",sourcePath(best)));else source.textContent=row.row_status_label||"Aday yok";const count=node("td",String((row.candidates||[]).length));if((row.rejected_candidates||[]).length)count.append(node("small","Elenen yakın aday: "+row.rejected_candidates.length));const status=node("td");status.append(node("span",row.row_status_label,"badge rs-"+row.row_status));if((row.retrieval_warnings||[]).length)status.append(node("small","Arama kapsamı notu · Detayı inceleyin","retrieval-note"));const verdict=node("td");if(row.verdict)verdict.append(node("span",row.verdict_label,"badge vd-"+row.verdict));const detail=node("td");const button=node("button","Detay →","text-button");button.setAttribute("aria-label",row.request.name+" detayını aç");button.addEventListener("click",()=>showDetail(row));detail.append(button);tr.append(first,source,count,status,verdict,detail);return tr;}));if(!rows.length){const tr=node("tr"),td=node("td","Bu filtreye uygun sonuç yok.");td.colSpan=6;tr.append(td);$("result-rows").append(tr);}}
$("result-search").addEventListener("input",renderRows);$("result-filter").addEventListener("change",renderRows);$("verdict-filter").addEventListener("change",renderRows);
function candidateCard(candidate,index,row,rejected){
    const card=node("article",undefined,"candidate"+(rejected?" rejected":""));const source=candidate.source||{};
    card.append(node("code",(rejected?"":(index+1)+" · ")+sourcePath(candidate)));
    const tags=node("div",undefined,"tag-row");tags.append(node("span","Ad eşleşmesi: "+(candidate.name_match_label||"—"),"badge nm-"+candidate.name_match),node("span","Anlam uyumu: "+(candidate.meaning_label||"—"),"badge mn-"+candidate.meaning));card.append(tags);
    if(rejected&&candidate.rejected_reason)card.append(node("p","Elenme nedeni: "+candidate.rejected_reason,"muted small"));
    card.append(node("span","Kolon açıklaması","detail-label"),node("p",source.column_comment||"Katalogda kolon açıklaması yok."));
    if(source.table_comment)card.append(node("span","Tablo açıklaması","detail-label"),node("p",source.table_comment));
    card.append(node("p","Veri tipi: "+(source.data_type||"Belirtilmemiş"),"muted small"));
    if((candidate.evidence||[]).length){const list=node("ul");candidate.evidence.forEach(item=>list.append(node("li",item)));card.append(node("span","Kanıt","detail-label"),list);}
    if((candidate.differences||[]).length){const list=node("ul");candidate.differences.forEach(item=>list.append(node("li",item.text||[item.dimension,item.requested,item.actual].filter(Boolean).join(" · "))));card.append(node("span","Farklar","detail-label"),list);}
    if((candidate.locations||[]).length)card.append(node("p","Ayrıca şu tablolarda: "+candidate.locations.join(", "),"muted small"));
    const advice=!rejected&&row.model_review?.assessments?.find(a=>a.catalog_id===candidate.catalog_id);
    if(advice){card.append(node("span","LLM yorumu · uzman incelemesi gerekir","detail-label"),node("p",advice.note),node("p",advice.evidence,"evidence"));}
    return card;
}
function coverageLine(row){const c=row.coverage||{};const sem={full:"tam",partial:"kısmi",none:"yok"}[c.semantic_scope]||"—";const parts=["Katalog "+(c.catalog_complete?"tam":"eksik"),"Anlamsal kapsam "+sem];if(Number.isFinite(c.index_coverage))parts.push("İndeks %"+Math.round(c.index_coverage*100));return parts.join(" · ");}
function verdictBlock(row){
    const box=node("section",undefined,"verdict-block");if(!row.verdict)return box;
    box.append(node("span","Sonuç sınıfı","detail-label"),node("p",row.verdict_label,"vd-line"));
    if(row.verdict_source)box.append(node("span","Kaynak","detail-label"),node("p",row.verdict_source));
    if((row.verdict_reasons||[]).length){const list=node("ul");row.verdict_reasons.forEach(item=>list.append(node("li",item.text||"")));box.append(node("span","Dayanak","detail-label"),list);}
    const d=row.derivation;
    if(d){box.append(node("span","Türetme yöntemi","detail-label"),node("p",row.derivation_method||""));const list=node("ul");for(const c of d.components||[]){list.append(node("li",({numerator:"Pay",denominator:"Payda"}[c.role]||"")+": "+(c.name||"")+(c.window_label?" ("+c.window_label+")":"")+" · "+(c.state_label||"")));}box.append(list);}
    if((row.tier_trace||[]).length){const list=node("ul");for(const t of row.tier_trace){list.append(node("li",[({numerator:"Pay",denominator:"Payda"}[t.role]||""),t.tier,(t.schemas||[]).join(", "),t.row_status_label,t.top,t.meaning_label,t.scope_label?"Anlamsal kapsam "+t.scope_label:""].filter(Boolean).join(" · ")));}box.append(node("span","Katman izi","detail-label"),list);}
    return box;
}
function showDetail(row){$("detail-title").textContent=row.request.name;const content=$("detail-content");content.replaceChildren(node("p",row.request.description||""));if(row.request.sheet||row.request.excel_row)content.append(node("p",(row.request.sheet||"")+" · Satır "+(row.request.excel_row||""),"muted small"));content.append(node("span",row.row_status_label,"badge rs-"+row.row_status),node("p",coverageLine(row),"muted small"),verdictBlock(row));if(row.model_changed)content.append(node("p","Bu satır önceki bir çalışmadan sonra model veya indeks değiştiği için yeniden değerlendirilmelidir.","notice"));if((row.retrieval_warnings||[]).length){const warnings=node("div",undefined,"scope-note retrieval-warnings");warnings.append(node("strong","Arama kapsamı ve sınırları"));for(const warning of row.retrieval_warnings)warnings.append(node("p",warning));content.append(warnings);}const reasons=node("ul");[...(row.reason_texts||[]),...(row.errors||[])].forEach(reason=>reasons.append(node("li",reason)));const notes=node("details");notes.append(node("summary","İnceleme notları ("+reasons.children.length+")"),reasons);content.append(notes);const candidates=(row.candidates||[]).slice(0,3);content.append(node("h3",candidates.length+" kaynak adayı"));candidates.forEach((candidate,index)=>content.append(candidateCard(candidate,index,row,false)));const rejected=row.rejected_candidates||[];if(rejected.length){const box=node("details");box.append(node("summary","Elenen yakın adaylar ("+rejected.length+")"));rejected.forEach((candidate,index)=>box.append(candidateCard(candidate,index,row,true)));content.append(box);}$("detail-dialog").showModal();}
$("close-detail").addEventListener("click",()=>$("detail-dialog").close());


const indexLabels={idle:"Henüz başlatılmadı",running:"İndeksleniyor",stopped:"Durduruldu",failed:"Hata ile durdu",done:"Tamamlandı"};
function indexVisible(){return $("use-models").checked&&!!state.connection&&!$("workspace").hidden;}
function renderIndex(s){
    state.index=s||{state:"idle"};const st=state.index.state||"idle";
    $("index-state").textContent="Durum: "+(indexLabels[st]||st);
    const total=state.index.total||0,done=state.index.done||0;
    if(total>0){$("index-progress").max=total;$("index-progress").value=done;}else $("index-progress").value=0;
    const parts=[];if(total>0)parts.push(done+" / "+total+" kolon");
    if(Number.isFinite(state.index.index_coverage))parts.push("Kapsam %"+Math.round(state.index.index_coverage*100));
    if(st==="running"&&Number.isFinite(state.index.eta_s))parts.push("Tahmini kalan: "+duration(state.index.eta_s));
    if(state.index.failures)parts.push(state.index.failures+" hata");
    if(state.index.message)parts.push(state.index.message);
    $("index-detail").textContent=parts.join(" · ");
    $("index-start").disabled=st==="running"||!state.schemas.size||!state.connection;$("index-stop").disabled=st!=="running";updateScope();
}
async function indexStatus(){try{const s=await api("/api/index/status");renderIndex(s);return s;}catch{return null;}}
function updateIndexPanel(){const visible=indexVisible();$("index-panel").hidden=!visible;if(visible)indexStatus();}
async function startIndex(){renderIndex(await api("/api/index/start",{schemas:[...state.schemas],settings:settings()}));}
async function autoStartIndex(){if(!$("use-models").checked||!state.schemas.size||state.modelVerified.embedding!==modelSignature("embedding"))return;const s=await indexStatus();if(s&&["idle","stopped"].includes(s.state||"idle")){try{await startIndex();}catch(error){notice(error.message);}}}
$("index-start").addEventListener("click",event=>action(event.currentTarget,startIndex));
$("index-stop").addEventListener("click",event=>action(event.currentTarget,async()=>renderIndex(await api("/api/index/stop",{}))));
$("catalog-refresh").addEventListener("click",event=>action(event.currentTarget,async()=>{const r=await api("/api/catalog/refresh",{schemas:[...state.schemas]});notice("Katalog yenilendi · "+r.columns+" kolon.",true);}));
setInterval(()=>{if(indexVisible()&&!document.hidden)indexStatus();},3000);
async function reconnectLatest(){
    try{const latest=await api("/api/jobs/latest");if(!latest||!latest.id)return;attachJob(latest.id);state.rendered=false;page("results");$("progress-card").hidden=false;poll();}catch{}
}
const diagnosticNames={catalog:"Analiz için katalog taraması",oracle:"Oracle bağlantısı",schemas:"Seçili şemalar",llm:"LLM",embedding:"Embedding"};
const diagnosticStatus={passed:"Başarılı",failed:"Başarısız",skipped:"Çalıştırılmadı",unsupported:"Desteklenmiyor",running:"Çalışıyor"};
function renderDiagnostics(open=true,activeName=null){
    const panel=$("diagnostics-panel"),container=$("diagnostics-reports");panel.hidden=!Object.keys(state.reports).length;if(open)panel.open=true;container.replaceChildren();
    activeName=activeName||Object.keys(state.reports).pop();
    for(const [name,report] of Object.entries(state.reports)){
        const section=node("details",undefined,"diagnostic-report");section.open=name===activeName;
        const summary=node("summary");summary.append(node("span",diagnosticNames[name]||name),node("span",report.running?"Çalışıyor":report.ok?"Başarılı":"Tamamlanamadı","badge "+(report.running?"running":report.ok?"passed":"failed")));section.append(summary);
        if(report.checked_at)section.append(node("p","Test zamanı: "+new Date(report.checked_at).toLocaleString("tr-TR"),"muted small"));if(report.message)section.append(node("p",report.message,"muted small"));
        const wrap=node("div",undefined,"table-scroll"),table=node("table"),thead=node("thead"),head=node("tr");
        ["Test adımı","Sonuç","Süre","Açıklama"].forEach(title=>head.append(node("th",title)));thead.append(head);table.append(thead);
        const tbody=node("tbody");
        for(const check of report.checks||[]){const row=node("tr"),status=node("td");status.append(node("span",diagnosticStatus[check.status]||check.status,"badge "+check.status));row.append(node("td",check.name),status,node("td",Number.isFinite(check.elapsed_ms)?(check.elapsed_ms/1000).toFixed(2)+" sn":"—"),node("td",(check.code?check.code+" · ":"")+(check.message||""),"message"));tbody.append(row);}
        table.append(tbody);wrap.append(table);section.append(wrap);container.append(section);
    }
    $("download-diagnostics").disabled=Object.values(state.reports).some(r=>r.running);
}
async function diagnostic(name,run){
    const generation=state.reportGeneration[name]=(state.reportGeneration[name]||0)+1;
    state.reports[name]={running:true,checks:[{name:"Bağlantı testi",status:"running",message:"Yanıt bekleniyor. Adımlar tamamlandığında burada görünecek."}]};renderDiagnostics(true,name);
    const started=performance.now();let data;
    try{data=await run();}catch(error){data={ok:false,error:error.message,checks:[{name:"İstek hazırlama / uygulama erişimi",status:"failed",elapsed_ms:Math.round(performance.now()-started),message:error.message}]};}
    if(state.reportGeneration[name]!==generation)return data;
    state.reports[name]={ok:!!data.ok,checked_at:new Date().toISOString(),message:data.warning||data.message||data.error||"",checks:(data.checks||[]).map(c=>({name:c.name,status:c.status,elapsed_ms:c.elapsed_ms,message:c.message,code:c.code}))};renderDiagnostics(true,name);return data;
}
$("download-diagnostics").addEventListener("click",()=>{
    const report={application:"Risk Feature Workbench",version:document.querySelector('meta[name="app-version"]').content,created_at:new Date().toISOString(),services:state.reports};
    const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:"application/json"}));const link=node("a");link.href=url;link.download="baglanti-tanilama.json";link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});

addVariable(false);updateScope();reconnectLatest();const initialConnectionGeneration=state.reportGeneration.oracle;
api("/api/connection").then(data=>{if(state.reportGeneration.oracle===initialConnectionGeneration){showConnection(data);if(data.ok)return autoEmbedding();}}).catch(error=>{if(state.reportGeneration.oracle===initialConnectionGeneration)notice(error.message);});
