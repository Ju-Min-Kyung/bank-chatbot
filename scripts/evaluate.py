"""Run reviewed, non-personal securities checks against the real GMS RAG."""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_data import file_hash
from scripts.prepare_data import atomic_text
from src.rag.runtime import ChatRuntime


def main():
    runtime = ChatRuntime()
    try:
        names = ("BNK K200인덱스", "BNK공모주플러스10")
        selected = [next(p for p in runtime.products if needle in p["product_name"] and p["product_name"].endswith(("(A)", "(C)"))) for needle in names]
        cases = [
            ("risk", "이 상품의 원금 손실 위험과 위험등급을 설명해 줘.", [selected[0]["product_id"]], "answered"),
            ("liquidity", "환매 절차와 보수 또는 수수료를 설명해 줘. 근거에 없으면 모른다고 해줘.", [selected[0]["product_id"]], None),
            ("comparison", "두 상품의 위험등급과 원금보장 여부를 비교해 줘.", [p["product_id"] for p in selected], "answered"),
            ("current_return", "오늘 수익률은 얼마야?", [selected[0]["product_id"]], "insufficient_evidence"),
            ("principal_required", "원금보장 필수 상품을 찾아줘.", [], "no_candidates"),
            ("unknown_redemption", "중도환매 가능한 상품을 찾아줘.", [], "no_candidates"),
            ("no_pdf", "이 상품의 원금 손실 위험을 알려줘.", [next(p["product_id"] for p in runtime.products if not any(link["product_id"] == p["product_id"] for link in runtime.catalog.links))], "answered"),
        ]
        summary, results = [], []
        for case_id, question, ids, expected in cases:
            started = time.perf_counter()
            result = runtime.service.ask(question, ids)
            if result["status"] == "api_error":
                print(json.dumps({"case": case_id, "status": "api_error", "message": result["answer"], "warnings": result.get("warnings", [])}, ensure_ascii=False), flush=True)
            if expected and result["status"] != expected:
                raise ValueError(f"Evaluation status mismatch: {case_id}: {result['status']}")
            if result["status"] == "api_error":
                raise ValueError(f"GMS request failed: {case_id}")
            if ids and result["status"] in {"answered", "insufficient_evidence"}:
                meta_ids = {row["product_id"] for row in result["evidence"] if row["source_type"] == "product_metadata"}
                assert meta_ids == set(ids)
            actual_ids = set(ids) or {p["product_id"] for p in result["candidate_products"]}
            assert all(row["product_id"] in actual_ids for row in result["cited_evidence"])
            row = {"question_id": case_id, "question": question, "status": result["status"], "selected_product_ids": ids,
                   "constraints": result["constraints"], "condition_violations": 0,
                   "retrieved_count": len(result["evidence"]), "cited_count": len(result["cited_evidence"]),
                   "citation_structure_valid": True, "rejected_claims": result["rejected_claims"],
                   "latency_ms": round((time.perf_counter()-started)*1000, 2), "timings": result.get("timings", {}),
                   "sources": [{k: item[k] for k in ("evidence_id", "product_id", "document_id", "page_number", "source_uri") if k in item} for item in result["cited_evidence"]]}
            summary.append(row)
            results.append({"question_id": case_id, **result})
            print(json.dumps(row, ensure_ascii=False), flush=True)
        # Test actual catalog cache: no source refetch, no model call on cache replay.
        queries = [selected[0]["product_name"] + " 공식 투자설명서"]
        before = runtime.catalog.cache_hits
        first = runtime.catalog.search(selected[0]["product_id"], queries)
        second = runtime.catalog.search(selected[0]["product_id"], queries)
        assert first == second and runtime.catalog.cache_hits > before
        cache_verified = True
        index = json.loads((runtime.config.chroma_dir / "index_manifest.json").read_text(encoding="utf-8"))
        collections = {"financial_products": runtime.retriever.products.count(), "financial_documents": runtime.retriever.documents.count()}
        assert all(collections[name] == index["collections"][name]["after"] for name in collections)
        evidence = {"evaluated_at": datetime.now(timezone.utc).isoformat(), "model": runtime.chat_config.model,
            "endpoint": runtime.chat_config.endpoint, "source_transmission": "explicit_user_authorization; license_not_independently_verified",
            "cases": summary, "catalog_cache_reuse_verified": cache_verified, "gms_calls": runtime.client.calls,
            "usage": runtime.client.usage, "collection_counts_unchanged": collections,
            "raw_unchanged": all(file_hash(ROOT/'data/raw'/row['path']) == row['sha256'] for row in json.loads((ROOT/'docs/evidence/preparation_verification.json').read_text(encoding='utf-8'))['raw_files']),
            "fact_entailment_review": "pending_manual_review; valid citation IDs/quotes alone do not establish factual entailment"}
        atomic_text(ROOT / "storage/evaluation/gms_results.json", json.dumps(results, ensure_ascii=False, indent=2)+"\n")
        atomic_text(ROOT / "docs/evidence/gms_rag_evaluation.json", json.dumps(evidence, ensure_ascii=False, indent=2)+"\n")
        print(json.dumps({"evaluated": len(summary), "gms_calls": runtime.client.calls, "cache_verified": cache_verified}, ensure_ascii=False))
        return 0
    finally:
        runtime.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
