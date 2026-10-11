"""Retrieval ablation harness. Synthetic labels do not measure bank accuracy."""
from .demo import DemoEmbedding,demo_snapshot
from .retrieval import HybridIndex

def evaluate(cases=None,snapshot=None,embedding=None):
    snapshot=snapshot or demo_snapshot()
    by_column={r["column"]:r["catalog_id"] for r in snapshot["records"]}
    cases=cases or [
        {"name":"FaizTuru","description":"Başvuru bazında faiz türü sabit faiz değişken faiz","expected_ids":[by_column["INTEREST_TYPE_CODE"]]},
        {"name":"BeyanGelir","description":"Başvuru bazında beyan edilen gelir TRY","expected_ids":[by_column["DECLARED_INCOME_TRY"]]},
        {"name":"RequestedLimit","description":"Başvurulan limit","expected_ids":[by_column["REQUESTED_LIMIT_AMOUNT"]]},
    ]
    index=HybridIndex(snapshot,embedding=embedding or DemoEmbedding())
    report={"dataset":"synthetic-demo-v1","warning":"Yalnız sentetik retrieval kablo testi. Hash vektörleri Qwen değildir; banka doğruluk oranı değildir.","strategies":{}}
    for strategy in ("bm25","embedding","hybrid"):
        hit=0
        reciprocal_rank=0
        details=[]
        for case in cases:
            found=index.search(case,limit=5,strategy=strategy)
            ranks=[i+1 for i,r in enumerate(found) if r["catalog_id"] in case["expected_ids"]]
            hit+=bool(ranks)
            reciprocal_rank+=1/min(ranks) if ranks else 0
            details.append({"name":case["name"],"retrieved":[r["catalog_id"] for r in found],"first_expected_rank":min(ranks) if ranks else None})
        report["strategies"][strategy]={"cases":len(cases),"hit_at_5":hit/len(cases),"mrr_at_5":reciprocal_rank/len(cases),"details":details}
    return report
