"""Sahte Medallion (DM_CAD / DM / EDW) Oracle katalog görüntüsü ve talep satırları (yalnız test verisi)."""
from benchmark_fixture import CAPTURED, _id, _record

SCOPE = {"source_database": "MEDALLION_DB", "environment": "SYNTHETIC", "container": "MEDALLION_PDB",
         "schemas": ["DM_CAD", "DM", "EDW"]}
VARIANTS = ("base", "ratio_column", "cross_table", "denominator_missing")

XFER_TABLE = "MVA_CUSTOMER_MONEY_XFER_DAILY"
XFER_COMMENT = ("Müşterinin günlük para transferleri işlemlerinin (eft, havale, swift, wu) zaman dilimlerine göre "
                "tüm kanallardan yaptığı işlem bilgilerini tutan tablodur.")
CHANNELS = [("EFT", "EFT"), ("HVL", "havale"), ("SWIFT", "SWIFT"), ("WU", "Western Union")]
WINDOWS = [("W1W", "1 hafta"), ("W1M", "1 ay"), ("W3M", "3 ay")]
MEASURES = [("AMT_TL", "tutarıdır"), ("CNT", "adedidir")]
RATIO_COLUMN = "TOTAL_HVL_TRX_CNT_RATIO_W1W_W3M"
DENOMINATOR = "TOTAL_HVL_TRX_CNT_W3M"
CROSS_TABLE = "MVA_CUSTOMER_HVL_SUMMARY"

REQUESTS = [
    {"name": "Giden_EFT_tut_LM", "description": "Müşterinin son ayda giden toplam eft tutarı",
     "expect": ("DM_CAD", XFER_TABLE, "TOTAL_EFT_TRX_AMT_TL_W1M")},
    {"name": "Gelen_EFT_tut_LM", "description": "Müşterinin son ayda gelen toplam eft tutarı",
     "expect": ("DM_CAD", XFER_TABLE, "TOTAL_INC_EFT_TRX_AMT_TL_W1M")},
    {"name": "Giden_havale_tut_LM", "description": "Müşterinin son ayda giden toplam havale tutarı",
     "expect": ("DM_CAD", XFER_TABLE, "TOTAL_HVL_TRX_AMT_TL_W1M")},
    {"name": "Giden_EFT_adet_L3M", "description": "Müşterinin son 3 ayda yaptığı giden eft işlem adedi",
     "expect": ("DM_CAD", XFER_TABLE, "TOTAL_EFT_TRX_CNT_W3M")},
    {"name": "Giden_swift_tut_LM", "description": "Müşterinin son ayda giden toplam swift tutarı",
     "expect": ("DM_CAD", XFER_TABLE, "TOTAL_SWIFT_TRX_AMT_TL_W1M")},
    {"name": "Giden_havale_adeti_LW_L3M",
     "description": "Son bir haftadaki giden havale adedinin son 3 aydaki giden havale adedine oranı",
     "expect": {"ratio": True}},
    {"name": "Giden_swift_tut_L6M", "description": "Müşterinin son 6 ayda giden swift tutarı",
     "expect": ("DM", "CUST_XFER_SUMMARY_MONTHLY", "SWIFT_OUT_AMT_TL_L6M")},
    {"name": "Kredi_karti_limit_doluluk_LM", "description": "Müşterinin son ayda kredi kartı limit doluluk oranı",
     "expect": None},
    {"name": "Sigorta_police_adet_L12M", "description": "Müşterinin son 12 ayda yaptırdığı sigorta poliçesi adedi",
     "expect": None},
]


def _xfer_columns():
    prefix = "Müşterinin ilgili yıl aydan önceki {} içerisinde tüm kanallarından yaptığı toplam {} {} {}."
    columns = []
    for channel, label in CHANNELS:
        for measure, word in MEASURES:
            for window, span in WINDOWS:
                columns.append(("TOTAL_{}_TRX_{}_{}".format(channel, measure, window), "NUMBER",
                                prefix.format(span, "giden", label, word)))
    for channel, label in CHANNELS[:2]:
        for measure, word in MEASURES:
            columns.append(("TOTAL_INC_{}_TRX_{}_W1M".format(channel, measure), "NUMBER",
                            prefix.format("1 ay", "gelen", label, word)))
    return columns


def _table(schema, table, table_comment, columns, keys):
    rows = [_record(schema, table, name, data_type, comment, table_comment, key=True)
            for name, data_type, comment in keys]
    rows += [_record(schema, table, name, data_type, comment, table_comment) for name, data_type, comment in columns]
    for row in rows:  # _record benchmark kapsamını yazar; snapshot kapsamıyla aynı olmalı
        row.update({key: SCOPE[key] for key in ("source_database", "environment", "container")})
    return rows


def records(variant="base"):
    if variant not in VARIANTS:
        raise ValueError("Bilinmeyen varyant: {}".format(variant))
    xfer = _xfer_columns()
    if variant in ("denominator_missing", "cross_table"):
        xfer = [item for item in xfer if item[0] != DENOMINATOR]
    elif variant == "ratio_column":
        xfer.append((RATIO_COLUMN, "NUMBER", "Müşterinin son 1 haftadaki giden havale adedinin son 3 aydaki giden "
                                             "havale adedine oranıdır."))
    customer_keys = [("CUSTOMER_ID", "NUMBER", "Müşteri numarası"), ("YEAR_MONTH", "NUMBER", "Yıl ay bilgisi")]
    out = _table("DM_CAD", XFER_TABLE, XFER_COMMENT, xfer, customer_keys)
    if variant == "cross_table":
        out += _table("DM_CAD", CROSS_TABLE, "Müşterinin havale işlemlerinin hesap bazında özetini tutan tablodur.",
                      [(DENOMINATOR, "NUMBER", "Müşterinin ilgili yıl aydan önceki 3 ay içerisinde tüm kanallarından "
                                               "yaptığı toplam giden havale adedidir.")],
                      [("ACCOUNT_NO", "NUMBER", "Hesap numarası"), ("REPORT_DATE", "DATE", "Rapor tarihi")])
    card = []
    for measure, word in (("AMT_TL", "tutarıdır"), ("CNT", "adedidir")):
        for window, span in WINDOWS[1:]:
            card.append(("TOTAL_CARD_SPEND_{}_{}".format(measure, window), "NUMBER",
                         "Müşterinin ilgili yıl aydan önceki {} içerisinde yaptığı toplam kart harcama {}".format(
                             span, word)))
    out += _table("DM_CAD", "MVA_CUSTOMER_CARD_DAILY", "Müşterinin günlük kart harcama bilgilerini tutan tablodur.",
                  card, customer_keys)
    out += _table("DM_CAD", "MVA_CUSTOMER_BRANCH_VISIT", "Müşterinin şube ziyaret bilgilerini tutan tablodur.",
                  [("BRANCH_VISIT_CNT_W1M", "NUMBER", "Müşterinin son 1 ayda yaptığı şube ziyaret adedidir."),
                   ("LAST_VISIT_DATE", "DATE", "Müşterinin son şube ziyaret tarihidir.")], customer_keys)
    out += _table("DM_CAD", "MVA_CUSTOMER_DEMOGRAPHY", "Müşterinin demografik bilgilerini tutan tablodur.",
                  [("AGE", "NUMBER", "Müşterinin yaşıdır."), ("EDUCATION_CODE", "VARCHAR2", "Eğitim durumu kodudur.")],
                  customer_keys)
    out += _table("DM_CAD", "MVA_CUSTOMER_LOAN_MONTHLY", "Müşterinin aylık kredi bilgilerini tutan tablodur.",
                  [("LOAN_BALANCE_AMT_TL", "NUMBER", "Müşterinin kredi bakiye tutarıdır.")], customer_keys)
    out += _table("DM_CAD", "INP_MONEY_XFER", "Para transferi girdi tablosu",
                  [("EFT_OUT_AMT", "NUMBER", ""), ("HVL_OUT_AMT", "NUMBER", "Havale"), ("LOAD_DATE", "DATE", "")],
                  [("CUSTOMER_ID", "NUMBER", "Müşteri numarası")])
    out += _table("DM_CAD", "OUT_RISK_FEATURES", "Risk modeli çıktı değişkenleri",
                  [("GIDEN_EFT_TUT_LM", "NUMBER", "Giden EFT tutarı son ay"), ("RISK_SCORE", "NUMBER", "Risk skoru")],
                  [("CUSTOMER_ID", "NUMBER", "Müşteri numarası")])
    out += _table("DM", "CUST_XFER_SUMMARY_MONTHLY", "Aylık transfer özeti",
                  [("EFT_OUT_AMT_TL_L1M", "NUMBER", "Giden EFT"), ("EFT_OUT_CNT_L1M", "NUMBER", ""),
                   ("HVL_OUT_AMT_TL_L1M", "NUMBER", "Havale tutarı"), ("INC_EFT_AMT_TL_L1M", "NUMBER", ""),
                   ("SWIFT_OUT_AMT_TL_L6M", "NUMBER", "Müşterinin son 6 ayda giden SWIFT tutarı")],
                  customer_keys)
    out += _table("EDW", "TRX_MONEY_TRANSFER", "Para transferi işlemlerinin işlem bazında tutulduğu kaynak tablo",
                  [("TRX_DATE", "DATE", "İşlem tarihi"), ("CHANNEL_CODE", "VARCHAR2", "Kanal kodu"),
                   ("DIRECTION_CODE", "VARCHAR2", "Yön kodu (giden/gelen)"),
                   ("TRX_AMOUNT_TL", "NUMBER", "İşlem tutarı (TL)")],
                  [("CUSTOMER_NO", "NUMBER", "Müşteri numarası")])
    out.sort(key=lambda row: (row["schema"], row["object"], row["column"]))
    return out


def snapshot(variant="base", schemas=("DM_CAD", "DM", "EDW"), complete=True):
    wanted = set(schemas)
    rows = [row for row in records(variant) if row["schema"] in wanted]
    return {"records": rows, "scope": dict(SCOPE, schemas=list(schemas)), "complete": complete,
            "errors": [], "version": _id("medallion", variant, ",".join(schemas)), "captured_at": CAPTURED}
