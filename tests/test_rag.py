"""Financial constraints, GMS protocol, candidate selection and graph safety."""
import io
import json
import unittest
import urllib.error
from dataclasses import replace
from types import SimpleNamespace

from config import ChatConfig
from src.domain.recommendations import comparison_table, eligible_products
from src.ingestion.normalize import normalize_product
from src.ingestion.query_extractor import extract_queries
from src.ingestion.document_selector import select_documents
from src.rag.citations import validated_answer
from src.rag.conditions import QueryConstraints, parse_constraints, check_product
from src.rag.gms import GMSClient, ModelError, SourcePermissionError
from src.rag.prompts import QUERY_SCHEMA
from src.ui.state import conversation


def product(**fields):
    record = dict(product_name="테스트 채권펀드(A)", product_type="채권형", protected_type="원금 비보장형",
                  risk_grade="5등급(낮은 위험)", maturity_type="만기 없음", income_rate="2.0% (1년)",
                  liquidity_conditions="중도해지 가능", tax_type="무", payment_type="추가납입", loss_rate="최대 100%", investment_strategy="채권 투자")
    record.update(fields)
    return normalize_product(record, {"source_file": "fixture.zip::product.json", "split": "training", "record_index": 0, "content_hash": "synthetic"})


class ConditionsTests(unittest.TestCase):
    def test_principal_never_recommends_unprotected(self):
        constraints, _ = parse_constraints("원금 손실은 피하고 싶어. 원금보장 필요해")
        self.assertTrue(constraints.principal_required)
        self.assertEqual(eligible_products([product()], constraints), [])

    def test_unknown_redemption_does_not_pass_termination(self):
        constraints, _ = parse_constraints("중도환매 가능한 상품")
        self.assertTrue(constraints.early_redemption_required)
        self.assertTrue(check_product(product(), constraints))
        self.assertFalse(check_product(product(liquidity_conditions="중도환매 가능"), constraints))

    def test_risk_type_maturity_and_unknown(self):
        constraints, _ = parse_constraints("고위험은 피하고 채권형 상품을 찾아줘")
        self.assertEqual(constraints.minimum_risk_grade, 4)
        self.assertFalse(check_product(product(), constraints))
        self.assertTrue(check_product(product(risk_grade="2등급(높은 위험)"), constraints))
        self.assertTrue(check_product(product(risk_grade="미확인"), constraints))
        constraints, _ = parse_constraints("1년 이내 만기 상품")
        self.assertTrue(check_product(product(), constraints))
        self.assertFalse(check_product(product(maturity_type="6개월"), constraints))

    def test_ambiguity_and_waived_condition(self):
        constraints, _ = parse_constraints("원금보장은 필요없고 채권형 말고 주식형")
        self.assertFalse(constraints.principal_required)
        self.assertTrue(constraints.ambiguous)
        self.assertTrue(check_product(product(), constraints))
        constraints, warnings = parse_constraints("안정적 성향이야")
        self.assertIsNone(constraints.minimum_risk_grade)
        self.assertTrue(warnings)

    def test_comparison_citations_and_conversation_isolation(self):
        p = product()
        rows, evidence = comparison_table([p])
        self.assertIn("[근거 1]", rows[0]["원금보장"])
        self.assertEqual(evidence[0]["product_id"], p["product_id"])
        store = {}
        conversation(store, ["p1"]).append({"role": "user", "content": "one"})
        self.assertEqual(conversation(store, ["p2"]), [])
        self.assertIs(conversation(store, ["p1", "p2"]), conversation(store, ["p2", "p1"]))


class Opener:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.requests = result, error, []

    def open(self, request, timeout):
        self.requests.append(request)
        if self.error:
            raise self.error
        return io.BytesIO(json.dumps(self.result).encode())


def response(value):
    return {"status": "completed", "output": [{"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps(value)}]}], "usage": {"total_tokens": 20}}


class GMSTests(unittest.TestCase):
    def test_request_protocol_and_no_secret_repr(self):
        opener = Opener(response({"queries": ["official name"]}))
        config = ChatConfig(api_key="secret-fixture", source_transmission_allowed=True)
        client = GMSClient(config, opener)
        result = client.structured("JSON", {"product": "synthetic"}, QUERY_SCHEMA, "queries")
        req = opener.requests[0]
        body = json.loads(req.data)
        self.assertTrue(req.full_url.endswith("/v1/responses"))
        self.assertEqual(body["model"], "gpt-6-astra")
        self.assertFalse(body["store"])
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertNotIn("secret-fixture", repr(config))
        self.assertEqual(result["queries"], ["official name"])

    def test_auth_and_transmission_gates_do_not_call_network(self):
        opener = Opener()
        for config, error in [(ChatConfig(), ModelError), (ChatConfig(api_key="secret"), SourcePermissionError)]:
            with self.assertRaises(error):
                GMSClient(config, opener).structured("JSON", {}, QUERY_SCHEMA, "queries")
        self.assertEqual(opener.requests, [])
        with self.assertRaises(ValueError):
            ChatConfig(base_url="https://example.invalid/v1").endpoint

    def test_errors_schema_refusal_and_incomplete_are_safe(self):
        config = ChatConfig(api_key="secret", source_transmission_allowed=True)
        values = [response({"queries": "invalid"}), {"status": "incomplete"},
                  {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]}]
        for value in values:
            with self.assertRaises(ModelError):
                GMSClient(config, Opener(value)).structured("JSON", {}, QUERY_SCHEMA, "queries")
        error = urllib.error.HTTPError(config.endpoint, 401, "secret", {}, io.BytesIO(b"secret"))
        with self.assertRaises(ModelError) as found:
            GMSClient(config, Opener(error=error)).structured("JSON", {}, QUERY_SCHEMA, "queries")
        self.assertNotIn("secret", str(found.exception))


class FakeClient:
    def __init__(self, selected=None):
        self.calls = []
        self.selected = selected

    def structured(self, instructions, payload, schema, name, **kwargs):
        self.calls.append((name, payload))
        if name == "search_queries":
            return {"queries": ["환매 설명서"]}
        if name == "document_selection":
            return {"selected_document_ids": self.selected or [], "reasons": []}
        evidence = payload["evidence"][0]
        return {"question_summary": "위험 조건 확인", "claims": [{"product_id": evidence["product_id"],
            "text": "원금 손실 가능성이 있습니다.", "supporting_quote": "원금 비보장형이며 원금 손실 가능성이 있습니다.",
            "citation_ids": [evidence["evidence_id"]]}], "unknowns": ["현재 수익률은 확인되지 않습니다."]}


class PromptTests(unittest.TestCase):
    def test_query_has_official_name_and_selector_cannot_invent(self):
        p = product()
        client = FakeClient()
        queries = extract_queries(client, p, "환매 방법")
        self.assertTrue(any(p["product_name"] in q for q in queries))
        candidates = [{"id": "d1", "verified_product_link": True}]
        result = select_documents(client, p, "환매", candidates)
        self.assertEqual(result["selected_document_ids"], ["d1"])
        with self.assertRaises(ModelError):
            select_documents(FakeClient(["invented"]), p, "환매", candidates)

    def test_citation_numbers_cross_product_and_quotes_fail_closed(self):
        source = {"evidence_id": 1, "product_id": "p1", "content": "원금 비보장형이며 원금 손실 가능성이 있습니다."}
        base = {"product_id": "p1", "text": "원금 손실 가능", "supporting_quote": source["content"], "citation_ids": [1]}
        result = {"question_summary": "확인", "claims": [base], "unknowns": []}
        valid = validated_answer(result, [source], {"p1": "테스트"})
        self.assertEqual(valid["cited_evidence_ids"], [1])
        for update in ({"citation_ids": [99]}, {"product_id": "p2"}, {"supporting_quote": "실제로 존재하지 않는 보장 문장입니다"}):
            broken = {**result, "claims": [{**base, **update}]}
            failed = validated_answer(broken, [source], {"p1": "테스트", "p2": "다른 상품"})
            self.assertEqual(failed["cited_evidence"], [])
            self.assertEqual(failed["rejected_claims"], 1)


class FakeRetriever:
    def __init__(self, p):
        self.p = p

    def search_products(self, question, top_k, allowed_ids):
        return [{"product_id": self.p["product_id"]}] if self.p["product_id"] in allowed_ids else []

    def retrieve(self, question, ids, document_top_k=5, document_ids=None, **search_options):
        self.last_search_options = search_options
        rows = [{"evidence_id": 1, "record_id": "metadata", "source_type": "product_metadata", "product_id": self.p["product_id"],
                 "product_name": self.p["product_name"], "content": "원금 비보장형이며 원금 손실 가능성이 있습니다."}]
        if document_top_k and document_ids:
            rows.append({"evidence_id": 2, "record_id": "doc", "source_type": "official_pdf", "product_id": self.p["product_id"],
                         "document_id": "d1", "chunk_index": 0, "content": "환매 비용은 별도 조건을 확인해야 합니다."})
        return rows


class GraphTests(unittest.TestCase):
    def setUp(self):
        from src.rag.graph import RAGService
        self.p, self.client = product(), FakeClient()
        catalog = SimpleNamespace(search=lambda pid, queries: [{"id": "d1", "verified_product_link": True}])
        self.service = RAGService([self.p], FakeRetriever(self.p), catalog, self.client)

    def test_two_nodes_real_langgraph_and_validated_answer(self):
        self.assertTrue({"retrieve", "generate"} <= set(self.service.graph.get_graph().nodes))
        result = self.service.ask("원금 손실 위험 알려줘", [self.p["product_id"]])
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["cited_evidence_ids"], [1])
        self.assertEqual([name for name, _ in self.client.calls], ["search_queries", "document_selection", "grounded_answer"])

    def test_hard_constraint_empty_and_sensitive_input_no_api(self):
        result = self.service.ask("원금보장 필수 상품 찾아줘")
        self.assertEqual(result["status"], "no_candidates")
        self.assertEqual(result["cited_evidence"], [])
        result = self.service.ask("연락처 010-1234-5678 상품 추천")
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(self.client.calls, [])

    def test_mmr_settings_reach_document_retrieval(self):
        self.service.search_mode = "mmr"
        self.service.fetch_k = 30
        self.service.lambda_mult = 0.4
        result = self.service.ask("위험 설명", [self.p["product_id"]])
        self.assertEqual(result["status"], "answered")
        self.assertEqual(self.service.retriever.last_search_options, {"search_mode": "mmr", "fetch_k": 30, "lambda_mult": 0.4})
        self.assertEqual(result["retrieval_audit"][0]["search"]["mode"], "mmr")

    def test_live_return_question_abstains_and_api_failure_distinct(self):
        result = self.service.ask("오늘 수익률은?", [self.p["product_id"]])
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertNotIn("grounded_answer", [name for name, _ in self.client.calls])
        def fail(*args, **kwargs):
            raise ModelError("GMS 인증에 실패했습니다.")
        self.client.structured = fail
        result = self.service.ask("위험 알려줘", [self.p["product_id"]])
        self.assertEqual(result["status"], "api_error")
        self.assertEqual(result["cited_evidence"], [])


if __name__ == "__main__":
    unittest.main()
