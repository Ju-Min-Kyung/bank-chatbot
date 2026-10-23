"""Financial parsing must retain uncertainty, provenance and evaluation isolation."""
import copy
import json
import unittest

from src.ingestion.normalize import (
    ProductLookup, clean_text, deduplicate_products, finalize_labels, normalize_label,
    normalize_product, parse_income, parse_liquidity, parse_loss, parse_maturity,
    parse_payment, parse_principal, parse_risk, parse_tax,
)


def source(name="TS_securities.zip!/sample.json", index=0):
    return {"source_file": name, "member_index": index, "record_index": 0,
            "encoding": "utf-8", "layout": "object"}


def product_record(name="검증상품(A-e)"):
    return {"product_name": name, "product_type": "주식형", "protected_type": "원금 비보장형",
            "risk_grade": "2등급(높은위험)", "income_rate": "-1.25% (기준 6개월)",
            "maturity_type": "만기 없음", "liquidity_conditions": "중도해지 가능",
            "tax_type": "유", "payment_type": "일시납", "loss_rate": "최대 100%",
            "investment_strategy": "  전략\uf09e\n예시_x000D_다음_x0002_항목  "}


def label_record(cot_id=1):
    return {"cot_id": cot_id, "category": "증권", "query_type": "상품 비교 추천형",
            "question": "검증상품을 비교해줘", "product_names": ["검증상품(A-e)"],
            "cot1": "HIDDEN_REASONING", "cot2": "HIDDEN_REASONING", "cot3": "HIDDEN_REASONING",
            "answer": "HIDDEN_ANSWER", "age": "PRIVATE_PROFILE", "gender": "PRIVATE_PROFILE"}


class NormalizeTests(unittest.TestCase):
    def test_raw_preserved_and_search_text_separated(self):
        record = product_record()
        original = copy.deepcopy(record)
        product = normalize_product(record, source())
        self.assertEqual(record, original)
        self.assertEqual(product["attributes"], original)
        self.assertEqual(product["normalized_attributes"]["income_rate"]["raw_value"], record["income_rate"])
        self.assertNotIn("\uf09e", product["search_text"])
        self.assertNotIn("_x000D_", product["search_text"])
        self.assertIn("investment_strategy:", product["search_text"])
        self.assertEqual(product["document_reference"]["status"], "unlinked")
        self.assertIsNone(product["company"])
        product["attributes"]["income_rate"] = "mutated output"
        self.assertEqual(record, original)

    def test_cleaning_does_not_invent_damaged_text(self):
        self.assertEqual(clean_text(" A\r\n\uf09e B_x000D_C_x0002_D\x01 "), "A\nB\nC D")
        self.assertEqual(clean_text("� _x0041_"), "� _x0041_")

    def test_ids_stable_across_paths_and_retain_share_classes(self):
        record = product_record()
        first = normalize_product(record, source())
        other_path = normalize_product(record, source("VS_securities.zip!/other.json"))
        self.assertEqual(first["product_id"], other_path["product_id"])
        self.assertNotEqual(first["product_id"], normalize_product(product_record("검증상품(C-e)"), source())["product_id"])
        self.assertNotEqual(first["product_id"], normalize_product({**record, "company": "회사"}, source())["product_id"])
        explicit = {**record, "fund_code": "stable-source-code"}
        self.assertEqual(normalize_product(explicit, source())["product_id"],
                         normalize_product({**explicit, "product_name": "변경명"}, source())["product_id"])
        self.assertEqual(normalize_product(explicit, source())["id_method"], "source_id")

    def test_conservative_principal_and_risk(self):
        self.assertIs(parse_principal("원금 비보장형")["normalized_value"], False)
        self.assertIs(parse_principal("원금보장형")["normalized_value"], True)
        for value in ("원금 부분 보장", "보장하지 않음", None):
            self.assertIsNone(parse_principal(value)["normalized_value"])
        self.assertEqual(parse_risk("2등급(높은 위험)")["normalized_value"]["grade"], 2)
        for value in ("7등급", "2등급(낮은위험)", "투자성향 안정형", None):
            self.assertEqual(parse_risk(value)["parse_status"], "unknown")

    def test_returns_need_explicit_percent_and_complete_period(self):
        normalized = parse_income("-1.25% (기준 6개월)")["normalized_value"]
        self.assertEqual(normalized["percent"], -1.25)
        self.assertEqual(normalized["measurement_period_months"], 6)
        self.assertIsNone(normalized["basis_date"])
        self.assertFalse(normalized["is_current_verified"])
        self.assertEqual(parse_income("2%(기준 1년)")["normalized_value"]["measurement_period_months"], 12)
        self.assertEqual(parse_income("1.2% (설정 이후)")["normalized_value"]["period_kind"], "since_inception")
        for value in ("임의해지", "19.06 (기준 6개월)", "6.43.% (기준 1년)", "--0.96% (기준 6개월)",
                      "7.2% (기준 1년 (기준 1년)", "누적수익률 7% (현재 해지)", "1%(기준 0년)",
                      "9" * 400 + "% (기준 1년)"):
            with self.subTest(value=value):
                self.assertEqual(parse_income(value)["parse_status"], "unknown")

    def test_termination_does_not_verify_redemption_or_cost(self):
        value = parse_liquidity("중도해지 가능")["normalized_value"]
        self.assertTrue(value["early_termination_allowed"])
        self.assertIsNone(value["early_redemption_allowed"])
        self.assertIsNone(value["redemption_allowed"])
        self.assertIsNone(value["fees"])
        self.assertFalse(value["detailed_conditions_verified"])
        self.assertIs(parse_liquidity("중도환매 불가")["normalized_value"]["early_redemption_allowed"], False)
        self.assertEqual(parse_liquidity("중도해지 가능하나 별도 조건")["parse_status"], "unknown")

    def test_maturity_absolute_dates_durations_and_conditions(self):
        self.assertIs(parse_maturity("만기 없음")["normalized_value"]["has_maturity"], False)
        value = parse_maturity("최초설정일로부터 최대 3년 1개월")["normalized_value"]
        self.assertEqual((value["duration_months"], value["duration_kind"], value["duration_anchor"]), (37, "maximum", "inception"))
        self.assertEqual(parse_maturity("2020년4월28일")["normalized_value"]["maturity_date"], "2020-04-28")
        for value in ("2025년2월30일", "신탁계약기간", "최초설정일부터 최대 3년 (목표 기준가격 도달 시 만기)"):
            self.assertEqual(parse_maturity(value)["parse_status"], "unknown")

    def test_tax_loss_and_lump_sum_do_not_infer_eligibility_or_bounds(self):
        self.assertFalse(parse_tax("유")["normalized_value"]["eligibility_verified"])
        self.assertTrue(parse_tax("유")["normalized_value"]["benefit_indicated"])
        self.assertEqual(parse_loss("최대 100%")["normalized_value"]["maximum_loss_percent"], 100)
        self.assertIsNone(parse_loss("0.15%")["normalized_value"]["maximum_loss_percent"])
        self.assertEqual(parse_loss("120%")["parse_status"], "unknown")
        self.assertIsNone(parse_payment("일시납")["normalized_value"]["additional_payment_allowed"])

    def test_malformed_fields_are_not_search_facts_and_labels_are_rejected(self):
        product = normalize_product({**product_record(), "income_rate": "임의해지"}, source())
        self.assertNotIn("임의해지", product["search_text"])
        self.assertEqual(product["attributes"]["income_rate"], "임의해지")
        for record in ({"product_name": ""}, {**product_record(), "risk_grade": 2},
                       {**product_record(), "cot1": "hidden"}, {**product_record(), "fund_code": True}):
            with self.assertRaises(ValueError):
                normalize_product(record, source())

    def test_exact_duplicate_merge_and_identity_conflict_are_different(self):
        record = product_record()
        a = normalize_product(record, source("TS.zip!/a.json"))
        b = normalize_product(dict(reversed(list(record.items()))), source("VS.zip!/a.json"))
        accepted, review, duplicates = deduplicate_products([b, a])
        self.assertEqual((len(accepted), len(review), duplicates), (1, 0, 1))
        self.assertEqual(len(accepted[0]["sources"]), 2)
        changed = normalize_product({**record, "risk_grade": "3등급(다소높은위험)"}, source("TS.zip!/b.json"))
        accepted, review, duplicates = deduplicate_products([a, changed])
        self.assertEqual((len(accepted), len(review), duplicates), (0, 2, 0))
        self.assertTrue(all(r["code"] == "product_identity_conflict" for r in review))


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.lookup = ProductLookup([normalize_product(product_record(), source())])

    def test_label_export_excludes_profile_answer_and_cot(self):
        label = normalize_label(label_record(), source(), "training", self.lookup)
        self.assertTrue(label["evaluation_ready"])
        self.assertEqual(len(label["product_ids"]), 1)
        serialized = json.dumps(label)
        for value in ("HIDDEN_REASONING", "HIDDEN_ANSWER", "PRIVATE_PROFILE"):
            self.assertNotIn(value, serialized)

    def test_incomplete_and_unlinked_labels_are_not_evaluation_ready(self):
        for names, code in (('["검증상품(A-e)"', "invalid_product_names"),
                            (["없는 상품"], "unmatched_product_name")):
            label = normalize_label({**label_record(), "product_names": names}, source(), "training", self.lookup)
            self.assertFalse(label["evaluation_ready"])
            self.assertIn(code, label["review_codes"])
        serialized = normalize_label({**label_record(), "product_names": '["검증상품(A-e)"]'}, source(), "training", self.lookup)
        self.assertTrue(serialized["evaluation_ready"])

    def test_name_linkage_does_not_merge_companies_or_classes(self):
        products = [normalize_product(product_record("상품  이름(A)"), source()),
                    normalize_product({**product_record("상품  이름(A)"), "company": "다른회사"}, source())]
        lookup = ProductLookup(products)
        self.assertEqual(lookup.link("상품 이름(A)")["status"], "ambiguous")
        self.assertIsNone(lookup.link("상품 이름(A)")["product_id"])
        self.assertEqual(lookup.link("상품 이름(C)")["status"], "unmatched")
        self.assertEqual(ProductLookup(products[:1]).link("상품 이름(A)")["status"], "normalized")

    def test_cross_split_id_or_question_overlap_is_excluded(self):
        for same_id in (True, False):
            a = normalize_label(label_record(1), source(), "training", self.lookup)
            b = normalize_label(label_record(1 if same_id else 2), source(), "validation", self.lookup)
            ready, review, _ = finalize_labels([a, b])
            self.assertFalse(ready)
            self.assertEqual(len(review), 2)
            self.assertTrue(all("cross_split_question" in r["review_codes"] for r in review))

    def test_duplicate_labels_merge_but_conflicting_id_is_quarantined(self):
        a = normalize_label(label_record(), source(), "training", self.lookup)
        b = normalize_label(label_record(), source("TL.zip!/another.json"), "training", self.lookup)
        ready, review, duplicates = finalize_labels([a, b])
        self.assertEqual((len(ready), len(review), duplicates), (1, 0, 1))
        self.assertEqual(len(ready[0]["sources"]), 2)
        changed = normalize_label({**label_record(), "question": "다른 질문"}, source(), "training", self.lookup)
        ready, review, _ = finalize_labels([a, changed])
        self.assertFalse(ready)
        self.assertTrue(all("label_id_conflict" in r["review_codes"] for r in review))

    def test_missing_question_id_or_split_is_reviewed(self):
        label = normalize_label({**label_record(), "cot_id": {"unexpected": "PRIVATE"}, "question": None},
                                source(), "unknown", self.lookup)
        self.assertFalse(label["evaluation_ready"])
        self.assertIsNone(label["source_cot_id"])
        self.assertEqual(set(label["review_codes"]), {"invalid_cot_id", "invalid_question", "unknown_split"})


if __name__ == "__main__":
    unittest.main()
