"""Fail closed on invalid source IDs, product mixing, and fabricated quotations."""
import re

from src.domain.safety import NO_EVIDENCE, NOTICE


def normalize_quote(text):
    return re.sub(r"\s+", "", text)


def plain_markdown(text):
    return re.sub(r"([\\`*_{}\[\]()<>#|!])", r"\\\1", text)


def validated_answer(result, evidence, product_names):
    by_id = {row["evidence_id"]: row for row in evidence}
    claims, used, rejected = [], set(), 0
    for claim in result["claims"]:
        ids = list(dict.fromkeys(claim["citation_ids"]))
        quote = normalize_quote(claim["supporting_quote"])
        if (not ids or any(type(key) is not int or key not in by_id for key in ids) or
            claim["product_id"] not in product_names or
            any(by_id[key]["product_id"] != claim["product_id"] for key in ids) or
            not 8 <= len(quote) <= 300 or
            not any(quote in normalize_quote(by_id[key]["content"]) for key in ids)):
            rejected += 1
            continue
        # Never accept citations or links embedded inside model prose.
        if re.search(r"\[근거\s*\d+\]|https?://", claim["text"]):
            rejected += 1
            continue
        claims.append(claim)
        used.update(ids)
    lines = ["질문 요약: " + plain_markdown(result["question_summary"][:500]), ""]
    for claim in claims:
        label = plain_markdown(product_names[claim["product_id"]])
        citation = " ".join(f"[근거 {key}]" for key in dict.fromkeys(claim["citation_ids"]))
        lines.append(f"- **{label}**: {plain_markdown(claim['text'])} {citation}")
    if not claims:
        lines.append(NO_EVIDENCE)
    unknowns = list(dict.fromkeys(result["unknowns"]))
    if rejected:
        unknowns.append("출처 번호·상품 연결 또는 원문 구절을 확인할 수 없는 문장을 제외했습니다.")
    if unknowns:
        lines += ["", "확인 필요:"] + ["- " + plain_markdown(text) for text in unknowns]
    lines += ["", NOTICE]
    return {"answer": "\n".join(lines), "claims": claims,
            "cited_evidence": [row for row in evidence if row["evidence_id"] in used],
            "cited_evidence_ids": sorted(used), "rejected_claims": rejected}
