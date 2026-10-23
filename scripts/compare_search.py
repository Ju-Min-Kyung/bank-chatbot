"""F2014: same-pool similarity/MMR comparison on the real local vector DB."""
import argparse
import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import load_index_config
from scripts.build_indexes import offline_network
from scripts.prepare_data import atomic_text
from src.ingestion.embeddings import LocalEmbedder
from src.rag.retriever import VectorRetriever


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", default="원금 손실 위험, 환매 방법, 보수와 수수료를 설명해 줘.")
    parser.add_argument("--product-id", action="append", default=[])
    parser.add_argument("--fetch-k", type=int, default=30)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--lambda-mult", type=float, default=0.5)
    args = parser.parse_args()
    config = replace(load_index_config(), workers=1)
    products = [json.loads(line) for line in config.products.read_text(encoding="utf-8").splitlines()]
    ids = args.product_id or [next(p["product_id"] for p in products if p["product_name"] == "BNK K200인덱스증권투자신탁(주식-파생형)(A)")]
    started = time.perf_counter()
    embedder = retriever = None
    with offline_network():
        try:
            embedder = LocalEmbedder(config)
            retriever = VectorRetriever(config, embedder)
            before = {"financial_products": retriever.products.count(), "financial_documents": retriever.documents.count()}
            report = retriever.compare_documents(args.question, ids, document_top_k=args.top_k, fetch_k=args.fetch_k, lambda_mult=args.lambda_mult)
            for mode in ("similarity", "mmr"):
                evidence = retriever.retrieve(args.question, ids, args.top_k, search_mode=mode, fetch_k=args.fetch_k, lambda_mult=args.lambda_mult)
                assert [row["product_id"] for row in evidence if row["source_type"] == "product_metadata"] == list(dict.fromkeys(ids))
                expected = [row["record_id"] for row in report["results"][mode]["rows"]]
                assert [row["record_id"] for row in evidence if row["source_type"] == "official_pdf"] == expected
                assert all(row["product_id"] in ids for row in evidence)
            after = {"financial_products": retriever.products.count(), "financial_documents": retriever.documents.count()}
            assert before == after
        finally:
            if retriever:
                retriever.client.close()
            if embedder:
                embedder.close()
    # Store source metadata and small previews, not complete copyrighted PDFs.
    for mode in ("similarity", "mmr"):
        for row in report["results"][mode]["rows"]:
            row["preview"] = row.pop("content")[:180]
    report.update(evaluated_at=datetime.now(timezone.utc).isoformat(), question=args.question, product_ids=ids,
                  latency_ms=round((time.perf_counter()-started)*1000, 2), local_only=True, gms_calls=0,
                  selected_metadata_preserved=True, source_filter_preserved=True, collection_counts_unchanged=after)
    atomic_text(ROOT / "docs/evidence/mmr_comparison.json", json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    lines = ["# F2014 — 유사도/MMR 실제 검색 비교", "", f"검증 시각(UTC): {report['evaluated_at']}", "",
             f"질문: {args.question}", f"상품 ID: {', '.join(ids)}", "",
             f"동일한 후보 집합에서 두 검색 방식을 비교했습니다. 후보 상한 {args.fetch_k}, 중복 제거 후 실제 후보 {report['unique_candidates']}, 최종 선택 상한 {args.top_k}, lambda={args.lambda_mult}입니다.", "",
             "| 지표 | 유사도 | MMR |", "| --- | --- | --- |"]
    labels = {"selected_count": "선택 청크", "unique_documents": "서로 다른 PDF", "unique_pages": "서로 다른 페이지", "exact_text_duplicates": "완전히 같은 본문 중복",
              "mean_pairwise_cosine": "청크 간 평균 코사인 유사도", "max_pairwise_cosine": "청크 간 최대 코사인 유사도", "mean_character_4gram_jaccard": "문자 4-gram 평균 Jaccard"}
    for key, label in labels.items():
        values = [report["results"][mode]["metrics"][key] for mode in ("similarity", "mmr")]
        values = [f"{v:.6f}" if isinstance(v, float) else str(v) for v in values]
        lines.append(f"| {label} | {values[0]} | {values[1]} |")
    lines += ["", f"선택 집합의 공통 ID {report['shared_selected_ids']}개, 대칭 차집합 {report['different_selected_ids']}개입니다.", "",
              "## 선택된 출처 순서", "", "| 방식 | 선택 순서 | 원래 후보 순위 | 문서 | PDF 페이지 |", "| --- | --- | --- | --- | --- |"]
    for mode in ("similarity", "mmr"):
        for row in report["results"][mode]["rows"]:
            lines.append(f"| {mode} | {row['selection_rank']} | {row['candidate_rank']} | [{row['title']}]({row['source_uri']}) | {row['page_number']} |")
    lines += ["", "## 구현과 해석", "",
              "MMR은 첫 청크를 질문 유사도가 가장 높은 것으로 고른 후, lambda×질문 코사인 유사도 − (1−lambda)×기선택 청크와의 최대 코사인 유사도를 반복 최대화합니다.",
              "lambda=1은 관련성 순서, 0은 다양성을 강조하며 기본값은 0.5입니다. 거리(distance)는 원래 질문과의 거리로 유지하고 MMR 선택 순위와 구분합니다.",
              "완전히 같은 정규화 본문은 상품 내부에서 먼저 중복 제거합니다. 서로 다른 상품의 문서 관계는 보존합니다.",
              "채팅에서는 상품마다 후보를 fetch_k까지 가져와 배분된 k를 고른 뒤 상품별로 번갈아 병합합니다. 따라서 여러 상품을 비교하면 전체 후보 예산이 상품 수×fetch_k가 될 수 있습니다.",
              "후보가 적으면 k보다 적게 반환하며 근거를 복제해 채우지 않습니다. 상품 메타데이터는 MMR과 별도로 항상 포함됩니다.",
              "평균 청크 유사도/문자 중복이 낮으면 이 후보 집합에서 내용 겹침이 줄었다는 뜻이며, 질문 관련성·정답 품질 향상을 자동으로 보장하지 않습니다. 최대 유사도가 높을 수 있고 모든 유사 청크를 제거한다고 주장하지 않습니다.",
              "이번 검증은 로컬 검색만 수행했으며 GMS를 호출하거나 기존 벡터를 다시 임베딩하지 않았습니다. 신규 질문 벡터만 로컬 생성/캐시합니다.", "",
              "증거: [mmr_comparison.json](evidence/mmr_comparison.json). 화면의 '검색 방식 비교' 탭에서 같은 설정으로 재현할 수 있습니다.", "",
              "[MMR 파라미터 공식 문서](https://reference.langchain.com/python/langchain-chroma/vectorstores/Chroma/max_marginal_relevance_search)", ""]
    atomic_text(ROOT / "docs/mmr_comparison.md", "\n".join(lines))
    print(json.dumps({"fetch_k": args.fetch_k, "k": args.top_k, "unique_candidates": report["unique_candidates"],
        "metrics": {mode: report["results"][mode]["metrics"] for mode in ("similarity", "mmr")},
        "shared_selected_ids": report["shared_selected_ids"], "gms_calls": 0}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
