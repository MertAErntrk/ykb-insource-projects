"use strict";
const $=id=>document.getElementById(id);
const state={upload:null,job:null,results:[],mapping:{},running:false,connection:null,schemas:new Set(),nextVariable:0,reports:{},reportGeneration:{},schemaTesting:false,modelVerified:{},discovered:{}};
const keys={llm:["base_url","endpoint","model","model_version","contract","request_template","response_path","models_endpoint","ca_file","api_key"],embedding:["base_url","endpoint","model","model_version","model_selection","max_batch","dimensions","request_template","response_path","models_endpoint","ca_file","api_key"]};
const fields={name:"Alan adı *",description:"Açıklama *",category:"Kategori",data_type:"Alan tipi",reported:"Halihazırda bildiriliyor mu",row_id:"Talep kimliği (varsa)"};
function node(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;}
function notice(message,success=false){const el=$("notice");el.textContent=message;el.className="notice"+(success?" success":"");el.hidden=false;el.focus();}
function page(name){document.querySelectorAll(".page").forEach(el=>el.hidden=el.id!==name);document.querySelectorAll(".nav-item").forEach(el=>el.classList.toggle("active",el.dataset.page===name));$("page-label").textContent={workspace:"Değişken inceleme",connections:"Model bağlantıları",results:"Sonuçlar"}[name];$("notice").hidden=true;updateScope();}
document.querySelectorAll("[data-page]").forEach(el=>el.addEventListener("click",()=>page(el.dataset.page)));
async function api(path,data){const options=data===undefined?{}:{method:"POST",headers:{"X-CSRF-Token":document.querySelector('meta[name="csrf-token"]').content},body:data instanceof FormData?data:JSON.stringify(data)};if(data!==undefined&&!(data instanceof FormData))options.headers["Content-Type"]="application/json";let response;try{response=await fetch(path,options);}catch{throw new Error("Uygulamaya ulaşılamıyor. Başlatma penceresinin açık olduğunu kontrol edin.");}const result=await response.json();if(!response.ok)throw new Error(result.error||"İşlem tamamlanamadı.");return result;}
async function action(button,fn){if(button.disabled)return;button.disabled=true;const previous=button.textContent;button.textContent="İşleniyor…";$("notice").hidden=true;try{await fn();}catch(error){notice(error.message);}finally{button.disabled=false;button.textContent=previous;updateScope();}}
function settings(force=false,only=null){const result={mode:"real",use_models:$("use-models").checked,search_mode:$("search-mode").value,llm_review:$("llm-review").checked};if(!result.use_models&&!force)return result;for(const [section,items] of Object.entries(keys)){if(only&&section!==only)continue;if(!force&&section==="llm"&&!result.llm_review)continue;result[section]={};for(const key of items){let value=$(section+"-"+key).value;if(key==="request_template"){if(value.trim()){try{value=JSON.parse(value);}catch{throw new Error((section==="llm"?"LLM":"Embedding")+" istek şablonu geçerli JSON olmalı.");}}else continue;}if(key==="dimensions"||key==="max_batch")value=Number(value);result[section][key]=value;}}return result;}
function updateScope(){
    const dbReady=!!state.connection&&state.schemas.size>0;
    const enabled=$("use-models").checked,review=enabled&&$("llm-review").checked;
    const modelsReady=!enabled||["embedding",...(review?["llm"]:[])].every(name=>state.modelVerified[name]===modelSignature(name));
    const ready=dbReady&&modelsReady,selected=[...state.schemas];
    $("test-schemas").disabled=!dbReady||state.schemaTesting;$("scope-name").textContent=state.connection?"EDWLIVE":"Bağlantı bekleniyor";
    $("scope-detail").textContent=selected.length?"Taranacak şemalar: "+selected.join(", "):"Bağlanıp açılır listeden en az bir şema seçin.";
    $("schema-summary").textContent=selected.length?selected.length+" şema seçildi · değiştir ▾":"Taranan şemaları seçin";
    $("selected-schemas").textContent=selected.length?selected.join(", "):"Henüz şema seçilmedi.";
    $("llm-review").disabled=!enabled||state.running;$("search-mode").disabled=state.running;$("use-models").disabled=state.running;
    const fast=$("search-mode").value==="fast";
    $("search-mode-help").textContent=fast?"Önce ad ve açıklamayla adaylar bulunur. Değişken başına en fazla 24 aday, analiz başına en fazla 256 farklı kolon için embedding kullanılır. Sınırdan sonra ad/açıklama araması sürer. Kelime olarak benzemeyen adaylar kaçabilir.":enabled?"Okunabilen katalogdaki tüm kolonlar için embedding hazırlanır. Büyük şemalarda çok sayıda servis çağrısı ve uzun bekleme gerekir; ilk sonuç hazırlık bitince görünür.":"Okunabilen katalogda ad ve açıklama eşleşmeleri aranır. Embedding desteği kapalı.";
    $("mode-note").textContent=!enabled?"Oracle kataloğunda alan adı ve açıklama araması yapılır.":(fast?"Hızlı aday araması ve sınırlı embedding karşılaştırması.":"Tüm okunabilen katalogda anlamsal arama.")+(review?" Her değişken için ayrıca LLM yorumu istenir.":" LLM yorumu kapalı.");
    $("manual-run").disabled=!ready||state.running;$("run-button").disabled=!ready||!state.upload||!Object.keys(state.mapping).length||state.running;
    document.querySelectorAll(".readiness").forEach(el=>el.textContent=state.running?"Analiz devam ediyor; sonuçlardan ilerlemeyi izleyebilir veya durdurabilirsiniz.":!dbReady?"Aramak için bağlanıp en az bir şema seçin.":!modelsReady?(review?"LLM ve embedding testlerini başarıyla tamamlayın.":"Embedding testini başarıyla tamamlayın veya embedding desteğini kapatın."):selected.length+" şemada aranacak.");
}
function showConnection(data){invalidateDiagnostics(["oracle","schemas"]);state.connection=data.ok?data:null;state.schemas.clear();$("login-form").hidden=data.ok;$("connected-area").hidden=!data.ok;$("connection-badge").textContent=data.ok?"Bağlandı · "+data.db_name:"Bağlantı bekleniyor";$("schema-list").replaceChildren();$("oracle-probe-status").textContent=data.ok?(data.schemas.length?data.schemas.length+" şema adayı listelendi. Seçtiklerinizin katalog erişimini test edebilirsiniz.":"Şema adayı bulunamadı. Test ayrıntılarını kontrol edin."):"";if(data.warning)$("oracle-probe-status").textContent+=" "+data.warning;if(data.ok){for(const schema of data.schemas){const label=node("label");const input=node("input");input.type="checkbox";input.value=schema;input.addEventListener("change",()=>{if(input.checked){if(state.schemas.size>=30){input.checked=false;notice("En fazla 30 şema seçebilirsiniz.");return;}state.schemas.add(schema);}else state.schemas.delete(schema);invalidateDiagnostics(["schemas"]);updateScope();});label.append(input,node("span",schema));$("schema-list").append(label);}if(data.truncated)notice("İlk 1.000 şema adayı listelendi. Görünen şemalardan kapsamınızı seçin.");}updateScope();}
$("login-form").addEventListener("submit",event=>{event.preventDefault();action($("probe-oracle"),async()=>{showConnection({ok:false});const data=await diagnostic("oracle",()=>api("/api/connect",{user:$("oracle-user").value,password:$("oracle-password").value}));if(!data.ok){notice(data.error||"Oracle testindeki başarısız adımı inceleyin.");return;}$("oracle-password").value="";const oracleReport=state.reports.oracle;showConnection(data);state.reports.oracle=oracleReport;renderDiagnostics();$("schema-picker").open=true;$("schema-search").value="";$("schema-search").focus();});});
$("test-schemas").addEventListener("click",event=>action(event.currentTarget,async()=>{state.schemaTesting=true;try{await diagnostic("schemas",()=>api("/api/diagnostics/oracle-schemas",{schemas:[...state.schemas]}));}finally{state.schemaTesting=false;}}));
$("disconnect").addEventListener("click",event=>action(event.currentTarget,async()=>{await api("/api/disconnect",{});showConnection({ok:false});$("oracle-user").focus();}));
$("schema-done").addEventListener("click",()=>{$("schema-picker").open=false;$("schema-summary").focus();});
$("schema-search").addEventListener("input",event=>{for(const label of $("schema-list").children)label.hidden=!label.textContent.toLocaleLowerCase("tr").includes(event.target.value.toLocaleLowerCase("tr"));});
$("use-models").addEventListener("change",updateScope);
$("llm-review").addEventListener("change",updateScope);
$("search-mode").addEventListener("change",updateScope);
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
document.querySelectorAll("[data-probe-model]").forEach(button=>button.addEventListener("click",()=>action(button,async()=>{
    const name=button.dataset.probeModel,inputs=keys[name].map(key=>$(name+"-"+key));
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
})));
$("embedding-preset").addEventListener("change",event=>{delete state.modelVerified.embedding;invalidateDiagnostics(["embedding"]);$("embedding-probe-status").textContent="İstek biçimi değişti; yeniden test edin.";updateScope();const value=event.target.value;if(value==="openai"){$("embedding-request_template").value=JSON.stringify($("embedding-model").value.trim()?{model:"{model}",input:"{texts}"}:{input:"{texts}"},null,2);$("embedding-response_path").value="data[*].embedding";}if(value==="tei"){$("embedding-request_template").value=JSON.stringify({inputs:"{texts}"},null,2);$("embedding-response_path").value="$";}});
document.querySelectorAll("[data-input]").forEach(button=>button.addEventListener("click",()=>{const manual=button.dataset.input==="manual";$("manual-form").hidden=!manual;$("excel-panel").hidden=manual;document.querySelectorAll("[data-input]").forEach(el=>{const active=el===button;el.classList.toggle("active",active);el.setAttribute("aria-pressed",String(active));});}));
function renumberVariables(){const rows=[...$("manual-rows").children];$("manual-count").textContent=rows.length+" değişken";rows.forEach((row,i)=>{row.querySelector("strong").textContent="Değişken "+(i+1);const remove=row.querySelector("button");remove.disabled=rows.length===1;remove.setAttribute("aria-label","Değişken "+(i+1)+" sil");});$("add-variable").disabled=rows.length>=100;}
function addVariable(focus=true){if($("manual-rows").children.length>=100)return;const id=++state.nextVariable;const row=node("section",undefined,"variable-row");const head=node("div",undefined,"card-title");const remove=node("button","Sil","text-button remove-variable");remove.type="button";remove.addEventListener("click",()=>{row.remove();renumberVariables();$("add-variable").focus();});head.append(node("strong"),remove);row.append(head);for(const [field,title,tag,max] of [["name","Alan / kolon adı","input",256],["description","Açıklama","textarea",8000]]){const label=node("label",title);const input=node(tag);input.id="variable-"+id+"-"+field;input.name=field;input.required=true;input.maxLength=max;input.dataset.field=field;if(tag==="textarea"){input.rows=3;input.placeholder="Bu alan neyi ifade ediyor? Dönem, ölçüm ve hesaplama bilgilerini ekleyin.";}else input.placeholder="Aradığınız alanın adı";label.append(input);row.append(label);}$("manual-rows").append(row);renumberVariables();if(focus)row.querySelector("input").focus();}
$("add-variable").addEventListener("click",()=>addVariable());
async function startAnalysis(payload){const job=await api("/api/analyze",{...payload,schemas:[...state.schemas],settings:settings()});state.running=true;state.job=job.id;state.results=[];$("result-count").textContent="0";$("cancel-analysis").hidden=false;$("cancel-analysis").disabled=false;$("cancel-analysis").textContent="Durdur";$("progress-title").textContent="Analiz hazırlanıyor";$("progress-detail").textContent="İlk işlem başlatılıyor…";$("progress-time").textContent="";$("failure-card").hidden=true;invalidateDiagnostics(["catalog"]);page("results");$("progress-card").hidden=false;$("empty-results").hidden=true;$("results-body").hidden=true;$("download").hidden=true;poll();}
$("manual-form").addEventListener("submit",event=>{event.preventDefault();action($("manual-run"),async()=>{const variables=[...$("manual-rows").children].map(row=>Object.fromEntries([...row.querySelectorAll("[data-field]")].map(el=>[el.dataset.field,el.value])));await startAnalysis({input_type:"manual",variables});});});
$("run-button").addEventListener("click",event=>action(event.currentTarget,()=>startAnalysis({input_type:"excel",upload_id:state.upload.id,mapping:state.mapping})));
$("excel-file").addEventListener("change",async event=>{const input=event.target,file=input.files[0];state.upload=null;state.mapping={};$("mapping-card").hidden=true;$("file-label").textContent=file?.name||"Excel dosyasını seçin";updateScope();if(!file)return;input.disabled=true;$("upload-status").textContent="Excel okunuyor…";try{if(file.size>12*1024*1024)throw new Error("Excel dosyası en fazla 12 MB olabilir.");const form=new FormData();form.append("file",file);const result=await api("/api/upload",form);state.upload=result;renderMapping(result.sheets);$("mapping-card").hidden=false;$("mapping-details").open=result.sheets.some(s=>!s.suggested.name||!s.suggested.description);mappingSummary();$("upload-status").textContent="Dosya okundu · "+result.sheets.length+" sayfa";}catch(error){$("upload-status").textContent="Dosya okunamadı.";notice(error.message);}finally{input.disabled=false;updateScope();}});
function mappingSummary(){const count=Object.keys(state.mapping).length;$("mapping-summary").textContent=count?count+" sayfa seçildi · sütunlar eşleştirildi":"Alan adı ve açıklama sütunlarını seçin";updateScope();}
function renderMapping(sheets){const container=$("mapping");container.replaceChildren();for(const sheet of sheets){const block=node("section",undefined,"sheet-map");const enable=node("input");enable.type="checkbox";enable.checked=!!sheet.suggested.name&&!!sheet.suggested.description;const label=node("label",undefined,"check");label.append(enable,node("span",sheet.name+" · "+sheet.rows+" satır"));block.append(label);const form=node("div",undefined,"mapping-grid");const headerLabel=node("label","Başlık satırı");const header=node("input");header.type="number";header.min="1";header.max="30";header.value=sheet.header_row;headerLabel.append(header);form.append(headerLabel);const selects={};for(const [key,title] of Object.entries(fields)){const field=node("label",title);const select=node("select");select.setAttribute("aria-label",sheet.name+" · "+title);const blank=node("option","Seçilmedi");blank.value="";select.append(blank);for(const item of sheet.headers){const option=node("option",item.index+" · "+item.label);option.value=item.index;select.append(option);}select.value=sheet.suggested[key]||"";selects[key]=select;field.append(select);form.append(field);}const update=()=>{if(enable.checked)state.mapping[sheet.name]={header_row:Number(header.value),columns:Object.fromEntries(Object.entries(selects).map(([key,el])=>[key,el.value?Number(el.value):null]))};else delete state.mapping[sheet.name];form.querySelectorAll("input,select").forEach(el=>el.disabled=!enable.checked);mappingSummary();};enable.addEventListener("change",update);form.addEventListener("change",update);update();block.append(form);const preview=node("details");preview.append(node("summary","İlk satırları göster"));const wrap=node("div",undefined,"preview");const table=node("table");const head=node("tr");sheet.headers.forEach(h=>head.append(node("th",h.label)));table.append(head);for(const row of sheet.preview){const tr=node("tr");row.forEach(v=>tr.append(node("td",v)));table.append(tr);}wrap.append(table);preview.append(wrap);block.append(preview);container.append(block);}}
function catalogReport(report){
    if(!report)return;
    state.reports.catalog={ok:!!report.ok,checked_at:new Date().toISOString(),message:report.message||(report.errors||[]).join(" "),checks:report.checks||[]};
    renderDiagnostics(false,"catalog");
}
function showJobFailure(job){
    $("progress-card").hidden=true;$("empty-results").hidden=true;$("download").hidden=true;
    $("failure-card").hidden=false;$("failure-title").textContent=job.failure_stage==="catalog"?"Katalog okunamadı; arama başlatılamadı":"Analiz tamamlanamadı";
    $("failure-message").textContent=job.error;
    $("failure-input").textContent=(job.progress?.total||0)+" değişken hazır · "+state.results.length+" değişken incelendi";
    $("results-body").hidden=!state.results.length;
    if(state.results.length)renderResults(job);else{$("result-count").textContent="0";$("result-rows").replaceChildren();}
    catalogReport(job.catalog_diagnostics);
}
function duration(seconds){const value=Math.max(0,Math.floor(Number(seconds)||0));return value>=60?Math.floor(value/60)+" dk "+value%60+" sn":value+" sn";}
function renderProgress(job){
    const progress=job.progress||{},phase=progress.phase,finished=(job.results||[]).length;
    $("progress-title").textContent=job.status==="cancelling"?"Durduruluyor…":job.status==="cancelled"?"Analiz durduruldu":progress.stage;
    const items=Number.isFinite(progress.items_completed)&&Number.isFinite(progress.items_total)&&progress.items_total>0;
    if(items){$("progress").max=progress.items_total;$("progress").value=progress.items_completed;}
    else if(phase==="catalog"||phase==="index"||phase==="embedding")$("progress").removeAttribute("value");
    else{$("progress").max=Math.max(progress.total||1,1);$("progress").value=progress.completed||0;}
    const parts=[finished+" / "+(progress.total||0)+" değişken incelendi"];
    if(phase==="catalog")parts.push("Katalog taranıyor"+(Number.isFinite(progress.records)?" · "+progress.records+" kolon okundu":""));
    if(items)parts.push(progress.items_completed+" / "+progress.items_total+" "+(progress.items_label||"metin"));
    if(progress.variable)parts.push(progress.variable);
    if(job.status==="cancelling")parts.push("Devam eden servis çağrısı bittiğinde duracak. Tamamlanan sonuçlar korunur.");
    if(job.status==="cancelled")parts.push("Kısmi sonuçlar aşağıdadır. Nihai Excel raporu üretilmedi. Girdileriniz korunuyor.");
    parts.push(job.filename);$("progress-detail").textContent=parts.join(" · ");
    $("progress-time").textContent="Geçen süre: "+duration(job.elapsed_seconds)+" · Son işlem güncellemesi: "+duration(job.last_update_seconds)+" önce";
    $("cancel-analysis").disabled=job.status!=="running";$("cancel-analysis").hidden=job.status==="cancelled";
    $("cancel-analysis").textContent=job.status==="cancelling"?"Durduruluyor…":"Durdur";
}
$("cancel-analysis").addEventListener("click",async()=>{
    const button=$("cancel-analysis");button.disabled=true;
    try{await api("/api/jobs/"+state.job+"/cancel",{});button.textContent="Durduruluyor…";$("progress-title").textContent="Durduruluyor…";}
    catch(error){button.disabled=false;notice(error.message);}
});
async function poll(){
    try{
        const job=await api("/api/jobs/"+state.job);
        renderProgress(job);
        const oldCount=state.results.length;state.results=job.results||[];
        if(state.results.length&&(state.results.length!==oldCount||!state.running||!["running","cancelling"].includes(job.status)))renderResults(job);
        if(["running","cancelling"].includes(job.status)){setTimeout(poll,1500);return;}
        state.running=false;updateScope();$("progress-card").hidden=job.status!=="cancelled";
        if(job.status==="failed"){showJobFailure(job);return;}
        if(job.status==="cancelled"){$("download").hidden=true;$("empty-results").hidden=true;if(state.results.length)renderResults(job);return;}
        catalogReport(job.run_info?.["Katalog tanılama"]);renderResults(job);
        $("download").href="/api/jobs/"+job.id+"/download";$("download").hidden=false;
    }catch(error){notice(error.message+" Analiz durumuna yeniden ulaşılmaya çalışılıyor.");if(state.running)setTimeout(poll,3000);}
}
function renderResults(job){
    $("results-body").hidden=false;$("result-count").textContent=state.results.length;
    const metrics=[[state.results.length,"İncelenen değişken"],[state.results.filter(r=>r.candidates.length).length,"Kaynak adayı bulunan"],[state.results.filter(r=>r.physical_name==="present").length,"Aynı fiziksel ad bulunan"],[state.results.filter(r=>r.semantic_status==="supported").length,"İş tanımı desteklenen"]];
    $("metrics").replaceChildren(...metrics.map(([count,label])=>{const el=node("div",undefined,"metric");el.append(node("strong",count),node("span",label));return el;}));
    const info=job.run_info||{},scope=info.Kapsam,mode=info["Arama modu"]||job.search_mode;
    const pieces=[job.status==="complete"?info.Tamamlanma||"Tamamlandı":job.status==="cancelled"?"Durduruldu · Kısmi sonuçlar":job.status==="failed"?"Analiz tamamlanamadı · Kısmi sonuçlar":"Analiz devam ediyor · Kısmi sonuçlar"];
    if(scope)pieces.push(scope.source_database+" / "+scope.container,"Şemalar: "+scope.schemas.join(", "),info["Tarama tamamlandı"]?"Seçili katalog kapsamı tamamlandı.":"Katalog taraması eksik. Alan yokluğu doğrulanamaz.");
    if(mode)pieces.push(mode==="fast"?"Hızlı aday araması · Kelime olarak benzemeyen adaylar kaçabilir.":mode==="full"?"Tüm okunabilen katalogda arama.":String(mode));
    if(job.status!=="complete")pieces.push("Tamamlanan değişkenler gösteriliyor; bütün incelemenin nihai sonucu değildir.");
    pieces.push(...(info.Hatalar||[]));$("run-summary").textContent=pieces.join(" · ");renderRows();
}
function bestCandidate(row){return row.candidates.find(c=>c.status==="supported")||row.candidates[0];}
function renderRows(){const query=$("result-search").value.toLocaleLowerCase("tr"),filter=$("result-filter").value;const rows=state.results.filter(row=>(!filter||row.semantic_status===filter)&&(!query||[row.request.name,row.request.description,...row.candidates.map(c=>c.path)].join(" ").toLocaleLowerCase("tr").includes(query)));$("visible-count").textContent=rows.length+" / "+state.results.length+" sonuç";$("result-rows").replaceChildren(...rows.map(row=>{const tr=node("tr");const first=node("td");first.append(node("strong",row.request.name),node("small",row.request.description.slice(0,160)+(row.request.description.length>160?"…":"")));const source=node("td"),best=bestCandidate(row);if(best){source.append(node("code",[best.source?.schema,best.source?.object,best.source?.column].filter(Boolean).join(".")||best.path),node("small",row.candidates.length+" kaynak adayı"));}else source.textContent="Kanıtlı aday bulunamadı";const physical=node("td",row.labels.physical_name);const semantic=node("td");semantic.append(node("span",row.labels.semantic_status,"badge "+row.semantic_status));if((row.retrieval_warnings||[]).length)semantic.append(node("small","Arama kapsamı notu · Detayı inceleyin","retrieval-note"));const detail=node("td");const button=node("button","Detay →","text-button");button.setAttribute("aria-label",row.request.name+" detayını aç");button.addEventListener("click",()=>showDetail(row));detail.append(button);tr.append(first,source,physical,semantic,detail);return tr;}));if(!rows.length){const tr=node("tr"),td=node("td","Bu filtreye uygun sonuç yok.");td.colSpan=5;tr.append(td);$("result-rows").append(tr);}}
$("result-search").addEventListener("input",renderRows);$("result-filter").addEventListener("change",renderRows);
function showDetail(row){$("detail-title").textContent=row.request.name;const content=$("detail-content");content.replaceChildren(node("p",row.request.description),node("p",row.request.sheet+" · Satır "+row.request.excel_row,"muted small"),node("span",row.labels.semantic_status,"badge "+row.semantic_status),node("p",row.labels.physical_name+" · "+row.labels.production_status,"muted small"));if((row.retrieval_warnings||[]).length){const warnings=node("div",undefined,"scope-note retrieval-warnings");warnings.append(node("strong","Arama kapsamı ve sınırları"));for(const warning of row.retrieval_warnings)warnings.append(node("p",warning));content.append(warnings);}const reasons=node("ul");[...row.reason_texts,...row.errors].forEach(reason=>reasons.append(node("li",reason)));const notes=node("details");notes.append(node("summary","İnceleme notları ("+reasons.children.length+")"),reasons);content.append(notes,node("h3",row.candidates.length+" kaynak adayı"));for(const [index,candidate] of row.candidates.entries()){const card=node("article",undefined,"candidate");card.append(node("span",(index+1)+" · "+candidate.label,"badge "+candidate.status),node("code",candidate.path));const source=candidate.source||{};card.append(node("span","Kolon açıklaması","detail-label"),node("p",source.column_comment||"Katalogda kolon açıklaması yok."));if(source.table_comment)card.append(node("span","Tablo açıklaması","detail-label"),node("p",source.table_comment));card.append(node("p","Veri tipi: "+(source.data_type||"Belirtilmemiş")+" · Arama skoru: "+Number(candidate.score||0).toFixed(3)+" (doğruluk yüzdesi değildir)","muted small"));const list=node("ul");candidate.reason_texts.forEach(reason=>list.append(node("li",reason)));const checks=node("details");checks.append(node("summary","Uyum ve eksik bilgi ayrıntıları"),list);card.append(checks);const proofs=(candidate.checks||[]).filter(check=>check.evidence?.candidate);if(proofs.length){const details=node("details");details.append(node("summary","Kaynak kanıtlarını göster"));for(const proof of proofs){details.append(node("p",proof.dimension+": "+proof.evidence.candidate,"evidence"));}card.append(details);}const advice=row.model_review?.assessments?.find(a=>a.catalog_id===candidate.catalog_id);if(advice){card.append(node("span","LLM yorumu · uzman incelemesi gerekir","detail-label"),node("p",advice.note),node("p",advice.evidence,"evidence"));}content.append(card);}$("detail-dialog").showModal();}
$("close-detail").addEventListener("click",()=>$("detail-dialog").close());


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

addVariable(false);updateScope();const initialConnectionGeneration=state.reportGeneration.oracle;
api("/api/connection").then(data=>{if(state.reportGeneration.oracle===initialConnectionGeneration)showConnection(data);}).catch(error=>{if(state.reportGeneration.oracle===initialConnectionGeneration)notice(error.message);});
