"""F2004: select only verified candidates and retain the product's own sources."""
from src.rag.gms import ModelError
from src.rag.prompts import SELECT_PROMPT, SELECT_SCHEMA


def select_documents(client, product, question, candidates):
    if not candidates:
        return {"selected_document_ids": [], "reasons": []}
    result = client.structured(SELECT_PROMPT, {"product_name": product["product_name"], "question": question,
                                "candidates": candidates}, SELECT_SCHEMA, "document_selection")
    valid = {c["id"] for c in candidates}
    if not set(result["selected_document_ids"]) <= valid or any(r["document_id"] not in valid for r in result["reasons"]):
        raise ModelError("문서 선별 결과에 후보 목록 밖의 ID가 있습니다.")
    required = [c["id"] for c in candidates if c["verified_product_link"]]
    result["selected_document_ids"] = list(dict.fromkeys(required + result["selected_document_ids"]))
    return result
