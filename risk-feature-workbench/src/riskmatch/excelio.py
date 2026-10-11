"""Excel taşıma katmanı. Girdi metnini korur; hiçbir formülü çalıştırmaz."""
from copy import copy
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata
import zipfile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .errors import InputError

HEADERS = ["Kategori", "Alan Adı", "Alan tipi", "Açıklama", "Halihazırda bildiriliyor mu", "Görüş", "Hazırlama süresi"]

def norm(value):
    value = str(value or "").replace("İ", "i").replace("I", "ı").lower()
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", value.replace("ı", "i")) if not unicodedata.combining(c)).split())

def serial(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value

def _load(path):
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        raise InputError("Yalnız .xlsx desteklenir. Eski .xls dosyasını Excel'de .xlsx olarak kaydedin.")
    try:
        with zipfile.ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist()) > 150_000_000:
                raise InputError("Çalışma kitabı açılmış boyut sınırını aşıyor (150 MB).")
        return load_workbook(path, data_only=False, keep_links=False)
    except InputError:
        raise
    except Exception:
        raise InputError("Excel dosyası okunamadı. Dosya türünü ve erişim iznini kontrol edin.") from None

def read_requests(path, column_mapping=None):
    wb = _load(path)
    requests, seen_ids = [], set()
    workbook_id = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    for sheet in wb:
        selected=column_mapping.get(sheet.title) if column_mapping is not None else None
        if column_mapping is not None and selected is None:
            continue
        header_row = None
        headers = []
        first=selected["header_row"] if selected else 1
        last=first if selected else min(sheet.max_row,30)
        for cells in sheet.iter_rows(min_row=first, max_row=last):
            values = [str(c.value or "") for c in cells]
            if selected or norm("Alan Adı") in [norm(v) for v in values]:
                headers, header_row = values, cells[0].row
                break
        if header_row is None:
            if any(c.value is not None for row in sheet for c in row):
                # Surface every unparsed nonempty row; never silently claim whole-input completion.
                for row in sheet:
                    if any(c.value is not None for c in row):
                        rid = hashlib.sha256(f"{workbook_id}|{sheet.title}|{row[0].row}".encode()).hexdigest()[:24]
                        requests.append({"row_id":rid,"sheet":sheet.title,"excel_row":row[0].row,"name":"","description":"","data_type":"","category":"","original":{c.column_letter:serial(c.value) for c in row},"input_errors":["Alan Adı başlığı bulunamadı; satır incelenemedi."]})
            continue
        normalized = [norm(v) for v in headers]
        missing = [h for h in ("Alan Adı","Açıklama") if norm(h) not in normalized]
        duplicates = {h for h in normalized if h and normalized.count(h) > 1}
        mapping = {h:i for i,h in enumerate(normalized)}
        if selected:
            field_names={"name":"Alan Adı","description":"Açıklama","category":"Kategori","data_type":"Alan tipi","reported":"Halihazırda bildiriliyor mu","row_id":"Talep Kimliği"}
            mapping={norm(field_names[key]):index-1 for key,index in selected["columns"].items() if key in field_names and index is not None}
            missing=[h for h in ("Alan Adı","Açıklama") if norm(h) not in mapping]
            duplicates=set()  # Explicit column positions resolve duplicate header labels.
        for cells in sheet.iter_rows(min_row=header_row+1):
            values = [serial(c.value) for c in cells]
            if all(v is None for v in values):
                continue
            original = {(h if normalized.count(norm(h)) == 1 else f"{h} [Sütun {i+1}]") or f"Sütun {i+1}": values[i] for i,h in enumerate(headers)}
            def get(h):
                return values[mapping[norm(h)]] if norm(h) in mapping else None
            existing_id = get("Talep Kimliği")
            rid = str(existing_id) if existing_id else hashlib.sha256(f"{workbook_id}|{sheet.title}|{cells[0].row}".encode()).hexdigest()[:24]
            errors = []
            if missing:
                errors.append("Eksik başlıklar: " + ", ".join(missing))
            if duplicates:
                errors.append("Yinelenen sütun başlıkları var; otomatik yorum engellendi.")
            if rid in seen_ids:
                rid = hashlib.sha256(f"{rid}|{sheet.title}|{cells[0].row}".encode()).hexdigest()[:24]
                errors.append("Yinelenen Talep Kimliği; yeni kimlik atandı.")
            seen_ids.add(rid)
            name = str(get("Alan Adı") or "")
            if not name.strip():
                errors.append("Alan adı boş.")
            requests.append({"row_id":rid,"sheet":sheet.title,"excel_row":cells[0].row,"name":name,"description":str(get("Açıklama") or ""),"data_type":str(get("Alan tipi") or ""),"category":str(get("Kategori") or ""),"reported":str(get(HEADERS[4]) or ""),"original":original,"input_errors":errors,"name_column":mapping.get(norm("Alan Adı"),1)+1})
    wb.close()
    if not requests:
        raise InputError("Çalışma kitabında incelenecek satır bulunamadı.")
    # Only literal division and the documented zero guard. All other Excel expressions remain unresolved.
    addresses = {(r["sheet"], r["excel_row"]):r for r in requests}
    for r in requests:
        text = r["description"].strip()
        if not text.startswith("="):
            continue
        plain = re.fullmatch(r"=\s*(\$?[A-Z]+\$?\d+)\s*/\s*(\$?[A-Z]+\$?\d+)\s*",text,re.I)
        guarded = re.fullmatch(r"=IF\(\s*(\$?[A-Z]+\$?\d+)\s*=\s*0\s*[,;]\s*(-?\d+(?:\.\d+)?)\s*[,;]\s*(\$?[A-Z]+\$?\d+)\s*/\s*(\$?[A-Z]+\$?\d+)\s*\)",text,re.I)
        refs = (plain.group(1),plain.group(2)) if plain else (guarded.group(3),guarded.group(4)) if guarded and guarded.group(1).replace("$","").upper()==guarded.group(4).replace("$","").upper() else None
        if refs:
            targets=[]
            for ref in refs:
                m = re.fullmatch(r"\$?([A-Z]+)\$?(\d+)",ref,re.I)
                target = addresses.get((r["sheet"],int(m.group(2))))
                if not target or m.group(1).upper()!=get_column_letter(target["name_column"]):
                    targets=[]
                    break
                targets.append(target["name"])
            if len(targets)==2:
                r["description_for_parse"] = f"{targets[0]} / {targets[1]}" + (f"; payda sıfırsa {guarded.group(2)}." if guarded else "")
                r["formula_origin"] = "excel_cell_reference"
                continue
        r["input_errors"].append("Desteklenmeyen veya çözümlenemeyen Excel formülü; çalıştırılmadı.")
    return requests

def _cell(sheet, row, col, value):
    if isinstance(value, (dict,list,tuple)):
        value=json.dumps(value,ensure_ascii=False,default=str)
    cell=sheet.cell(row,col,value)
    # Force every text to an OOXML string. Keeps exact original text without activating = or DDE.
    if isinstance(value,str):
        cell.data_type="s"
    cell.alignment=Alignment(vertical="top",wrap_text=True)
    return cell

def _table(wb,name,headers,rows):
    ws=wb.create_sheet(name)
    for col,h in enumerate(headers,1):
        cell=_cell(ws,1,col,h)
        cell.fill=PatternFill("solid",fgColor="142C47")
        cell.font=Font(color="FFFFFF",bold=True,size=11)
        ws.column_dimensions[get_column_letter(col)].width=min(54,max(18,len(h)+4))
    ws.row_dimensions[1].height=34
    preferred={"Açıklama":64,"Gerekçe":64,"Satır hataları":54,"Tam kaynak yolu":72,"Boyut bazında kanıt, çelişki ve eksik":48,"Talebin kaynak metni":72,"Katalog kaynak metni":72,"Model yorumu (doğrulanmış karar değil)":64,"Modelin alıntıladığı kaynak":64,"Bilgi":36,"Değer":100}
    for col,h in enumerate(headers,1):
        if h in preferred:ws.column_dimensions[get_column_letter(col)].width=preferred[h]
    for rowno,values in enumerate(rows,2):
        for col,value in enumerate(values,1):
            c=_cell(ws,rowno,col,value)
            c.font=Font(name="Calibri",size=11,color="1E293B")
            if rowno%2==0:
                c.fill=PatternFill("solid",fgColor="F1F5F9")
        lines=1
        for col,value in enumerate(values,1):
            text=json.dumps(value,ensure_ascii=False,default=str) if isinstance(value,(dict,list,tuple)) else str(value or "")
            width=ws.column_dimensions[get_column_letter(col)].width
            lines=max(lines,sum(max(1,math.ceil(len(line)/max(1,width-3))) for line in text.split("\n")))
        ws.row_dimensions[rowno].height=min(409,max(44,16*lines+10))
    ws.freeze_panes="C2"
    ws.auto_filter.ref=ws.dimensions
    return ws

LABELS={"supported":"Metadata ile destekleniyor","insufficient":"Kritik bilgi eksik","conflict":"İş tanımı çelişiyor","not_found_in_scope":"Taranan kapsamda eşleşme bulunamadı","incomplete":"İnceleme tamamlanamadı","present":"Aynı fiziksel ad var","absent_in_scope":"Tamamlanan kapsamda aynı ad yok","unknown":"Ad yokluğu doğrulanamadı","not_evaluated":"Üretim değerlendirilmedi","candidate":"Metriklerden üretim adayı","source_only":"Yalnız hareket kaynağı adayı"}
DIMENSION_LABELS={"entity":"Varlık","grain":"Kayıt seviyesi","concept":"İş kavramı","transaction":"İşlem türü","direction":"İşlem yönü","measure":"Ölçüm","aggregation":"Toplulaştırma","grouping":"Gruplama seviyesi","window":"Zaman penceresi","window_bounds":"Pencere sınırları","reference_date":"Referans tarihi","person_key":"Tekil kişi anahtarı","unit":"Birim","codes":"Kod listesi","zero_policy":"Sıfır payda kuralı","null_policy":"NULL kuralı","numerator":"Pay","denominator":"Payda","business_scope":"İş kapsamı"}
CHECK_LABELS={"compatible":"Uyumlu","conflict":"Çelişkili","missing":"Bilgi eksik"}
REASON_LABELS={"unparsed_description_qualifier":"Açıklamada otomatik yorumlanamayan iş nitelemeleri var.","unparsed_formula_qualifier":"Formülde otomatik yorumlanamayan ek koşul var.","no_verified_derivation":"Üretim için doğrulanmış yeterli kanıt bulunamadı.","ready_feature_supported":"Hazır alan metadata ile destekleniyor.","duplicate_name":"Aynı alan adı birden fazla talepte geçiyor.","ambiguous_ratio_name":"Alan adından oran yapısı kesin çıkarılamıyor.","unsupported_formula":"Formül izin verilen basit bölme yapısında değil.","cycle":"Bağımlılıklarda döngü var.","unresolved_operands":"Pay veya payda talebi kesin olarak çözümlenemedi.","movement_sources_only; aggregation_not_verified":"Hareket kaynağı adayı var; toplulaştırmanın doğruluğu kanıtlanmadı.","verified_operands; expert_review_required":"Hazır metrikler ve bağlantı koşulları destekleniyor; uzman kararı gerekli.","cross_object_relationship_unverified":"Farklı nesneler arasındaki ilişki doğrulanmadı."}

def reason_text(value):
    value=str(value)
    if value in REASON_LABELS:return REASON_LABELS[value]
    if ":" in value:
        prefix,rest=value.split(":",1)
        if prefix in {"request","candidate"}:
            return ("Talep: " if prefix=="request" else "Aday: ")+reason_text(rest)
        labels={"missing":"Bilgi eksik","conflict":"Çelişki","missing_definition":"Bağımlılık tanımı bulunamadı","ambiguous_reference":"Bağımlılık adı birden fazla satıra karşılık geliyor","operand_not_ready":"Bağımlı metrik doğrulanmadı","operand_keys_unknown":"Bağımlı metriğin anahtarı bilinmiyor","operand_graph_issue":"Bağımlı talepte çözülmemiş bağımlılık var","operand_conflict":"Bağımlı metrikle çelişki","operands_conflict":"Metrikler arasında çelişki"}
        if prefix in labels:return labels[prefix]+": "+DIMENSION_LABELS.get(rest,rest)
    return value

def write_results(input_path, output_path, requests, results, run_info):
    if Path(input_path).resolve()==Path(output_path).resolve():
        raise InputError("Sonuç dosyası giriş dosyasının üzerine yazılamaz.")
    wb=Workbook()
    wb.remove(wb.active)
    original_headers=list(dict.fromkeys(k for r in requests for k in r["original"] if norm(k)!=norm("Talep Kimliği")))
    headers=["Talep Kimliği","Kaynak Sayfa","Kaynak Satır"]+original_headers+["Olası şema","Olası tablo","Olası kolon","Katalog kolon açıklaması","Fiziksel ad kontrolü","Semantik uyum","Üretim ihtimali","Gerekçe","Satır hataları","Bağımlılıklar","Uzman kararı"]
    result_map={r["row_id"]:r for r in results}
    rows=[]
    for req in requests:
        r=result_map[req["row_id"]]
        best=next((c for c in r["candidates"] if c["status"]=="supported"),r["candidates"][0] if r["candidates"] else {})
        source=best.get("source",{})
        rows.append([req["row_id"],req["sheet"],req["excel_row"]]+[req["original"].get(h) for h in original_headers]+[source.get(k,"") for k in ("schema","object","column","column_comment")]+[LABELS.get(r["physical_name"],r["physical_name"]),LABELS.get(r["semantic_status"],r["semantic_status"]),LABELS.get(r["production_status"],r["production_status"]),"\n".join(reason_text(v) for v in r["reasons"]),"\n".join(r["errors"]),r.get("dependencies",[]),r.get("expert_decision","")])
    ws=_table(wb,"Sonuçlar",headers,rows)
    for h in ("Açıklama","Gerekçe","Satır hataları"):
        if h in headers:
            ws.column_dimensions[get_column_letter(headers.index(h)+1)].width=64
    expert_col=get_column_letter(len(headers))
    dv=DataValidation(type="list",formula1='"İncelenmedi,Onaylandı,Reddedildi,Ek bilgi bekleniyor"')
    ws.add_data_validation(dv)
    if rows:
        dv.add(f"{expert_col}2:{expert_col}{len(rows)+1}")
    candidates=[]
    evidence_rows=[]
    for r in results:
        reviews={a["catalog_id"]:a for a in r.get("model_review",{}).get("assessments",[])}
        for c in r["candidates"]:
            advice=reviews.get(c["catalog_id"],{})
            candidates.append([r["row_id"],c["catalog_id"],c.get("path","")]+[c.get("source",{}).get(k,"") for k in ("schema","object","column","data_type","column_comment","table_comment")]+[LABELS.get(c["status"],c["status"]),c.get("score",0),", ".join(c.get("retrieval",[])),"\n".join(f"{DIMENSION_LABELS.get(x['dimension'],x['dimension'])}: {CHECK_LABELS.get(x['status'],x['status'])}" for x in c.get("checks",[])),"\n".join(reason_text(v) for v in c.get("reasons",[])),advice.get("note",""),advice.get("evidence","")])
            for check in c.get("checks",[]):
                proof=check.get("evidence",{})
                evidence_rows.append([r["row_id"],c["catalog_id"],DIMENSION_LABELS.get(check["dimension"],check["dimension"]),CHECK_LABELS.get(check["status"],check["status"]),check.get("requested"),check.get("actual"),proof.get("request",""),proof.get("candidate",""),proof.get("request_source",""),proof.get("candidate_source","")])
    _table(wb,"Adaylar",["Talep Kimliği","Katalog Kimliği","Tam kaynak yolu","Şema","Tablo","Kolon","Veri tipi","Kolon açıklaması","Tablo açıklaması","Semantik durum","Arama skoru (olasılık değil)","Arama yöntemleri","Boyut bazında kanıt, çelişki ve eksik","Gerekçe","Model yorumu (doğrulanmış karar değil)","Modelin alıntıladığı kaynak"],candidates)
    _table(wb,"Çalışma bilgisi",["Bilgi","Değer"],list(run_info.items()))
    _table(wb,"Kanıtlar",["Talep Kimliği","Katalog Kimliği","Boyut","Uyum","Talep değeri","Katalog değeri","Talebin kaynak metni","Katalog kaynak metni","Talep kanıt türü","Katalog kanıt türü"],evidence_rows)
    # Preserve all original rows, sheets, ordering, formulas-as-text, styles, and supplied user fields.
    source=_load(input_path)
    for i,original in enumerate(source,1):
        name=f"Girdi {i} {original.title}"[:31]
        copied=wb.create_sheet(name)
        for row in original:
            for c in row:
                dest=_cell(copied,c.row,c.column,c.value)
                if c.has_style:
                    # Style indices belong to their workbook, so copy actual components.
                    for part in ("font","fill","border","alignment","protection"):
                        setattr(dest,part,copy(getattr(c,part)))
                    dest.number_format=c.number_format
                if c.comment:
                    dest.comment=copy(c.comment)
        for key,value in original.column_dimensions.items():
            target=copied.column_dimensions[key]
            for part in ("width","hidden","outlineLevel","min","max"):
                setattr(target,part,getattr(value,part))
        for key,value in original.row_dimensions.items():
            target=copied.row_dimensions[key]
            for part in ("height","hidden","outlineLevel"):
                setattr(target,part,getattr(value,part))
        for merged in original.merged_cells.ranges:
            copied.merge_cells(str(merged))
        copied.freeze_panes=original.freeze_panes
    source.close()
    destination=Path(output_path)
    destination.parent.mkdir(parents=True,exist_ok=True)
    temp=destination.with_name(destination.stem+".writing.xlsx")
    wb.save(temp)
    wb.close()
    temp.replace(destination)
    return str(destination)

def prepare_requests(input_path,output_path,requests=None):
    """Create a separate editable workbook carrying immutable row IDs.

    Existing IDs survive content edits and sorting. Source input is never changed.
    """
    if Path(input_path).resolve()==Path(output_path).resolve():
        raise InputError("Kimlikli kopya için farklı bir dosya adı kullanın.")
    requests=requests or read_requests(input_path)
    wb=_load(input_path)
    for sheet in wb:
        rows={r["excel_row"]:r["row_id"] for r in requests if r["sheet"]==sheet.title}
        if not rows:continue
        for header_row in range(1,min(sheet.max_row,30)+1):
            values=[norm(c.value) for c in sheet[header_row]]
            if norm("Alan Adı") not in values:continue
            id_col=values.index(norm("Talep Kimliği"))+1 if norm("Talep Kimliği") in values else sheet.max_column+1
            _cell(sheet,header_row,id_col,"Talep Kimliği")
            sheet.column_dimensions[get_column_letter(id_col)].width=28
            for row,rid in rows.items():
                _cell(sheet,row,id_col,rid)
            break
        for row in sheet:
            for c in row:
                if c.data_type=="f":c.data_type="s"
    output_path=Path(output_path)
    output_path.parent.mkdir(parents=True,exist_ok=True)
    temp=output_path.with_name(output_path.stem+".writing.xlsx")
    wb.save(temp)
    wb.close()
    temp.replace(output_path)
    return str(output_path)
