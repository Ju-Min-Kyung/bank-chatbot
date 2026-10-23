"""MMR trade-off, content deduplication, filters, and real Chroma integration."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from src.rag.mmr import select_indices, unit_vectors, validate_search
from src.ingestion.vector_store import persistent_client, managed_collection
from src.rag.retriever import VectorRetriever


class SelectionTests(unittest.TestCase):
    def test_diversity_penalty_changes_near_duplicate_selection(self):
        query = [1, 0, 0]
        vectors = [[0.95, 0.31, 0], [0.949, 0.315, 0], [0.85, -0.5268, 0], [-1, 0, 0]]
        self.assertEqual(select_indices(query, vectors, 2, mode="similarity"), [0, 1])
        self.assertEqual(select_indices(query, vectors, 2, mode="mmr", lambda_mult=0.5), [0, 2])
        self.assertEqual(select_indices(query, vectors, 4, mode="mmr", lambda_mult=1), select_indices(query, vectors, 4, mode="similarity"))
        self.assertEqual(select_indices(query, vectors, 2, mode="mmr", lambda_mult=0), [0, 3])

    def test_limits_empty_zero_norm_nan_and_bad_parameters(self):
        self.assertEqual(select_indices(None, [], 5), [])
        self.assertEqual(select_indices([1, 0], [[1, 0]], 5), [0])
        self.assertEqual(select_indices([1, 0], [[1, 0]], 0), [])
        for mode, k, fetch, weight in [("bad", 2, 30, .5), ("mmr", 5, 3, .5), ("mmr", 2, 30, float("nan")), ("mmr", 2, 30, -0.1)]:
            with self.assertRaises(ValueError):
                validate_search(mode, k, fetch, weight)
        for values in ([[0, 0]], [[float("nan"), 1]]):
            with self.assertRaises(ValueError):
                unit_vectors(values)


class FixedEmbedder:
    signature = "synthetic-mmr-test"
    dimension = 3
    config = SimpleNamespace(model_name="synthetic-only")

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [[1, 0, 0] for _ in texts]


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".test-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.client = persistent_client(self.path)
        self.addCleanup(self.client.close)
        self.embedder = FixedEmbedder()
        products = managed_collection(self.client, "financial_products", self.embedder)
        products.upsert(ids=["p1", "p2"], embeddings=[[1, 0, 0], [1, 0, 0]], documents=["first", "second"],
                        metadatas=[{"product_id": key, "source_type": "product_metadata"} for key in ("p1", "p2")])
        docs = managed_collection(self.client, "financial_documents", self.embedder)
        vectors = [[.95, .31, 0], [.949, .315, 0], [.85, -.5268, 0], [.95, .31, 0], [1, 0, 0]]
        texts = ["원금 손실 위험 설명", "원금 손실 위험 추가 설명", "환매 절차 및 수수료", "원금  손실 위험 설명", "다른 상품 원문"]
        keys = ["a", "b", "c", "duplicate", "foreign"]
        metadata = [{"product_id": "p2" if key == "foreign" else "p1", "document_id": "d2" if key == "foreign" else "d1",
                     "chunk_index": i, "page_number": i+1, "source_type": "official_pdf"} for i, key in enumerate(keys)]
        docs.upsert(ids=keys, embeddings=vectors, documents=texts, metadatas=metadata)
        (self.path / "build_status.json").write_text('{"status":"ready"}', encoding="utf-8")
        self.retriever = VectorRetriever(SimpleNamespace(chroma_dir=self.path), self.embedder)
        self.addCleanup(self.retriever.client.close)

    def test_same_pool_once_dedup_mmr_and_similarity_differ(self):
        report = self.retriever.compare_documents("위험과 환매", ["p1"], document_top_k=2, fetch_k=5)
        self.assertEqual(self.embedder.calls, 1)
        self.assertEqual(report["unique_candidates"], 3)
        self.assertEqual([r["record_id"] for r in report["results"]["similarity"]["rows"]], ["a", "b"])
        self.assertEqual([r["record_id"] for r in report["results"]["mmr"]["rows"]], ["a", "c"])
        self.assertLess(report["results"]["mmr"]["metrics"]["mean_pairwise_cosine"], report["results"]["similarity"]["metrics"]["mean_pairwise_cosine"])

    def test_metadata_preserved_and_filtered_sources_and_shortfall(self):
        evidence = self.retriever.retrieve("환매", ["p1"], 10, ["d1"], search_mode="mmr", fetch_k=30)
        self.assertEqual(evidence[0]["source_type"], "product_metadata")
        self.assertEqual(len(evidence), 4)
        self.assertTrue(all(row["product_id"] == "p1" for row in evidence))
        self.assertEqual([row["evidence_id"] for row in evidence], [1, 2, 3, 4])
        self.assertEqual(len(self.retriever.retrieve("환매", ["p1"], 2, ["missing"], search_mode="mmr")), 1)
        with self.assertRaises(ValueError):
            self.retriever.retrieve("환매", ["missing"], 2, search_mode="mmr")
        with self.assertRaises(ValueError):
            self.retriever.retrieve("환매", ["p1"], 10, search_mode="mmr", fetch_k=5)


if __name__ == "__main__":
    unittest.main()
