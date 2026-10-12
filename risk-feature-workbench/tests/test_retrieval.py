import copy
import hashlib
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riskmatch.errors import InputError, IntegrationError, StaleIndexError
from riskmatch.ranking import rank
from riskmatch.retrieval import HybridIndex, MemoryNamespace, _tokens


SCOPE = {"source_database": "risk", "environment": "dev", "container": "PDB1",
         "schemas": ["A", "B"]}


def record(identifier, column, comment, schema="A", obj="FEATURES"):
    return {"catalog_id": identifier, "source_database": "risk", "environment": "dev",
            "container": "PDB1", "schema": schema, "object": obj, "column": column,
            "column_comment": comment, "table_comment": "Risk features", "metadata_version": identifier + "-v1"}


def snapshot(records):
    return {"scope": copy.deepcopy(SCOPE), "records": records, "version": "v1", "complete": True}


def hash_vector(text, dims=64):
    vector = [0.0] * dims
    folded = " " + text.casefold() + " "
    for start in range(len(folded) - 2):
        value = int.from_bytes(hashlib.blake2b(folded[start:start + 3].encode("utf-8"),
                                               digest_size=4).digest(), "little")
        vector[value % dims] += 1.0 if value & (1 << 31) else -1.0
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


class HashEmbed:
    """Deterministic trigram-hash embedding; records every call."""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, texts, kind):
        self.calls.append((kind, list(texts)))
        if self.fail:
            raise IntegrationError("provider-secret-that-must-not-leak")
        return [hash_vector(text) for text in texts]


def fillers(count):
    words = ["MUSTERI_SON_AY_ADET", "MUSTERI_SON_AY_TUTAR", "SON_AY_MUSTERI_SAYI", "MUSTERI_KANAL_SON"]
    return [record("f%04d" % i, "%s_%d" % (words[i % len(words)], i), "musteri son ay bilgisi",
                   obj="T%03d" % (i % 40)) for i in range(count)]


def frozen_namespace(index, embed):
    namespace = index.namespace
    items = index.missing_documents()
    namespace.put_many([(h, vector) for (h, _), vector in zip(items, embed([t for _, t in items], "document"))])
    index.refresh_index()
    return namespace


class RetrievalTests(unittest.TestCase):
    def test_turkish_tokens_and_exact(self):
        self.assertEqual(_tokens("BaşvuruKanal_L3M"), ["basvuru", "kanal", "l", "3", "m"])
        data = snapshot([record("a", "BaşvuruKanal", "başvuru kanalı"),
                         record("b", "Faiz", "faiz türü")])
        hits = HybridIndex(data).search({"name": "BaşvuruKanal"}, strategy="exact")
        self.assertEqual([item["catalog_id"] for item in hits], ["a"])
        self.assertEqual(HybridIndex(data).search({"name": "BasvuruKanal"}, strategy="exact"), [])
        # Lexicon-normalized channel still finds the accent-folded name in hybrid mode.
        hit = HybridIndex(data).search({"name": "BasvuruKanal"})[0]
        self.assertEqual((hit["catalog_id"], hit["_features"]["name_level"]), ("a", "normalized"))

    def test_scope_filters_before_any_embedding(self):
        embed = HashEmbed()
        data = snapshot([record("a", "HAV_LM", "Havale son ay"),
                         record("b", "EFT_LM", "EFT son ay", schema="B")])
        index = HybridIndex(data, embed=embed)
        self.assertEqual(index.search({"name": "unknown"}, strategy="exact"), [])
        self.assertEqual(index.search({"name": "HAV", "scope": {"schemas": ["C"]}}), [])
        self.assertEqual(embed.calls, [])
        index.search({"name": "HAV_LM", "scope": {"schemas": ["A"]}})
        self.assertNotIn("EFT", str([texts for kind, texts in embed.calls if kind == "query"]))
        self.assertEqual(embed.calls[0][0], "query")

    def test_bm25_and_hybrid_use_related_terms(self):
        data = snapshot([record("a", "FIELD_1", "son hafta farklı kişi sayısı"),
                         record("b", "FIELD_2", "gelen toplam tutar")])
        index = HybridIndex(data)
        self.assertEqual(index.search({"name": "kişi sayısı"}, strategy="bm25")[0]["catalog_id"], "a")
        self.assertIn("bm25", index.search({"name": "kişi sayısı"})[0]["_retrieval"])

    def test_stale_index_detects_deletion_even_if_version_not_bumped(self):
        data = snapshot([record("a", "A", ""), record("b", "B", "")])
        index = HybridIndex(data)
        updated = copy.deepcopy(data)
        updated["records"].pop()
        with self.assertRaises(StaleIndexError):
            index.assert_fresh(updated)
        changed = copy.deepcopy(data)
        changed["records"][0]["column_comment"] = "changed without version bump"
        with self.assertRaises(StaleIndexError):
            index.assert_fresh(changed)

    def test_embedding_requires_adapter(self):
        with self.assertRaises(IntegrationError):
            HybridIndex(snapshot([record("a", "A", "")])).search({"name": "A"}, strategy="embedding")

    def test_document_and_query_text(self):
        index = HybridIndex(snapshot([]))
        self.assertEqual(index.document_text({"column": "ORT_MEVDUAT_BKY_12AY", "column_comment": "",
                                              "table_comment": "ignored"}),
                         "ORT_MEVDUAT_BKY_12AY | ortalama mevduat bakiye son 12 ay")
        self.assertEqual(index.query_text({"name": "AVG_BAL_3M", "description": "x", "category": "c"}),
                         "AVG_BAL_3M | ortalama bakiye son 3 ay | x | c")

    def test_abbreviation_without_comment_enters_pool(self):
        data = snapshot(fillers(300) + [record("target", "AVG_BAL_3M", "")])
        request = {"name": "Son 3 ay ortalama bakiye", "description": ""}
        for embed in (None, HashEmbed()):
            hits = HybridIndex(data, embed=embed).search(request)
            self.assertIn("target", [hit["catalog_id"] for hit in hits])
            hit = next(hit for hit in hits if hit["catalog_id"] == "target")
            self.assertEqual(hit["_features"]["name_level"], "normalized")  # same token multiset
            self.assertEqual(hit["_features"]["coverage"], 1.0)

    def test_subject_coverage_ignores_structural_tokens(self):
        data = snapshot(fillers(50) + [
            record("atm", "ATM_ORT_TUT_3AY", "Son 3 ay ortalama ATM tutarı"),
            record("tah", "TAH_GDN_ADET_6AY", "Müşteri son 6 ay giden tahsilat adedi"),
            record("hvl", "HAVALE_GDN_ADET_6AY", "son 6 ay giden havale adedi")])
        index = HybridIndex(data)
        hits = {h["catalog_id"]: h["_features"] for h in index.search({"name": "GIDEN_HAVALE_ADET_6AY",
                                                                      "description": "Son 6 ayda giden havale adedi"})}
        self.assertEqual(hits["tah"]["subject_coverage"], 0.0)
        self.assertEqual(hits["tah"]["subject_missing"], ["havale"])
        self.assertGreaterEqual(hits["tah"]["coverage"], 0.5)  # structural tokens alone fill coverage
        self.assertEqual(hits["hvl"]["subject_coverage"], 1.0)
        hits = {h["catalog_id"]: h["_features"] for h in index.search({"name": "KRIPTO_ALIM_TUT_3AY",
                                                                      "description": "Son 3 ay kripto para alım tutarı"})}
        self.assertEqual(hits["atm"]["subject_coverage"], 0.0)
        # Only structural tokens in name and description: no subject, gate condition does not apply.
        hits = index.search({"name": "TOP_TUT_3AY", "description": "son 3 ay toplam tutar"})
        self.assertTrue(hits)
        self.assertTrue(all(h["_features"]["subject_coverage"] is None for h in hits))

    def test_generic_only_overlap_never_enters_pool(self):
        data = snapshot(fillers(300) + [record("target", "AVG_BAL_3M", "")])
        hits = HybridIndex(data).search({"name": "Musteri son 3 ay ortalama bakiye"})
        self.assertEqual([hit["catalog_id"] for hit in hits], ["target"])
        self.assertEqual(hits[0]["_features"]["distinctive_matched"], 3)

    def test_pool_is_independent_of_previous_rows(self):
        data = snapshot(fillers(200) + [record("t1", "AVG_BAL_3M", ""),
                                        record("t2", "ORT_MEVDUAT_BKY_12AY", "ortalama mevduat"),
                                        record("t3", "KK_GECIKME_GUN", "kart gecikme")])
        request = {"name": "Son 3 ay ortalama bakiye", "description": "mevduat"}
        others = [{"name": "MUSTERI_SON_AY_TUTAR_%d" % i, "description": "kart gecikme %d" % i}
                  for i in range(49)]
        embed = HashEmbed()
        index = HybridIndex(data, embed=embed)
        namespace = index.namespace
        frozen = [h for h, _ in index.missing_documents()][::2]
        namespace.put_many([(h, hash_vector(index._doc_texts[index._hash_indices[h][0]])) for h in frozen])
        index.refresh_index()
        first = index.search(request)
        for other in others:
            index.search(other)
        later = index.search(request)
        self.assertEqual([h["catalog_id"] for h in first], [h["catalog_id"] for h in later])
        for a, b in zip(first, later):
            fa, fb = a["_features"], b["_features"]
            self.assertEqual((fa["name_level"], fa["coverage"]), (fb["name_level"], fb["coverage"]))
            self.assertEqual(fa["cos"] is None, fb["cos"] is None)
            if fa["cos"] is not None:
                self.assertLess(abs(fa["cos"] - fb["cos"]), 1e-9)
                self.assertLess(abs(fa["z"] - fb["z"]), 1e-9)
        # A fresh index in the other order agrees too.
        fresh = HybridIndex(data, embed=HashEmbed(), namespace=MemoryNamespace())
        fresh.namespace.put_many([(h, hash_vector(fresh._doc_texts[fresh._hash_indices[h][0]])) for h in frozen])
        fresh.refresh_index()
        self.assertEqual([h["catalog_id"] for h in fresh.search(request)], [h["catalog_id"] for h in first])

    def test_semantic_scope_full_partial_none(self):
        data = snapshot([record("a", "AVG_BAL_3M", ""), record("b", "KK_LIMIT", "kart limiti")])
        request = {"name": "ortalama bakiye"}
        index = HybridIndex(data)
        index.search(request)
        self.assertEqual(index.last_search_info["semantic_scope"], "none")
        embed = HashEmbed()
        index = HybridIndex(data, embed=embed)
        index.search(request)
        self.assertEqual(index.last_search_info["semantic_scope"], "partial")
        frozen_namespace(index, embed)
        self.assertEqual(index.index_coverage, 1.0)
        index.search(request)
        self.assertEqual(index.last_search_info["semantic_scope"], "full")
        hit = index.search(request)[0]
        self.assertIsNotNone(hit["_features"]["cos"])
        self.assertIsNotNone(hit["_features"]["z"])
        self.assertIn("semantic", hit["_features"]["channels"])

    def test_embed_failures_degrade_and_open_circuit(self):
        embed = HashEmbed(fail=True)
        index = HybridIndex(snapshot([record("a", "LIMIT", "kredi limiti")]), embed=embed)
        for number in range(5):
            hits = index.search({"name": "LIMIT", "description": "item %d" % number})
            self.assertEqual(hits[0]["catalog_id"], "a")
            self.assertTrue(index.last_search_info["warning"])
            self.assertNotIn("provider-secret", index.last_search_info["warning"])
            self.assertEqual(index.last_search_info["semantic_scope"], "none")
            self.assertEqual(index.stats["circuit_open"], number >= 2)
        self.assertEqual(len(embed.calls), 3)
        self.assertEqual(index.stats["embedding_failures"], 3)

    def test_same_document_text_in_two_tables_embeds_once(self):
        embed = HashEmbed()
        data = snapshot([record("a", "KK_LIMIT", "kart limiti", obj="T1"),
                         record("b", "KK_LIMIT", "kart limiti", obj="T2")])
        index = HybridIndex(data, embed=embed, background_size=0)
        hits = index.search({"name": "KK_LIMIT"})
        # Same (column, comment): one pool entry, the copy travels as a location.
        self.assertEqual([(hit["catalog_id"], hit["_locations"]) for hit in hits], [("a", ["A.T2"])])
        documents = [texts for kind, texts in embed.calls if kind == "document"]
        self.assertEqual(sum(len(texts) for texts in documents), 1)
        self.assertEqual(index.stats["document_calls"], 1)
        self.assertEqual(len(index.missing_documents()), 0)


def card_limit_embed(texts, kind):
    """Kredi kartı limiti metinleri ortak bir yöne, diğerleri hash yönüne gider."""
    out = []
    for text in texts:
        folded = text.casefold()
        if "kredi kart" in folded and "limit" in folded:
            out.append([1.0] * 16)
        else:
            out.append([b - 127.5 for b in hashlib.sha256(text.encode("utf-8")).digest()[:16]])
    return out


class QualityFixTests(unittest.TestCase):
    def test_duplicates_do_not_crowd_pool(self):
        records = [record("h%02d" % i, "KREDI_KARTI_BAKIYE", "Kredi kartı bakiyesi", obj="HIST%02d" % i)
                   for i in range(45)]
        records.append(record("main", "KK_GUNCEL_BAKIYE", "Kredi kartı güncel bakiye tutarı", obj="MAIN"))
        req = {"name": "Kredi kartı bakiyesi", "description": ""}
        index = HybridIndex(snapshot(records))
        pool = index.search(req)
        self.assertEqual(sorted(hit["column"] for hit in pool), ["KK_GUNCEL_BAKIYE", "KREDI_KARTI_BAKIYE"])
        duplicate = next(hit for hit in pool if hit["column"] == "KREDI_KARTI_BAKIYE")
        self.assertEqual(duplicate["catalog_id"], "h00")
        self.assertEqual(len(duplicate["_locations"]), 44)
        result = rank(req, pool, semantic_scope=index.last_search_info["semantic_scope"])
        self.assertEqual(len(result["candidates"]), 2)
        shown = next(c for c in result["candidates"] if c["catalog_id"] == "h00")
        self.assertEqual(len(shown["locations"]), 44)
        self.assertEqual(shown["locations"][0], "A.HIST01")

    def test_single_word_request_without_embedding_gives_weak_candidate(self):
        data = snapshot([record("a", "MUSTERI_BAKIYE", "Müşteri hesap bakiyesi", obj="T1"),
                         record("b", "X", "y", obj="T2")])
        pool = HybridIndex(data).search({"name": "Bakiye", "description": ""})
        self.assertIsNone(pool[0]["_features"]["z"])
        result = rank({"name": "Bakiye", "description": ""}, pool, semantic_scope="none")
        self.assertEqual([(c["catalog_id"], c["meaning"]) for c in result["candidates"]], [("a", "weak")])

    def test_single_token_rule_needs_token_in_column_name(self):
        data = snapshot([record("a", "MUSTERI_SEGMENT", "Müşteri bakiyesi segmenti", obj="T1")])
        pool = HybridIndex(data).search({"name": "Bakiye", "description": ""})
        self.assertIsNone(pool[0]["_features"]["query_tokens"])
        self.assertEqual(rank({"name": "Bakiye", "description": ""}, pool, semantic_scope="none")["candidates"], [])

    def test_lexical_coverage_keeps_structural_tokens(self):
        # Ölçümde yapısalsız kapsam none modunda ORT/MAX ve GELEN/GIDEN'i karıştırdı (P@3 0.99 -> 0.876).
        data = snapshot([record("a", "HESAP_BAKIYE", "Hesap bakiyesi")])
        pool = HybridIndex(data).search({"name": "Hesap bakiyesi tutarı", "description": ""})
        self.assertAlmostEqual(pool[0]["_features"]["coverage"], 2 / 3)

    def test_small_catalog_can_reach_likely_with_leave_one_out_z(self):
        records = [record("t", "KK_LIMIT", "Kredi kartı limiti")]
        records += [record("o%d" % i, "COL_%d" % i, "diğer bilgi %d" % i) for i in range(5)]
        index = HybridIndex(snapshot(records), embed=card_limit_embed)
        index.embed_all()
        req = {"name": "Kredi kartı limiti", "description": "kredi kartı limit"}
        pool = index.search(req)
        target = next(hit for hit in pool if hit["catalog_id"] == "t")
        self.assertGreaterEqual(target["_features"]["z"], 2.5)
        self.assertTrue(target["_features"]["background_small"])
        result = rank(req, pool, semantic_scope=index.last_search_info["semantic_scope"], catalog_complete=True)
        self.assertEqual([(c["catalog_id"], c["meaning"]) for c in result["candidates"]], [("t", "likely")])
        self.assertIn("Katalog küçük; anlamsal benzerlik ölçeği sınırlı.", result["notes"])

    def test_unscoped_search_matches_explicit_full_scope(self):
        data = snapshot([record("a", "KK_LIMIT", "kart limiti"), record("b", "KK_LIMIT_2", "kart limiti", schema="B")])
        index = HybridIndex(data)
        plain = index.search({"name": "KK_LIMIT"})
        scoped = index.search({"name": "KK_LIMIT", "scope": {"schemas": ["A", "B"]}})
        self.assertEqual([hit["catalog_id"] for hit in plain], [hit["catalog_id"] for hit in scoped])
        self.assertEqual([hit["catalog_id"] for hit in index.search({"name": "KK_LIMIT", "schema": "B"})], ["b"])


def table_record(identifier, column, comment, obj, table_comment, schema="A"):
    item = record(identifier, column, comment, schema=schema, obj=obj)
    item["table_comment"] = table_comment
    return item


def table_catalog():
    records = [table_record("g1", "C_017", "", "KART_GECIKME", "Kredi kartı gecikme bilgileri"),
               table_record("g2", "GECIKME_GUN_SAYI", "gecikme gün sayısı", "KART_GECIKME",
                            "Kredi kartı gecikme bilgileri")]
    records += [table_record("m%02d" % i, "MUSTERI_YAS_%d" % i, "müşteri yaşı", "MUSTERI_DEMOGRAFI",
                             "Müşteri demografik bilgileri") for i in range(12)]
    # Kolon adı/yorumu talebe daha yakın dolgu: lexical_k=5 iken kolon BM25'ini doldurur.
    records += [table_record("d%02d" % i, "KART_GECIKME_ADET_%d" % i, "kart gecikme adedi", "DIGER",
                             "Diğer bilgiler") for i in range(10)]
    return snapshot(records)


class TableChannelTests(unittest.TestCase):
    REQUEST = {"name": "Kart gecikme", "description": "Kredi kartı gecikme durumu"}

    def test_weak_column_of_matching_table_enters_pool(self):
        index = HybridIndex(table_catalog(), embed=HashEmbed(), lexical_k=5)
        pool = index.search(self.REQUEST)
        hit = next(item for item in pool if item["catalog_id"] == "g1")
        self.assertIn("table", hit["_features"]["channels"])  # sözcük kanalı tablo adı/açıklamasını da indeksler (mevcut davranış)
        self.assertEqual(hit["_features"]["table_rank"], 1)
        self.assertIsNotNone(hit["_features"]["table_score"])
        self.assertIsNotNone(hit["_features"]["cos"])
        self.assertEqual(index.last_search_info["table_channel"]["tables"][0], "A.KART_GECIKME")

    def test_table_vectors_give_semantic_mode(self):
        embed = HashEmbed()
        index = HybridIndex(table_catalog(), embed=embed)
        index.embed_all()
        index.search(self.REQUEST)
        self.assertEqual(index.last_search_info["table_channel"]["mode"], "semantic")
        self.assertEqual(index.last_search_info["table_channel"]["tables"][0], "A.KART_GECIKME")
        self.assertIn(index.table_document_text("A", "KART_GECIKME", "Kredi kartı gecikme bilgileri"),
                      [text for kind, texts in embed.calls if kind == "document" for text in texts])

    def test_bm25_table_channel_without_embedding(self):
        index = HybridIndex(table_catalog(), lexical_k=5)
        pool = index.search(self.REQUEST)
        self.assertEqual(index.last_search_info["table_channel"], {"tables": ["A.KART_GECIKME"], "mode": "lexical"})
        hit = next(item for item in pool if item["catalog_id"] == "g2")
        self.assertIn("table", hit["_features"]["channels"])
        self.assertIn("table", hit["_retrieval"])
        self.assertEqual(hit["_features"]["table_rank"], 1)
        self.assertIsNotNone(hit["_features"]["table_score"])
        g1 = next(item for item in pool if item["catalog_id"] == "g1")
        self.assertIn("lexical", g1["_features"]["channels"])  # tablo adı/açıklaması ağırlık 1 ile sözcük kanalında (mevcut davranış)

    def test_table_channel_off(self):
        index = HybridIndex(table_catalog(), table_k=0)
        pool = index.search(self.REQUEST)
        self.assertEqual(index.last_search_info["table_channel"], {"tables": [], "mode": "off"})
        self.assertTrue(all(item["_features"]["table_rank"] is None for item in pool))
        index = HybridIndex(table_catalog())
        index.search({"name": "GECIKME_GUN_SAYI"}, strategy="exact")
        self.assertEqual(index.last_search_info["table_channel"]["mode"], "off")

    def test_on_the_fly_table_embed_is_capped(self):
        records = [table_record("k%02d" % i, "C_%02d" % i, "", "KART_GECIKME", "Kredi kartı gecikme bilgileri")
                   for i in range(30)]
        for cap in (5, 0):
            embed = HashEmbed()
            index = HybridIndex(snapshot(records), embed=embed, table_embed=cap, shortlist_embed=0,
                                background_size=0)
            pool = index.search(self.REQUEST)
            documents = [text for kind, texts in embed.calls if kind == "document" for text in texts]
            self.assertEqual(len(documents), cap)
            self.assertEqual(sum(item["_features"]["cos"] is not None for item in pool), cap)

    def test_on_the_fly_failure_falls_back_to_lexical_tables(self):
        index = HybridIndex(table_catalog(), embed=HashEmbed(fail=True))
        pool = index.search(self.REQUEST)
        self.assertTrue(index.last_search_info["embedding_skipped"])
        self.assertEqual(index.last_search_info["table_channel"]["mode"], "lexical")
        self.assertIn("g2", [item["catalog_id"] for item in pool])

    def test_excluded_objects_never_enter_index(self):
        scope = dict(SCOPE, schemas=["DM_CAD"])
        records = [table_record("out", "KK_LIMIT", "kart limiti", "OUT_SKOR", "", schema="DM_CAD"),
                   table_record("inp", "KK_LIMIT_X", "kart limiti", "inp_ara", "", schema="DM_CAD"),
                   table_record("ok", "KK_LIMIT_2", "kart limiti", "FEATURES", "", schema="DM_CAD")]
        data = {"scope": scope, "records": records, "version": "v1", "complete": True}
        embed = HashEmbed()
        index = HybridIndex(data, embed=embed)
        index.embed_all()
        self.assertEqual([r["catalog_id"] for r in index.records], ["ok"])
        self.assertEqual(len(index.missing_documents()), 0)
        self.assertNotIn("OUT_SKOR", str(embed.calls))
        self.assertNotIn("out", [hit["catalog_id"] for hit in index.search({"name": "KK_LIMIT"})])
        self.assertEqual(index.search({"name": "KK_LIMIT"}, strategy="lexical")[0]["catalog_id"], "ok")
        index = HybridIndex(data, exclude_objects=[])
        self.assertEqual(index.search({"name": "KK_LIMIT"})[0]["catalog_id"], "out")
        with self.assertRaises(InputError):
            HybridIndex(data, exclude_objects=[{"schema": "DM_CAD"}])
        # Taramanın kuralı snapshot kapsamında gelir; indeks aynı kuralı uygular.
        scanned = dict(data, scope=dict(scope, exclude_objects=[]))
        self.assertEqual(HybridIndex(scanned).search({"name": "KK_LIMIT"})[0]["catalog_id"], "out")

    def test_index_queue_puts_tables_first_then_tiers(self):
        scope = dict(SCOPE, schemas=["EDW", "DM_CAD", "Z"])
        records = [table_record("z", "Z_COL", "", "ZT", "", schema="Z"),
                   table_record("e", "E_COL", "", "ET", "", schema="EDW"),
                   table_record("d", "D_COL", "", "DT", "", schema="DM_CAD")]
        index = HybridIndex({"scope": scope, "records": records, "version": "v1", "complete": True},
                            embed=HashEmbed())
        queue = [text.split(" | ")[0] for _h, text in index.index_queue()]
        self.assertEqual(queue, ["DT", "ET", "ZT", "D_COL", "E_COL", "Z_COL"])
        self.assertEqual(index.document_text(records[0]).split(" | ")[0], "Z_COL")


if __name__ == "__main__":
    unittest.main()
