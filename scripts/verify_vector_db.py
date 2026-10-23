"""Verify stored vectors, provenance, local retrieval and two-run index stability."""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import load_index_config
from scripts.audit_data import canonical_hash, file_hash
from scripts.build_indexes import offline_network
from scripts.prepare_data import atomic_text
from src.ingestion.embeddings import LocalEmbedder
from src.ingestion.vector_store import persistent_client
from src.rag.retriever import VectorRetriever


def verify():
    import numpy as np
    config = load_index_config()
    manifest = json.loads((config.chroma_dir / "index_manifest.json").read_text(encoding="utf-8"))
    first_file = ROOT / "docs/evidence/vector_index_first_run.json"
    if not first_file.exists():
        raise ValueError("Save the first successful index manifest before the second build")
    first = json.loads(first_file.read_text(encoding="utf-8"))
    if first["embedding_signature"] != manifest["embedding_signature"]:
        raise ValueError("Model signature changed between builds")
    stability = {}
    for name in ("financial_products", "financial_documents"):
        previous, current = first["collections"][name], manifest["collections"][name]
        assert previous["after"] == current["after"]
        assert previous["ids_sha256"] == current["ids_sha256"]
        assert current["upserted"] == 0 and current["stale_removed"] == 0
        stability[name] = {"count_first": previous["after"], "count_second": current["after"], "delta": 0,
                           "second_upserted": 0, "ids_identical": True}
    pages = {}
    for path in (config.products.parent / "document_text_cache").glob("*.json"):
        cache = json.loads(path.read_text(encoding="utf-8"))
        for i, text in enumerate(cache["pages"], 1):
            pages[(path.stem, i)] = text
    verified = Counter()
    embedder = None
    client = None
    with offline_network():
        try:
            embedder = LocalEmbedder(config)
            client = persistent_client(config.chroma_dir)
            for name in ("financial_products", "financial_documents"):
                collection = client.get_collection(name, embedding_function=None)
                for offset in range(0, collection.count(), 256):
                    batch = collection.get(limit=256, offset=offset, include=["documents", "metadatas", "embeddings"])
                    for key, text, metadata, vector in zip(batch["ids"], batch["documents"], batch["metadatas"], batch["embeddings"]):
                        assert vector.shape == (384,) and np.isfinite(vector).all()
                        assert abs(float(np.linalg.norm(vector)) - 1.0) < 0.001
                        values = {k: v for k, v in metadata.items() if k != "index_record_hash"}
                        assert canonical_hash({"text": text, "metadata": values, "embedding_signature": embedder.signature}) == metadata["index_record_hash"]
                        if name == "financial_products":
                            assert metadata["source_type"] == "product_metadata" and key == metadata["product_id"]
                        else:
                            assert metadata["source_type"] == "official_pdf"
                            assert 0 < metadata["token_count"] <= 128
                            page = pages[(metadata["document_id"], metadata["page_number"])]
                            assert page[metadata["char_start"]:metadata["char_end"]] == text
                        verified[name] += 1
            product_collection = client.get_collection("financial_products", embedding_function=None)
            assert product_collection.get(where={"principal_protected": True}, include=[])["ids"] == []
            assert len(product_collection.get(where={"principal_protected": False}, include=[])["ids"]) == 8000
            retriever = VectorRetriever(config, embedder)
            products = [json.loads(line) for line in config.products.open(encoding="utf-8")]
            targets = []
            for needle in ("BNK K200인덱스", "BNK공모주플러스10", "BNK튼튼배당증권자", "BNK삼성전자중소형", "BNK법인MMF"):
                matches = [p for p in products if needle in p["product_name"] and p["product_name"].endswith(("(A)", "(C)", "(A1)"))]
                if not matches:
                    raise ValueError("Missing verification product")
                targets.append(matches[0])
            retrieval_checks = []
            for target in targets:
                started = time.perf_counter()
                evidence = retriever.retrieve("환매 방법, 위험, 보수와 수수료를 설명해 줘", [target["product_id"]], document_top_k=5)
                assert evidence[0]["source_type"] == "product_metadata" and evidence[0]["product_id"] == target["product_id"]
                docs = [row for row in evidence if row["source_type"] == "official_pdf"]
                assert 0 < len(docs) <= 5 and all(row["product_id"] == target["product_id"] for row in docs)
                retrieval_checks.append({"product_id": target["product_id"], "product_name": target["product_name"],
                                         "metadata_included": True, "document_hits": len(docs), "cross_product_evidence": False,
                                         "sources": [{k: row[k] for k in ("document_id", "page_number", "source_uri")} for row in docs],
                                         "latency_ms": round((time.perf_counter()-started)*1000, 2)})
            one = retriever.retrieve("환매 방법", [targets[0]["product_id"]], document_top_k=1)
            assert len(one) == 2
            comparison = retriever.retrieve("보수 비교", [targets[0]["product_id"], targets[1]["product_id"]], document_top_k=3)
            assert len(comparison) == 5 and all(row["source_type"] == "product_metadata" for row in comparison[:2])
            name_query = "BNK K200인덱스증권투자신탁"
            semantic = retriever.search_products(name_query, top_k=5, name_first=False)
            assert len(semantic) == 5
            semantic_family_found = any("K200인덱스" in row["product_name"] for row in semantic)
            named = retriever.search_products(name_query, top_k=5)
            named_hits = [row for row in named if row["match_type"] == "official_name"]
            expected_ids = {p["product_id"] for p in products if name_query in p["product_name"]}
            assert len(named) == 5 and len(expected_ids) == 4
            assert {row["product_id"] for row in named_hits} == expected_ids
            assert all(row["match_type"] == "official_name" for row in named[:4])
            retriever.client.close()
        finally:
            if client:
                client.close()
            if embedder:
                embedder.close()
    prep_evidence = json.loads((ROOT / "docs/evidence/preparation_verification.json").read_text(encoding="utf-8"))
    for raw in prep_evidence["raw_files"]:
        assert file_hash(ROOT / "data/raw" / raw["path"]) == raw["sha256"]
    assert file_hash(config.products) == prep_evidence["outputs"]["products.jsonl"]["sha256"]
    external = json.loads((config.external_dir / "manifest.json").read_text(encoding="utf-8"))
    for document in external["documents"]:
        assert file_hash(config.external_dir / document["source_file"]) == document["sha256"]
    evidence = {"verified_at": datetime.now(timezone.utc).isoformat(), "model": manifest["embedding_model"],
                "dimension": manifest["embedding_dimension"], "signature": manifest["embedding_signature"],
                "collections": stability, "all_vectors_verified": dict(verified),
                "document_preparation": manifest["document_preparation"], "retrieval_checks": retrieval_checks,
                "selected_product_metadata_always_included": True, "top_k_change_verified": True,
                "comparison_metadata_included": True, "source_page_text_slices_verified": True,
                "named_product_search": {"query": "BNK K200인덱스증권투자신탁", "target_family_in_top5": semantic_family_found,
                                         "results": [{"product_id": row["product_id"], "product_name": row["product_name"], "distance": row["distance"]} for row in semantic]},
                "official_name_priority_search": {"target_family_in_top5": True, "official_name_hits": len(named_hits),
                    "results": [{k: row[k] for k in ("product_id", "product_name", "match_type", "distance")} for row in named]},
                "raw_and_normalized_inputs_unchanged": True, "all_downloaded_pdfs_unchanged": True,
                "principal_protection_metadata_filter_verified": True,
                "python_network_blocked": True, "external_embedding_api_calls": 0,
                "second_build_embedding_stats": manifest["embedding_stats_this_run"],
                "limitations": ["Semantic retrieval is not an eligibility/suitability decision", "Semantic relevance needs separate evaluation", "Only 23 products have linked PDFs",
                                "No LangGraph generation or user interface implemented"]}
    atomic_text(ROOT / "docs/evidence/vector_index_verification.json", json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    write_report(manifest, evidence)
    print(json.dumps({"verified_vectors": dict(verified), "idempotency": stability, "named_product_family_in_top5": semantic_family_found}, ensure_ascii=False, indent=2))


def write_report(manifest, evidence):
    lines = ["# 로컬 임베딩 및 Chroma 구축 검증", "", f"검증 시각(UTC): {evidence['verified_at']}", "",
             f"임베딩: `{manifest['embedding_model']}` / {manifest['embedding_dimension']}차원 / ONNX CPU / `{manifest['model_license']}`.",
             f"모델 revision: `{manifest['model_revision']}`", "",
             "금융 본문을 외부 API에 전송하지 않고 로컬에서 처리했습니다. Python 네트워크를 차단하고 Chroma/Hugging Face/ONNX 텔레메트리를 비활성화했습니다.", "",
             "## 두 컬렉션 및 재실행", "", "| 컬렉션 | 첫 성공 스냅샷 | 두 번째 실행 | 증가 | 두 번째 upsert |", "| --- | --- | --- | --- | --- |"]
    for name, row in evidence["collections"].items():
        lines.append(f"| {name} | {row['count_first']:,} | {row['count_second']:,} | 0 | 0 |")
    stats = manifest["document_preparation"]
    lines += ["", "상품은 1상품=1검색 텍스트=1벡터입니다. 긴 상품 텍스트는 모든 토큰 구간을 임베딩한 후 평균·L2 정규화하며 일부를 조용히 잘라내지 않습니다.",
              f"문서는 실제 PDF {stats['documents']}개, {stats['pages']}페이지의 고유 청크 {stats['unique_chunks']:,}개입니다.",
              f"클래스별 상품 ID {stats['linked_products']}개와의 관계를 보존해 Chroma에는 {stats['product_chunk_records']:,}개 상품-청크 행을 저장합니다. 서로 다른 PDF {stats['product_chunk_records']:,}개라는 뜻이 아닙니다.",
              "동일 청크의 임베딩은 캐시로 공유하고 product_id별 색인 ID는 분리합니다. 변경·정정대비표 10개는 근거 색인에서 제외했습니다.",
              f"청크 상한 설정은 {manifest['chunk_size_chars']}자이며 모델 한도 {manifest['max_tokens']}토큰을 우선합니다. 실측 최대 {stats['max_chunk_chars']}자 / {stats['max_chunk_tokens']}토큰입니다.",
              "겹침은 min(설정값 200자, 실제 청크 길이의 20%)입니다. 페이지 경계를 넘지 않으며 물리 PDF 페이지(1부터), 문자 시작/끝, 원문 URI를 저장합니다.", "",
              "## 검증 결과", "",
              "- 모든 벡터의 차원·유한값·L2 길이와 저장 레코드 해시 확인.",
              "- 모든 문서 청크가 추출된 해당 PDF 페이지의 정확한 문자 범위와 일치함을 확인.",
              "- 선택 상품 메타데이터 필수 포함, 다른 상품 문서 혼입 없음, 문서 top-k 변경 및 두 상품 비교 메타데이터 포함 확인.",
              "- 원본 ZIP 8개, 정규화 상품, 다운로드 PDF 25개의 해시 보존 확인.",
              f"- 순수 의미검색 예제: K200인덱스 상품군 top-5 포함 `{evidence['named_product_search']['target_family_in_top5']}`. 상품명 검색에 한계가 있어 공식명 일치를 우선하고 나머지를 의미검색으로 보충합니다.",
              "- 공식명 우선 검색 예제에서는 원본에 있는 K200인덱스 상품군 4개가 먼저 검색되고 남은 1개는 의미검색으로 보충되었습니다. 공백/NFC/대소문자만 정리하며 클래스 구두점은 유지합니다. 이름 일치 결과의 거리값은 null이며 의미검색 점수로 가장하지 않습니다.",
              "- 두 번째 실행은 동일한 ID 집합·건수 유지, 추가 임베딩/업서트/삭제 없음.", "",
              "## 저장 위치 및 재현", "",
              "- `storage/chroma/`: 영속 Chroma DB 및 index_manifest.json/build_status.json.",
              "- `storage/models/`: 공개 모델 가중치와 파일 해시·revision manifest.",
              "- `storage/embedding_cache.sqlite3`: 모델·내용 해시별 벡터 캐시.",
              "- `data/processed/document_pages.jsonl`, `document_chunks.jsonl`: 페이지와 상품별 청크 원문/출처.",
              "- `docs/evidence/vector_index_first_run.json`, `vector_index_verification.json`: 두 실행 및 실제 검색 증거.", "",
              "```powershell", ".\\.venv\\Scripts\\python.exe scripts/download_embedding_model.py",
              ".\\.venv\\Scripts\\python.exe scripts/build_indexes.py",
              ".\\.venv\\Scripts\\python.exe scripts/verify_vector_db.py", "```", "",
              "## 남은 범위", "",
              "현재 전체 상품 메타데이터는 검색할 수 있지만 PDF 근거가 연결된 상품은 23개뿐입니다. 나머지 상품의 문서 조건을 확인했다고 표시하면 안 됩니다.",
              "검색 유사도는 적격성 판단이 아닙니다. 구조화 제약 필터, LLM 검색어/문서 선별, LangGraph 답변 생성, 인용 UI는 이후 구현 대상입니다.",
              "이번 검색 검증은 상품별 격리·원문 페이지 연결·건수 검증입니다. 질문 관련성/정답 품질을 보증하지 않으며 표·유사한 공통 문구의 상위 검색이 관찰되어 검색 품질 평가와 개선이 필요합니다.",
              "문서의 외부 모델 전송/재배포 허용 범위는 미확인이며 이번 임베딩은 로컬에서만 수행했습니다.", "",
              "참조: [모델 카드](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2), [FastEmbed 모델 목록](https://qdrant.github.io/fastembed/examples/Supported_Models/), [Chroma 영속 클라이언트](https://docs.trychroma.com/reference/python/client).", ""]
    atomic_text(ROOT / "docs/vector_index.md", "\n".join(lines))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    verify()
