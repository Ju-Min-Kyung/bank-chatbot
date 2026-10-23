"""Synthetic indexing fixtures; real Chroma with explicitly fake test vectors."""
import importlib.util
import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from config import IndexConfig
from scripts.build_indexes import offline_network
from src.ingestion.embeddings import text_chunks
from src.ingestion.vector_store import index_records, managed_collection, persistent_client


class CharacterTokenizer:
    def num_special_tokens_to_add(self, pair):
        return 2

    def encode(self, text, add_special_tokens=False):
        return SimpleNamespace(ids=list(range(len(text))), offsets=[(i, i + 1) for i in range(len(text))])


class ChunkTests(unittest.TestCase):
    def test_token_boundaries_preserve_every_character_and_page_offsets(self):
        text = "가나다라마바사아자차카타파하 abcdefghijklmnopqrstuvwxyz"
        parts = list(text_chunks(text, CharacterTokenizer(), max_tokens=10, chunk_size=100, chunk_overlap=50))
        covered = set()
        for part in parts:
            self.assertLessEqual(part["token_count"], 10)
            self.assertEqual(text[part["char_start"]:part["char_end"]], part["text"])
            covered.update(range(part["char_start"], part["char_end"]))
        self.assertEqual(covered, set(range(len(text))))
        self.assertLess(len(parts), len(text) // 2)

    def test_character_limit_overlap_and_empty_pages(self):
        parts = list(text_chunks("abcdefghij", CharacterTokenizer(), max_tokens=128, chunk_size=5, chunk_overlap=3))
        self.assertEqual([(p["char_start"], p["char_end"]) for p in parts], [(0, 5), (4, 9), (8, 10)])
        self.assertEqual(list(text_chunks(" \n ", CharacterTokenizer())), [])
        with self.assertRaises(ValueError):
            list(text_chunks("x", CharacterTokenizer(), chunk_size=3, chunk_overlap=3))

    def test_python_network_denied_and_restored(self):
        import socket
        original = socket.getaddrinfo
        with offline_network():
            with self.assertRaises(RuntimeError):
                socket.getaddrinfo("example.invalid", 443)
        self.assertIs(socket.getaddrinfo, original)

    def test_storage_cannot_overlap_raw_processed_or_external(self):
        config = IndexConfig()
        for path in (config.products.parent, config.external_dir / "index", Path(__file__).resolve().parents[1] / "data/raw"):
            with self.assertRaises(ValueError):
                replace(config, chroma_dir=path).validate()


class FakeTestEmbedder:
    """Only tests use these non-semantic vectors. Production has no fake fallback."""
    def __init__(self):
        self.signature = "synthetic-test-only-v1"
        self.dimension = 3
        self.config = SimpleNamespace(model_name="synthetic-test-only", batch_size=2)
        self.calls = 0

    def embed(self, texts):
        self.calls += len(texts)
        return [[len(text) / math.sqrt(len(text)**2 + 2), 1 / math.sqrt(len(text)**2 + 2), 1 / math.sqrt(len(text)**2 + 2)] for text in texts]


@unittest.skipUnless(importlib.util.find_spec("chromadb"), "Install requirements-index.txt for Chroma tests")
class ChromaIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".test-", dir=Path(__file__).resolve().parent)
        self.path = Path(self.temp.name) / "chroma"
        self.client = persistent_client(self.path)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.client.close)
        self.embedder = FakeTestEmbedder()
        self.collection = managed_collection(self.client, "financial_products", self.embedder)

    def records(self):
        return [{"id": "p1", "text": "첫 번째 가상 상품", "metadata": {"product_id": "p1", "source_type": "product_metadata", "principal_protected": False}},
                {"id": "p2", "text": "두 번째 가상 상품", "metadata": {"product_id": "p2", "source_type": "product_metadata"}}]

    def test_two_runs_have_same_count_and_no_second_embedding(self):
        first = index_records(self.collection, self.records(), self.embedder, 2)
        second = index_records(self.collection, self.records(), self.embedder, 2)
        self.assertEqual((first["after"], second["after"], second["upserted"], second["unchanged"]), (2, 2, 0, 2))
        self.assertEqual(self.embedder.calls, 2)
        self.assertEqual(first["ids_sha256"], second["ids_sha256"])

    def test_source_change_updates_and_stale_rows_removed(self):
        rows = self.records()
        index_records(self.collection, rows, self.embedder, 2)
        rows[0]["text"] = "수정한 원문"
        result = index_records(self.collection, rows[:1], self.embedder, 2)
        self.assertEqual((result["after"], result["upserted"], result["stale_removed"]), (1, 1, 1))
        self.assertEqual(self.collection.get(ids=["p1"], include=["documents"])["documents"], ["수정한 원문"])

    def test_duplicate_ids_and_nested_metadata_rejected(self):
        rows = self.records()
        with self.assertRaises(ValueError):
            index_records(self.collection, [rows[0], rows[0]], self.embedder)
        rows[0]["metadata"]["nested"] = {"not": "scalar"}
        with self.assertRaises(ValueError):
            index_records(self.collection, rows, self.embedder)
        self.assertEqual(self.collection.count(), 0)

    def test_model_signature_mismatch_does_not_overwrite_collection(self):
        index_records(self.collection, self.records(), self.embedder)
        self.embedder.signature = "another-model"
        with self.assertRaises(ValueError):
            managed_collection(self.client, "financial_products", self.embedder)
        self.assertEqual(self.collection.count(), 2)

    def test_selected_metadata_always_present_and_documents_filtered(self):
        from src.rag.retriever import VectorRetriever
        index_records(self.collection, self.records(), self.embedder)
        docs = managed_collection(self.client, "financial_documents", self.embedder)
        records = [{"id": f"p{i}:doc:0", "text": f"가상 상품 {i} 문서", "metadata": {
            "product_id": f"p{i}", "document_id": f"doc{i}", "chunk_index": 0,
            "source_type": "official_pdf", "page_number": 1}} for i in (1, 2)]
        index_records(docs, records, self.embedder)
        (self.path / "build_status.json").write_text('{"status":"ready"}', encoding="utf-8")
        retriever = VectorRetriever(SimpleNamespace(chroma_dir=self.path), self.embedder)
        self.addCleanup(retriever.client.close)
        evidence = retriever.retrieve("환매 조건", ["p1"], document_top_k=1)
        self.assertEqual([e["source_type"] for e in evidence], ["product_metadata", "official_pdf"])
        self.assertTrue(all(e["product_id"] == "p1" for e in evidence))
        comparison = retriever.retrieve("비교", ["p2", "p1"], document_top_k=1)
        self.assertEqual([e["product_id"] for e in comparison[:2]], ["p2", "p1"])
        self.assertEqual(len(comparison), 3)
        self.assertEqual(len(retriever.retrieve("비교", ["p1"], document_top_k=0)), 1)
        with self.assertRaises(ValueError):
            retriever.retrieve("질문", ["missing"])

    def test_official_name_priority_preserves_class_and_semantic_fallback(self):
        from src.rag.retriever import VectorRetriever
        rows = self.records()
        rows[0]["metadata"]["product_name"] = "가상운용 인덱스펀드(A)"
        rows[1]["metadata"]["product_name"] = "가상운용 인덱스펀드(A-E)"
        index_records(self.collection, rows, self.embedder)
        managed_collection(self.client, "financial_documents", self.embedder)
        (self.path / "build_status.json").write_text('{"status":"ready"}', encoding="utf-8")
        retriever = VectorRetriever(SimpleNamespace(chroma_dir=self.path), self.embedder)
        self.addCleanup(retriever.client.close)
        calls = self.embedder.calls
        found = retriever.search_products("가상운용인덱스펀드(A-E)", top_k=1)
        self.assertEqual(found[0]["product_id"], "p2")
        self.assertEqual(found[0]["match_type"], "official_name")
        self.assertIsNone(found[0]["distance"])
        self.assertEqual(self.embedder.calls, calls)
        fallback = retriever.search_products("환매 비용이 낮은 상품", top_k=2)
        self.assertTrue(all(row["match_type"] == "semantic" for row in fallback))
        self.assertEqual(len(fallback), 2)
        eligible = retriever.search_products("가상운용 인덱스펀드", top_k=2, allowed_ids=["p2"])
        self.assertEqual([row["product_id"] for row in eligible], ["p2"])
        calls = self.embedder.calls
        self.assertEqual(retriever.search_products("조건", allowed_ids=[]), [])
        self.assertEqual(self.embedder.calls, calls)
        with self.assertRaises(ValueError):
            retriever.search_products(" ")


if __name__ == "__main__":
    unittest.main()
