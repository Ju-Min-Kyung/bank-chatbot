"""Streamlit rendering and local interactions; no GMS request in UI tests."""
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest


class AppTests(unittest.TestCase):
    def test_selection_comparison_and_zero_principal_candidates(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=20)
        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "증권상품 근거 탐색")
        options = app.multiselect[0].options
        self.assertTrue(options)
        # AppTest select uses raw widget options (product IDs), not display names.
        from config import load_index_config
        from src.ingestion.product_index import load_product_records
        config = load_index_config()
        catalog, _ = load_product_records(config)
        import json
        with config.document_links.open(encoding="utf-8") as source:
            linked = {json.loads(line)["product_id"] for line in source}
        selected = [p["product_id"] for p in catalog if p["product_id"] in linked][:2]
        app.multiselect[0].set_value(selected).run(timeout=20)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe), 1)
        app.checkbox(key="principal_required").check().run(timeout=20)
        app.button(key="browse").click().run(timeout=20)
        self.assertFalse(app.exception)
        self.assertTrue(any("조건을 충족한 상품을 찾지 못했습니다" in item.value for item in app.warning))

    def test_missing_key_warning_does_not_expose_key(self):
        from unittest.mock import patch
        from config import ChatConfig
        with patch("config.load_chat_config", return_value=ChatConfig()):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=20)
        self.assertFalse(app.exception)
        self.assertTrue(any("GMS 키가 없습니다" in item.value for item in app.warning))

    def test_local_mmr_comparison_screen_without_gms_calls(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=20)
        app.selectbox(key="retrieval_target").set_value("sec_53cdb52b41d92fdd3f592dc70c6e216b7a9bd61f458bd99c5d170b144cddaf7d").run(timeout=20)
        app.button(key="compare_retrieval").click().run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe), 2)
        self.assertEqual(app.session_state.runtime.client.calls, 0)
        report = app.session_state.retrieval_comparison["report"]
        self.assertTrue(report["same_candidate_pool"])
        self.assertEqual(report["fetch_k"], 30)
        self.assertEqual(report["k"], 10)
        self.assertLess(report["results"]["mmr"]["metrics"]["mean_pairwise_cosine"], report["results"]["similarity"]["metrics"]["mean_pairwise_cosine"])
        app.session_state.runtime.close()


if __name__ == "__main__":
    unittest.main()
