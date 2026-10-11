"""Labelled SYNTHETIC benchmark: a DM_CAD-like catalog and requests with expected columns or YOK.

Nothing here is bank data. Names, comments and labels were written by hand to reproduce the failure
modes seen in review: abbreviations (MUS/KRD/HVL/ORT), empty comments, TR/EN mixes, generic words
(musteri/son/ay/tutar) everywhere, and hard negatives that differ only in window, measure, direction,
aggregation or grain. Scores on this set are regression signals, not bank accuracy.
"""
import hashlib
import random

SCOPE = {"source_database": "BENCH_DB", "environment": "SYNTHETIC", "container": "BENCH_PDB",
         "schemas": ["DM_CAD", "DM_RISK"]}
CAPTURED = "2026-10-11T00:00:00+00:00"

# key, table, data_type, Turkish definition, English definition, column variants (name, comment style)
# comment style: tr = Turkish with diacritics, ascii = folded Turkish, en = English, "" = no comment
CONCEPTS = [
    ("dep_avg_1m", "MUS_MEVDUAT_OZET", "NUMBER", "Son 1 ay ortalama mevduat bakiyesi", "Average deposit balance last 1 month",
     [("AVG_DPST_BAL_1M", "en"), ("ORT_MEVDUAT_BKY_1AY", "tr")]),
    ("dep_avg_3m", "MUS_MEVDUAT_OZET", "NUMBER", "Son 3 ay ortalama mevduat bakiyesi", "Average deposit balance last 3 months",
     [("AVG_DPST_BAL_3M", "en"), ("ORT_MEVDUAT_BKY_3AY", "tr"), ("MEV_ORT_BAK_L3M", "")]),
    ("dep_avg_6m", "MUS_MEVDUAT_OZET", "NUMBER", "Son 6 ay ortalama mevduat bakiyesi", "Average deposit balance last 6 months",
     [("AVG_DPST_BAL_6M", "en"), ("ORT_MEVDUAT_BKY_6AY", "ascii")]),
    ("dep_avg_12m", "MUS_MEVDUAT_OZET", "NUMBER", "Son 12 ay ortalama mevduat bakiyesi", "Average deposit balance last 12 months",
     [("AVG_DPST_BAL_12M", "en"), ("ORT_MEVDUAT_BKY_12AY", "tr")]),
    ("dep_max_3m", "MUS_MEVDUAT_OZET", "NUMBER", "Son 3 ay maksimum mevduat bakiyesi", "Maximum deposit balance last 3 months",
     [("MAX_DPST_BAL_3M", "en"), ("MAKS_MEVDUAT_BKY_3AY", "tr")]),
    ("dep_end_bal", "MUS_MEVDUAT_OZET", "NUMBER", "Ay sonu toplam mevduat bakiyesi", "Month end total deposit balance",
     [("DPST_BAL_EOM", "en"), ("AY_SONU_MEVDUAT_BKY", "")]),
    ("acct_avg_bal_3m", "HSP_BAKIYE_OZET", "NUMBER", "Hesap bazında son 3 ay ortalama bakiye", "Account level average balance last 3 months",
     [("HSP_ORT_BAKIYE_3AY", "tr")]),
    ("dlq_cnt_6m", "KRD_GECIKME_OZET", "NUMBER", "Son 6 ayda gecikmeye düşen kredi adedi", "Number of loans delinquent in last 6 months",
     [("DLQ_LOAN_CNT_6M", "en"), ("GCK_KRD_ADET_6AY", "tr")]),
    ("dlq_amt_6m", "KRD_GECIKME_OZET", "NUMBER", "Son 6 ayda gecikmedeki kredi tutarı", "Delinquent loan amount in last 6 months",
     [("DLQ_LOAN_AMT_6M", "en"), ("GCK_KRD_TUT_6AY", "tr")]),
    ("dlq_cnt_12m", "KRD_GECIKME_OZET", "NUMBER", "Son 12 ayda gecikmeye düşen kredi adedi", "Number of loans delinquent in last 12 months",
     [("DLQ_LOAN_CNT_12M", "en"), ("GCK_KRD_ADET_12AY", "")]),
    ("max_dpd_12m", "KRD_GECIKME_OZET", "NUMBER", "Son 12 ayda maksimum gecikme gün sayısı", "Maximum days past due in last 12 months",
     [("MAX_DPD_12M", "en"), ("MAKS_GECIKME_GUN_12AY", "tr")]),
    ("max_dpd_3m", "KRD_GECIKME_OZET", "NUMBER", "Son 3 ayda maksimum gecikme gün sayısı", "Maximum days past due in last 3 months",
     [("MAX_DPD_3M", "en"), ("MAKS_GECIKME_GUN_3AY", "ascii")]),
    ("avg_dpd_12m", "KRD_GECIKME_OZET", "NUMBER", "Son 12 ayda ortalama gecikme gün sayısı", "Average days past due in last 12 months",
     [("AVG_DPD_12M", "en")]),
    ("cc_util", "KK_OZET", "NUMBER", "Kredi kartı limit kullanım oranı", "Credit card limit utilization ratio",
     [("CC_LMT_UTIL_RT", "en"), ("KK_LIMIT_DOLULUK_ORAN", "tr")]),
    ("cc_limit", "KK_OZET", "NUMBER", "Kredi kartı toplam limiti", "Credit card total limit",
     [("CC_TOT_LMT_AMT", "en"), ("KK_TOPLAM_LIMIT", "")]),
    ("cc_spend_3m", "KK_OZET", "NUMBER", "Son 3 ay kredi kartı harcama tutarı", "Credit card spending amount last 3 months",
     [("CC_SPEND_AMT_3M", "en"), ("KK_HARCAMA_TUT_3AY", "tr")]),
    ("cc_spend_6m", "KK_OZET", "NUMBER", "Son 6 ay kredi kartı harcama tutarı", "Credit card spending amount last 6 months",
     [("CC_SPEND_AMT_6M", "en"), ("KK_HARCAMA_TUT_6AY", "ascii")]),
    ("tot_loan_bal", "KRD_OZET", "NUMBER", "Müşterinin toplam kredi bakiyesi", "Customer total outstanding loan balance",
     [("TOT_LOAN_BAL", "en"), ("TOPLAM_KREDI_BKY", "tr")]),
    ("act_loan_cnt", "KRD_OZET", "NUMBER", "Müşterinin aktif kredi adedi", "Number of active loans of the customer",
     [("ACT_LOAN_CNT", "en"), ("AKTIF_KREDI_ADET", "")]),
    ("mon_inst", "KRD_OZET", "NUMBER", "Toplam aylık kredi taksit tutarı", "Total monthly loan installment amount",
     [("MON_INST_AMT", "en"), ("AYLIK_TAKSIT_TUT", "tr")]),
    ("dti", "KRD_OZET", "NUMBER", "Borç gelir oranı yüzde", "Debt to income ratio percent",
     [("DTI_RATIO", "en"), ("BORC_GELIR_ORAN", "tr")]),
    ("cust_age", "MUS_DEMOGRAFI", "NUMBER", "Müşteri yaşı", "Customer age in years",
     [("CUST_AGE", "en"), ("MUS_YAS", "")]),
    ("tenure", "MUS_DEMOGRAFI", "NUMBER", "Müşterinin bankadaki kıdemi ay", "Customer tenure with the bank in months",
     [("CUST_TENURE_MON", "en"), ("BANKA_KIDEM_AY", "tr")]),
    ("income", "MUS_DEMOGRAFI", "NUMBER", "Beyan edilen aylık gelir TL", "Declared monthly income TRY",
     [("MONTHLY_INCOME", ""), ("BEYAN_AYLIK_GELIR", "tr")]),
    ("education", "MUS_DEMOGRAFI", "VARCHAR2", "Müşterinin eğitim durumu", "Customer education level",
     [("EDU_LVL", "en"), ("EGITIM_DRM", "")]),
    ("marital", "MUS_DEMOGRAFI", "VARCHAR2", "Müşterinin medeni durumu", "Customer marital status",
     [("MEDENI_HAL", "tr")]),
    ("in_trf_amt_3m", "HVL_EFT_OZET", "NUMBER", "Son 3 ay gelen havale toplam tutarı", "Total incoming transfer amount last 3 months",
     [("INC_TRF_AMT_3M", "en"), ("HVL_GLN_TUT_3AY", "tr")]),
    ("out_trf_amt_3m", "HVL_EFT_OZET", "NUMBER", "Son 3 ay giden havale toplam tutarı", "Total outgoing transfer amount last 3 months",
     [("OUT_TRF_AMT_3M", "en"), ("HVL_GDN_TUT_3AY", "tr")]),
    ("in_trf_cnt_3m", "HVL_EFT_OZET", "NUMBER", "Son 3 ay gelen havale adedi", "Number of incoming transfers last 3 months",
     [("INC_TRF_CNT_3M", "en"), ("HVL_GLN_ADET_3AY", "ascii")]),
    ("out_eft_cnt_1m", "HVL_EFT_OZET", "NUMBER", "Son 1 ay giden EFT adedi", "Number of outgoing EFT last 1 month",
     [("OUT_EFT_CNT_L1M", "en"), ("EFT_GDN_ADET_1AY", "")]),
    ("bureau_score", "RISK_SKOR", "NUMBER", "KKB risk skoru", "Credit bureau risk score",
     [("BUREAU_SCORE", "en"), ("KKB_SKOR", "tr")]),
    ("chq_bounce_12m", "RISK_SKOR", "NUMBER", "Son 12 ay karşılıksız çek adedi", "Bounced cheque count last 12 months",
     [("BOUNCED_CHQ_CNT_12M", "en"), ("CEK_IADE_ADET_12AY", "tr")]),
    ("legal_flag", "RISK_SKOR", "VARCHAR2", "Takipte kredisi var mı E/H", "Legal follow-up loan flag Y/N",
     [("LEGAL_FOLLOWUP_FLG", "en"), ("TKP_KRD_FLG", "")]),
    ("app_date", "BASVURU", "DATE", "Kredi başvuru tarihi", "Loan application date",
     [("APP_DT", "en"), ("BASVURU_TRH", "tr")]),
    ("product_type", "BASVURU", "VARCHAR2", "Başvurulan ürün tipi", "Applied product type",
     [("PRD_TYPE_CD", "en"), ("URUN_TIPI", "tr")]),
    ("branch", "BASVURU", "VARCHAR2", "Başvurunun yapıldığı şube kodu", "Branch code of the application",
     [("BRANCH_CD", ""), ("SUBE_KOD", "tr")]),
    ("payroll", "MUS_DEMOGRAFI", "VARCHAR2", "Maaş müşterisi mi E/H", "Payroll customer flag",
     [("PAYROLL_FLG", "en"), ("MAAS_MUS_FLG", "")]),
    ("prd_cnt", "MUS_DEMOGRAFI", "NUMBER", "Müşterinin sahip olduğu aktif ürün sayısı", "Number of active products held",
     [("ACT_PRD_CNT", "en"), ("URUN_SAYISI", "tr")]),
    ("od_util", "HSP_BAKIYE_OZET", "NUMBER", "Kredili mevduat hesabı limit kullanım oranı", "Overdraft limit utilization ratio",
     [("OD_LMT_UTIL_RT", "en"), ("KMH_KULLANIM_ORAN", "tr")]),
    ("mobile_login", "DIJITAL_OZET", "DATE", "Mobil bankacılığa son giriş tarihi", "Last mobile banking login date",
     [("LAST_MOB_LOGIN_DT", "en"), ("SON_MOBIL_GIRIS_TRH", "")]),
]

TABLE_COMMENTS = {
    "MUS_MEVDUAT_OZET": "Müşteri bazında aylık mevduat özet tablosu",
    "HSP_BAKIYE_OZET": "Hesap bazında bakiye ve limit özet tablosu",
    "KRD_GECIKME_OZET": "Müşteri bazında kredi gecikme özeti",
    "KK_OZET": "Müşteri bazında kredi kartı özet tablosu",
    "KRD_OZET": "Müşteri bazında kredi portföy özeti",
    "MUS_DEMOGRAFI": "Müşteri demografik bilgileri",
    "HVL_EFT_OZET": "Müşteri bazında havale ve EFT hareket özeti",
    "RISK_SKOR": "Risk skorları ve olumsuz kayıtlar",
    "BASVURU": "Kredi başvuru bilgileri",
    "DIJITAL_OZET": "Dijital kanal kullanım özeti",
    "F_MUS_DEGISKEN_AYLIK": "Aylık müşteri değişken tablosu (geniş)",
}

# Requests as an analyst would type them. expected = concept keys; [] means the catalog has no match.
REQUESTS = [
    ("Mevduat", "ORT_MEVDUAT_3AY", "Sayısal", "Müşterinin son 3 aydaki ortalama mevduat bakiyesi", ["dep_avg_3m"]),
    ("Mevduat", "Son3AyOrtalamaBakiye", "Sayısal", "Son üç ay ortalama mevduat bakiyesi TL", ["dep_avg_3m"]),
    ("Mevduat", "AVG_DPST_BAL_6M", "Sayısal", "", ["dep_avg_6m"]),
    ("Mevduat", "MEVDUAT_ORT_12AY", "Sayısal", "Son 12 ay ortalama mevduat", ["dep_avg_12m"]),
    ("Mevduat", "MEVDUAT_ORT_1AY", "Sayısal", "Son bir ayın ortalama mevduat bakiyesi", ["dep_avg_1m"]),
    ("Mevduat", "MAKS_MEVDUAT_3AY", "Sayısal", "Son 3 ayda görülen en yüksek mevduat bakiyesi", ["dep_max_3m"]),
    ("Mevduat", "AY_SONU_MEVDUAT", "Sayısal", "Ay sonundaki toplam mevduat bakiyesi", ["dep_end_bal"]),
    ("Mevduat", "HESAP_ORT_BAKIYE_3AY", "Sayısal", "Hesap bazında son 3 ay ortalama bakiye", ["acct_avg_bal_3m"]),
    ("Kredi", "GECIKEN_KREDI_SAYISI_6AY", "Sayısal", "Son 6 ayda gecikmeye düşmüş kredi sayısı", ["dlq_cnt_6m"]),
    ("Kredi", "GCK_KRD_TUT_6AY", "Sayısal", "Son 6 ay gecikmedeki kredilerin tutarı", ["dlq_amt_6m"]),
    ("Kredi", "GECIKEN_KREDI_ADET_12AY", "Sayısal", "Son 12 ayda gecikmeye düşen kredi adedi", ["dlq_cnt_12m"]),
    ("Kredi", "MAX_GECIKME_GUN_12AY", "Sayısal", "Son 12 ay içindeki en yüksek gecikme gün sayısı", ["max_dpd_12m"]),
    ("Kredi", "MAX_DPD_3M", "Sayısal", "Maximum days past due in the last 3 months", ["max_dpd_3m"]),
    ("Kredi", "ORT_GECIKME_GUN_12AY", "Sayısal", "Son 12 ay ortalama gecikme günü", ["avg_dpd_12m"]),
    ("Kart", "KK_LIMIT_KULLANIM", "Sayısal", "Kredi kartı limitinin kullanım oranı yüzde", ["cc_util"]),
    ("Kart", "KART_LIMIT", "Sayısal", "Müşterinin toplam kredi kartı limiti", ["cc_limit"]),
    ("Kart", "KK_HARCAMA_3AY", "Sayısal", "Son 3 ay kredi kartı harcama toplamı", ["cc_spend_3m"]),
    ("Kart", "CC_SPEND_6M", "Sayısal", "Credit card spend last 6 months", ["cc_spend_6m"]),
    ("Kredi", "TOPLAM_KREDI_BAKIYE", "Sayısal", "Müşterinin tüm kredilerinin toplam bakiyesi", ["tot_loan_bal"]),
    ("Kredi", "AKTIF_KREDI_SAYISI", "Sayısal", "Aktif kredi sayısı", ["act_loan_cnt"]),
    ("Kredi", "AYLIK_TAKSIT", "Sayısal", "Müşterinin ödediği toplam aylık taksit", ["mon_inst"]),
    ("Kredi", "BORC_GELIR_ORANI", "Sayısal", "Borcun gelire oranı", ["dti"]),
    ("Demografik", "MUSTERI_YASI", "Sayısal", "Müşterinin yaşı", ["cust_age"]),
    ("Demografik", "KIDEM", "Sayısal", "Müşterinin bankadaki kıdemi (ay)", ["tenure"]),
    ("Demografik", "AYLIK_GELIR", "Sayısal", "Müşterinin beyan ettiği aylık gelir", ["income"]),
    ("Demografik", "EGITIM", "Metin", "Eğitim seviyesi", ["education"]),
    ("Demografik", "MEDENI_DURUM", "Metin", "Medeni durum", ["marital"]),
    ("Demografik", "MAAS_MUSTERISI", "Metin", "Maaşını bankamızdan alıyor mu", ["payroll"]),
    ("Demografik", "URUN_ADEDI", "Sayısal", "Sahip olunan aktif ürün adedi", ["prd_cnt"]),
    ("Hareket", "GELEN_HAVALE_TUTAR_3AY", "Sayısal", "Son 3 ayda gelen havalelerin toplam tutarı", ["in_trf_amt_3m"]),
    ("Hareket", "GIDEN_HAVALE_TUTAR_3AY", "Sayısal", "Son 3 ayda giden havalelerin toplam tutarı", ["out_trf_amt_3m"]),
    ("Hareket", "GELEN_HAVALE_ADET_3AY", "Sayısal", "Son 3 ay gelen havale sayısı", ["in_trf_cnt_3m"]),
    ("Hareket", "GIDEN_EFT_ADET_1AY", "Sayısal", "Son 1 ayda yapılan giden EFT adedi", ["out_eft_cnt_1m"]),
    ("Risk", "KKB_SKORU", "Sayısal", "Kredi kayıt bürosu skoru", ["bureau_score"]),
    ("Risk", "KARSILIKSIZ_CEK_12AY", "Sayısal", "Son 12 ayda karşılıksız çıkan çek sayısı", ["chq_bounce_12m"]),
    ("Risk", "TAKIP_FLAG", "Metin", "Kanuni takipte kredisi bulunuyor mu", ["legal_flag"]),
    ("Başvuru", "BASVURU_TARIHI", "Tarih", "Kredi başvurusunun yapıldığı tarih", ["app_date"]),
    ("Başvuru", "URUN_TIPI", "Metin", "Başvurulan ürünün tipi", ["product_type"]),
    ("Başvuru", "SUBE", "Metin", "Başvurunun alındığı şube", ["branch"]),
    ("Hesap", "KMH_KULLANIM", "Sayısal", "KMH limit kullanım oranı", ["od_util"]),
    ("Dijital", "SON_MOBIL_GIRIS", "Tarih", "Mobil uygulamaya en son giriş tarihi", ["mobile_login"]),
    # No match in this catalog: the right answer is zero confident candidates.
    ("Dijital", "SOSYAL_MEDYA_TAKIPCI", "Sayısal", "Müşterinin sosyal medya takipçi sayısı", []),
    ("Yatırım", "KRIPTO_ALIM_TUT_3AY", "Sayısal", "Son 3 ay kripto para alım tutarı", []),
    ("Demografik", "EVCIL_HAYVAN", "Metin", "Müşterinin evcil hayvanı var mı", []),
    ("Mevduat", "ORT_MEVDUAT_24AY", "Sayısal", "Son 24 ay ortalama mevduat bakiyesi", []),
    ("Hareket", "GIDEN_HAVALE_ADET_6AY", "Sayısal", "Son 6 ayda giden havale adedi", []),
    ("Kart", "NAKIT_AVANS_TUT", "Sayısal", "Kredi kartı nakit avans tutarı", []),
    ("Demografik", "ARAC_SAHIPLIGI", "Metin", "Müşterinin aracı var mı", []),
    ("Kredi", "EKSPERTIZ_DEGERI", "Sayısal", "Konut kredisi teminat ekspertiz değeri", []),
    ("Dijital", "EBULTEN_ABONE", "Metin", "E-posta bültenine abone mi", []),
    ("Kart", "KK_HARCAMA_12AY", "Sayısal", "Son 12 ay kredi kartı harcama tutarı", []),
]

FOLD = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
KEYS = [("MUSTERI_NO", "NUMBER", "Müşteri numarası"), ("HESAP_NO", "NUMBER", "Hesap numarası"),
        ("KAYIT_TRH", "DATE", "Kayıt tarihi"), ("DONEM_ID", "NUMBER", "Dönem"), ("GUNCELLEME_TRH", "DATE", "")]
FILLER_PREFIX = [("ISL", "işlem"), ("ODM", "ödeme"), ("TAH", "tahsilat"), ("DOV", "döviz"), ("SIG", "sigorta"),
                 ("FON", "fon"), ("POS", "POS"), ("ATM", "ATM"), ("FTR", "fatura"), ("KMP", "kampanya")]
FILLER_MEASURE = [("TUT", "tutarı", "NUMBER"), ("ADET", "adedi", "NUMBER"), ("ORAN", "oranı", "NUMBER"),
                  ("TRH", "tarihi", "DATE"), ("KOD", "kodu", "VARCHAR2"), ("FLG", "var mı", "VARCHAR2")]
FILLER_WINDOW = [("", ""), ("SON_AY", "son ay"), ("1AY", "son 1 ay"), ("3AY", "son 3 ay"), ("6AY", "son 6 ay"),
                 ("12AY", "son 12 ay"), ("GUN", "günlük")]


def _id(*parts):
    return "bm-" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]


def _record(schema, table, column, data_type, comment, table_comment, key=False):
    record = {"source_database": SCOPE["source_database"], "environment": SCOPE["environment"],
              "container": SCOPE["container"], "schema": schema, "object": table, "column": column,
              "object_type": "TABLE", "data_type": data_type, "column_comment": comment,
              "table_comment": table_comment, "captured_at": CAPTURED,
              "missing": [] if comment else ["column_comment"], "key_columns": [column] if key else [],
              "verified_relationships": [], "metadata_version": "bench-v1"}
    record["catalog_id"] = _id(schema, table, column)
    return record


ABSENT_TOPICS = [("UCAK_BILETI", "uçak bileti"), ("SPOR_SALONU", "spor salonu üyeliği"), ("BURS", "burs"),
                 ("KAN_GRUBU", "kan grubu"), ("FUTBOL_TAKIM", "tuttuğu futbol takımı"), ("TATIL_KOYU", "tatil köyü"),
                 ("BOY_KILO", "boy ve kilo"), ("EVCIL_HAYVAN", "evcil hayvan"), ("KRIPTO", "kripto para"),
                 ("SOSYAL_MEDYA", "sosyal medya")]
ABSENT_MEASURE = [("TUTAR", "tutarı"), ("ADET", "adedi"), ("ORAN", "oranı"), ("FLAG", "var mı")]


REQUEST_TYPES = {"NUMBER": "Sayısal", "VARCHAR2": "Metin", "DATE": "Tarih"}


def _camel(name):
    return "".join(part.capitalize() for part in name.split("_"))


def _extra_requests(count, concept_ids, rng):
    """Deterministic extra rows: every 5th has no catalog match (~20% YOK), the rest map to a concept."""
    rows = []
    for number in range(count):
        if number % 5 == 4:
            topic, topic_tr = rng.choice(ABSENT_TOPICS)
            measure, measure_tr = rng.choice(ABSENT_MEASURE)
            window, window_tr = rng.choice(FILLER_WINDOW[2:6])
            name = "{}_{}_{}".format(topic, measure, window)
            description = "Müşterinin {} {} {}".format(window_tr, topic_tr, measure_tr)
            rows.append(("Diğer", name, "Metin" if measure == "FLAG" else "Sayısal", description, []))
            continue
        key, _table, _type, tr, en, variants = rng.choice(CONCEPTS)
        column = rng.choice(variants)[0]
        style = rng.randrange(4)
        if style == 0:
            name, description = column, ""
        elif style == 1:
            name, description = _camel(column), tr
        elif style == 2:
            name, description = "ALAN_{:03d}".format(number), tr
        else:
            name, description = column.replace("_", ""), en
        rows.append(("Ek", name, REQUEST_TYPES[_type], description, [key]))
    return rows


def build(filler_tables=120, seed=11, rows=None):
    """Return (snapshot, requests, labels). labels[row_index] = sorted catalog_ids or [] for YOK.

    rows: total request count; the hand-written REQUESTS stay first, extra rows are generated deterministically."""
    rng = random.Random(seed)
    records, concept_ids = [], {}
    by_table = {}
    for key, table, data_type, tr, en, variants in CONCEPTS:
        for number, (column, style) in enumerate(variants):
            comment = {"tr": tr, "ascii": tr.translate(FOLD), "en": en, "": ""}[style]
            # Variants live in the concept table and, for the second variant, in the wide feature table too.
            homes = [("DM_CAD", table)] + ([("DM_CAD", "F_MUS_DEGISKEN_AYLIK")] if number == 1 else [])
            for schema, home in homes:
                record = _record(schema, home, column, data_type, comment, TABLE_COMMENTS[home])
                by_table.setdefault((schema, home), []).append(record)
                concept_ids.setdefault(key, []).append(record["catalog_id"])
    for number in range(filler_tables):
        prefix, prefix_tr = FILLER_PREFIX[number % len(FILLER_PREFIX)]
        schema = "DM_RISK" if number % 4 == 3 else "DM_CAD"
        table = "{}_{}_{:03d}".format(prefix, rng.choice(["OZET", "DETAY", "AYLIK", "GUNLUK", "HAREKET"]), number)
        table_comment = "Müşteri bazında {} {} tablosu".format(prefix_tr, rng.choice(["özet", "detay", "hareket"]))
        columns = set()
        for _ in range(rng.randint(12, 30)):
            measure, measure_tr, data_type = rng.choice(FILLER_MEASURE)
            window, window_tr = rng.choice(FILLER_WINDOW)
            sub = rng.choice(["", "MUS", "GLN", "GDN", "TOP", "ORT", "MAKS"])
            column = "_".join(part for part in (prefix, sub, measure, window) if part)
            if column in columns:
                continue
            columns.add(column)
            words = {"MUS": "müşteri", "GLN": "gelen", "GDN": "giden", "TOP": "toplam", "ORT": "ortalama",
                     "MAKS": "maksimum", "": ""}[sub]
            comment = " ".join(part for part in ("Müşteri" if rng.random() < .5 else "", window_tr, words,
                                                  prefix_tr, measure_tr) if part).strip()
            roll = rng.random()
            comment = "" if roll < .35 else comment.translate(FOLD) if roll < .55 else comment
            by_table.setdefault((schema, table), []).append(
                _record(schema, table, column, data_type, comment[:1].upper() + comment[1:], table_comment))
    for (schema, table), columns in sorted(by_table.items()):
        comment = TABLE_COMMENTS.get(table) or columns[0]["table_comment"]
        for name, data_type, key_comment in KEYS[:3 if table.startswith(("BASVURU", "DIJITAL")) else 5]:
            records.append(_record(schema, table, name, data_type, key_comment, comment, key=name == "MUSTERI_NO"))
        records.extend(columns)
    records.sort(key=lambda row: (row["schema"], row["object"], row["column"]))
    snapshot = {"records": records, "scope": dict(SCOPE, schemas=list(SCOPE["schemas"])), "complete": True,
                "errors": [], "version": _id("bench", str(filler_tables), str(seed)), "captured_at": CAPTURED}
    requests, labels = [], []
    source = list(REQUESTS)
    if rows and rows > len(source):
        source += _extra_requests(rows - len(source), concept_ids, random.Random(seed + 1000))
    elif rows:
        source = source[:rows]
    for number, (category, name, kind, description, expected) in enumerate(source, start=2):
        requests.append({"row_id": "bench-{:03d}".format(number), "sheet": "Degiskenler", "excel_row": number,
                         "name": name, "description": description, "data_type": kind, "category": category,
                         "reported": "", "input_errors": []})
        labels.append(sorted(cid for key in expected for cid in concept_ids[key]))
    return snapshot, requests, labels


def concept_of(text):
    """Hidden ground-truth concept for a column name or request name found in text (test oracle only)."""
    found = None
    for key, _table, _type, _tr, _en, variants in CONCEPTS:
        for column, _style in variants:
            if column in text and (found is None or len(column) > len(found[1])):
                found = (key, column)
    return found[0] if found else None
