"""Tamamen sentetik katalog ve deterministik vektör simülatörü; Qwen değildir."""
import hashlib
import json
from pathlib import Path
import re
import unicodedata

def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def _record(column,comment,table="APPLICATION_FEATURES",definition=None,**extra):
    r={"source_database":"SYNTHETIC_DB","environment":"DEMO","container":"SYNTHETIC_PDB","schema":"DEMO_RISK","object":table,"column":column,"object_type":"TABLE","data_type":"NUMBER","column_comment":comment,"table_comment":"SENTETİK örnek katalog. Gerçek banka alanı değildir.","captured_at":"2026-10-11T00:00:00+00:00","missing":[] if comment else ["column_comment"],"key_columns":[],"verified_relationships":[],**extra}
    r["catalog_id"]=_hash([r[k] for k in ("source_database","environment","container","schema","object","column")])[:24]
    if definition:
        r["definition"]=definition
    r["metadata_version"]=_hash(r)
    return r

def demo_snapshot():
    from .semantics import parse_definition
    records=[
        _record("FAIZ","Başvuru bazında faiz oranı yüzde."),
        _record("INTEREST_TYPE_CODE","Başvuru bazında faiz türü. 1 sabit faiz, 2 değişken faiz, 8 diğer, 9 bilinmiyor."),
        _record("LIMIT","Başvuru bazında onaylanan limit TRY."),
        _record("REQUESTED_LIMIT_AMOUNT","Başvuru bazında başvurulan limit TRY."),
        _record("GELIR","Başvuru bazında doğrulanmış gelir TRY."),
        _record("DECLARED_INCOME_TRY","Başvuru bazında beyan edilen gelir TRY."),
        _record("BASVURUTAR","",data_type="DATE"),
        _record("BASVURUNO","Kurum Kodu - Şube Kodu - Tarih - Saat - Başvuru No.",data_type="VARCHAR2"),
        _record("HAVALE_LM","Müşterinin son ayda yaptığı havale işlem sayısı.","TRANSFER_FEATURES"),
        _record("HAVALE_LW","Müşterinin son haftada kaç farklı kişi ile havale ilişkisine girdiği.","TRANSFER_FEATURES"),
        _record("EFT_UNIQUE_PEOPLE_1M","Müşterinin son ayda kaç farklı kişi ile EFT ilişkisine girdiği.","TRANSFER_FEATURES"),
        _record("EFT_LW","Müşterinin son haftada kaç farklı hesap ile EFT ilişkisine girdiği.","TRANSFER_FEATURES"),
        _record("GIDEN_EFT_TUT_LW","Müşterinin son haftada giden maksimum EFT tutarı.","TRANSFER_FEATURES"),
        _record("GIDEN_EFT_SUM_L3M","Müşterinin son 3 ayda giden toplam EFT tutarı.","TRANSFER_FEATURES"),
        _record("EFT_LW_L3M","EFT_L3M / EFT_LW; payda sıfırsa -1.","TRANSFER_FEATURES"),
    ]
    movement=parse_definition("AMOUNT","Giden EFT tutarı TRY")
    movement["facts"]["grain"]={"value":"transaction","source":"verified_metadata","evidence":"SENTETİK: her kayıt bir hareketi temsil eder."}
    records.append(_record("AMOUNT","Giden EFT tutarı TRY","EFT_MOVEMENTS",movement,key_columns=["TRANSACTION_ID"]))
    scope={"source_database":"SYNTHETIC_DB","environment":"DEMO","container":"SYNTHETIC_PDB","schemas":["DEMO_RISK"]}
    return {"records":records,"scope":scope,"complete":True,"errors":[],"version":_hash(records),"captured_at":"2026-10-11T00:00:00+00:00"}

class DemoEmbedding:
    """Hashing bag-of-words mock; repeatable wiring tests, not semantic accuracy."""
    model="synthetic-token-hash"
    model_version="1"
    dimensions=512

    def embed(self,texts,kind="document"):
        result=[]
        for text in texts:
            normalized=str(text).replace("İ","I").replace("ı","i").casefold()
            normalized="".join(c for c in unicodedata.normalize("NFKD",normalized) if not unicodedata.combining(c))
            vector=[0.0]*self.dimensions
            for token in re.findall(r"[a-z0-9]+",normalized):
                digest=hashlib.sha256(token.encode()).digest()
                vector[int.from_bytes(digest[:2],"big")%self.dimensions]+=1.0 if digest[2]%2 else -1.0
            result.append(vector)
        return result

def sample_rows():
    rows=[]
    def add(name,kind,desc,cat="Başvuru",opinion="İlk inceleme",reported="Bilinmiyor"):
        rows.append([cat,name,kind,desc,reported,opinion,""])
    add("BaşvuruNo","Varchar(200)","Kurum Kodu - Şube Kodu - Tarih - Saat - Başvuru No.")
    add("BaşvuruTar","Numeric (yyyymmdd)","")
    add("BaşvuruSaat","HH:MM","")
    add("BaşvuruKanal","Numeric","1 şube, 2 mobil, 3 web, 4 ATM, 5 diğer.")
    add("BaşvuruKaynak","Numeric","1 müşteri, 2 önonaylı.")
    add("Urun","Numeric","2 tüketici kredisi, 3 ev kredisi, 23 kredi kartı, 26 kredili mevduat, 50 mevcut bireysel müşteri, 60 mevcut kurumsal müşteri, 70 portföy yönetimi, 61 kurumsal kredi başvurusu, 90 başvurunun yeniden sorgulanması.")
    add("Vade","Numeric","Başvurulan ürüne ait vade.")
    add("Faiz","Numeric","1 sabit faiz, 2 değişken faiz, 8 diğer, 9 bilinmiyor. Burada faiz türü isteniyor.",reported="Evet")
    add("Limit","Numeric","Başvurulan limit.")
    add("Gelir","Numeric","Başvuruda beyan edilen gelir.")
    for name,desc in [
        ("Havale_LM","Müşterinin son ayda kaç farklı kişi ile havale ilişkisine girdiği."),
        ("Havale_LW","Müşterinin son haftada kaç farklı kişi ile havale ilişkisine girdiği."),
        ("Havale_LW_LM","Havale_LW / Havale_LM; payda sıfırsa -1."),
        ("Havale_LW_L3M","Havale_LW / Havale_L3M; payda sıfırsa -1."),
        ("EFT_L3M","Müşterinin son 3 ayda kaç farklı kişi ile EFT ilişkisine girdiği."),
        ("EFT_LM","Müşterinin son ayda kaç farklı kişi ile EFT ilişkisine girdiği."),
        ("EFT_LW","Müşterinin son haftada kaç farklı kişi ile EFT ilişkisine girdiği."),
        ("EFT_LW_LM","EFT_LW / EFT_LM; payda sıfırsa -1."),
        ("EFT_LW_L3M","EFT_LW / EFT_L3M; payda sıfırsa -1."),
        ("Giden_EFT_tut_L3M","Müşterinin son 3 ayda giden toplam EFT tutarı."),
        ("Giden_EFT_tut_LM","Müşterinin son ayda giden toplam EFT tutarı."),
        ("Giden_EFT_tut_LW","Müşterinin son haftada giden toplam EFT tutarı."),
        ("Giden_EFT_tut_LW_LM","Giden_EFT_tut_LW / Giden_EFT_tut_LM; payda sıfırsa -1."),
    ]:
        add(name,"Numeric",desc,"Başvuru-mevduat")
    return rows

def extra_rows():
    return [
        ["Sentetik kabul","FaizTuru_Kontrol","Numeric","Başvuru bazında faiz türü. 1 sabit faiz, 2 değişken faiz, 8 diğer, 9 bilinmiyor.","Hayır","Farklı adla tam tanım; sentetik kontrol", ""],
        ["Sentetik kabul","Gelir_Kontrol","Numeric","Başvuru bazında beyan edilen gelir TRY.","Hayır","Beyan bilgisi etiket değildir", ""],
        ["Sentetik kabul","KuantumSpin_ZYX987","Numeric","","Hayır","İlgisiz alan; zorla aday seçilmemeli", ""],
        ["Sentetik kabul","Havale_LM","Numeric","Müşterinin son ayda<br>kaç farklı kişi ile havale ilişkisine girdiği.\nİkinci satır.","Evet","Yinelenen alan adı ayrı kimlik almalı", "Kullanıcının kendi süresi"],
        ["Sentetik kabul","Ters_Oran_LW_L3M","Numeric","Ters_Oran_L3M / Ters_Oran_LW; payda sıfırsa -1.","Hayır","Ters oran", ""],
    ]

def export_fixture_json(path):
    from .excelio import HEADERS
    value={"headers":HEADERS,"sheets":[{"name":"Sentetik Talepler","rows":sample_rows()},{"name":"Sentetik Kontroller","rows":extra_rows()}],"catalog":demo_snapshot()}
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    return value
