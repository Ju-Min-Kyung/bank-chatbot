"""Code-validated exploration and source-backed metadata comparison."""
from src.rag.conditions import check_product

COMPARISON_FIELDS = {"risk_grade": "위험등급", "protected_type": "원금보장", "maturity_type": "만기",
                     "income_rate": "과거 수익률(기준일 미확인)", "liquidity_conditions": "유동성(세부조건 미확인)",
                     "investment_strategy": "투자전략", "tax_type": "세제혜택 표시(적용요건 미확인)"}


def eligible_products(products, constraints):
    return [p for p in products if not check_product(p, constraints)]


def comparison_table(products):
    rows, evidence = [], []
    for i, product in enumerate(products, 1):
        item = {"상품명": product["product_name"]}
        for field, label in COMPARISON_FIELDS.items():
            value = product["cleaned_attributes"].get(field) or "확인 불가"
            item[label] = f"{value} [근거 {i}]"
        rows.append(item)
        evidence.append({"evidence_id": i, "product_id": product["product_id"], "title": product["product_name"] + " — 상품 메타데이터",
                         "source_type": "product_metadata", "source_file": product["source_file"], "content": product["search_text"]})
    return rows, evidence
