"""Conservative securities normalization; never infer missing financial terms.

Pure functions accept already decoded records. Raw values are preserved; only
explicit, full-string patterns are parsed. Source labels are evaluation inputs,
not financial evidence, and their CoT/answer/demographics are not exported.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict
from datetime import date

NORMALIZATION_VERSION = "securities-v1"
PRODUCT_FIELDS = (
    "product_name", "product_type", "protected_type", "maturity_type", "income_rate",
    "risk_grade", "tax_type", "payment_type", "loss_rate", "liquidity_conditions", "investment_strategy",
)
SOURCE_ID_FIELDS = ("product_id", "product_code", "fund_code", "isin", "source_id")
RISK_LABELS = {1: "매우높은위험", 2: "높은위험", 3: "다소높은위험", 4: "보통위험", 5: "낮은위험", 6: "매우낮은위험"}


def digest(value) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def name_key(value: str) -> str:
    # Never remove punctuation, share class suffixes, or case distinctions.
    return " ".join(unicodedata.normalize("NFC", value).split())


def clean_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")

    def office_control(match):
        codepoint = int(match.group(1), 16)
        if codepoint in (10, 13):
            return "\n"
        return " " if codepoint < 32 else match.group(0)

    value = re.sub(r"_x([0-9a-fA-F]{4})_", office_control, value)
    value = "".join(" " if unicodedata.category(c) == "Co" or
                    (unicodedata.category(c) == "Cc" and c not in "\n\t") else c for c in value)
    return "\n".join(" ".join(line.split()) for line in value.splitlines() if line.strip())


def parsed(raw, value, status="parsed"):
    return {"raw_value": raw, "normalized_value": value, "parse_status": status}


def parse_risk(raw):
    match = re.fullmatch(r"([1-6])\s*등급\s*(?:\(([^()]*)\))?", clean_text(raw or ""))
    if not match:
        return parsed(raw, None, "unknown")
    grade = int(match[1])
    if match[2] and name_key(match[2]).replace(" ", "") != RISK_LABELS[grade]:
        return parsed(raw, None, "unknown")
    return parsed(raw, {"grade": grade, "label": match[2], "scale": "source_1_to_6"})


def parse_principal(raw):
    values = {"원금 비보장형": False, "원금비보장형": False, "원금 보장형": True, "원금보장형": True}
    text = clean_text(raw or "")
    return parsed(raw, values.get(text), "parsed" if text in values else "unknown")


def parse_income(raw):
    text = clean_text(raw or "")
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*%\s*\(\s*(?:기준\s*)?(\d+)\s*(개월|년)\s*\)", text)
    inception = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*%\s*\(\s*(?:최초\s*)?설정\s*(?:일\s*)?이후\s*\)", text)
    value = None
    if match and int(match[2]) > 0 and math.isfinite(float(match[1])):
        value = {"percent": float(match[1]), "measurement_period_months": int(match[2]) * (12 if match[3] == "년" else 1),
                 "period_kind": "fixed", "basis_date": None, "is_current_verified": False}
    elif inception and math.isfinite(float(inception[1])):
        value = {"percent": float(inception[1]), "measurement_period_months": None,
                 "period_kind": "since_inception", "basis_date": None, "is_current_verified": False}
    return parsed(raw, value, "parsed" if value is not None else "unknown")


def parse_maturity(raw):
    text = clean_text(raw or "")
    base = {"has_maturity": None, "duration_months": None, "duration_kind": None,
            "duration_anchor": None, "maturity_date": None}
    if text in {"만기 없음", "만기 있음"}:
        return parsed(raw, {**base, "has_maturity": text == "만기 있음"})
    date_match = re.fullmatch(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
    if date_match:
        try:
            maturity_date = date(*(int(p) for p in date_match.groups())).isoformat()
        except ValueError:
            return parsed(raw, None, "unknown")
        return parsed(raw, {**base, "has_maturity": True, "maturity_date": maturity_date})
    duration = re.fullmatch(r"(?:(최초설정일(?:로부터|부터))\s*)?(최대\s*)?(?:(\d+)\s*년\s*)?(?:(\d+)\s*개월\s*)?(이하|간)?", text)
    if duration and (duration[3] or duration[4]):
        months = int(duration[3] or 0) * 12 + int(duration[4] or 0)
        if months > 0:
            return parsed(raw, {**base, "has_maturity": True, "duration_months": months,
                                "duration_kind": "maximum" if duration[2] or duration[5] == "이하" else "exact",
                                "duration_anchor": "inception" if duration[1] else None})
    # Conditional redemption/maturity and malformed dates remain unparsed.
    return parsed(raw, None, "unknown")


def parse_liquidity(raw):
    text = clean_text(raw or "")
    match = re.fullmatch(r"(중도해지|중도환매|환매)\s*(가능|불가|불가능)", text)
    if not match:
        return parsed(raw, None, "unknown")
    field = {"중도해지": "early_termination_allowed", "중도환매": "early_redemption_allowed", "환매": "redemption_allowed"}[match[1]]
    return parsed(raw, {"early_termination_allowed": None, "early_redemption_allowed": None,
                        "redemption_allowed": None, field: match[2] == "가능",
                        "fees": None, "settlement_days": None, "detailed_conditions_verified": False})


def parse_loss(raw):
    match = re.fullmatch(r"(최대\s*)?(\d+(?:\.\d+)?)\s*%", clean_text(raw or ""))
    if not match or not 0 <= float(match[2]) <= 100:
        return parsed(raw, None, "unknown")
    return parsed(raw, {"reported_loss_percent": float(match[2]),
                        "maximum_loss_percent": float(match[2]) if match[1] else None})


def parse_tax(raw):
    text = clean_text(raw or "")
    if text not in {"유", "무"}:
        return parsed(raw, None, "unknown")
    return parsed(raw, {"benefit_indicated": text == "유", "eligibility_verified": False, "conditions": None})


def parse_payment(raw):
    text = clean_text(raw or "")
    values = {"추가납입": {"payment_mode": "additional_payment", "additional_payment_allowed": True},
              "일시납": {"payment_mode": "lump_sum", "additional_payment_allowed": None}}
    return parsed(raw, values.get(text), "parsed" if text in values else "unknown")


def normalize_product(record: dict, source: dict) -> dict:
    name = record.get("product_name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("product_name_missing_or_invalid")
    for field in (*PRODUCT_FIELDS, "company", *SOURCE_ID_FIELDS):
        value = record.get(field)
        if field in SOURCE_ID_FIELDS:
            if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int))):
                raise ValueError("source_id_invalid_type")
        elif value is not None and not isinstance(value, str):
            raise ValueError("product_field_invalid_type")
    # Do not let consumers/labels leak into product metadata even if misrouted.
    if {"cot_id", "question", "answer", "customer_id", "cot1", "cot2", "cot3"} & record.keys():
        raise ValueError("not_product_metadata")
    original_id = next(((key, record[key]) for key in SOURCE_ID_FIELDS
                        if record.get(key) is not None and str(record[key]).strip()), None)
    company = name_key(record["company"]) if record.get("company") else None
    identity = {"category": "securities", "company": company}
    if original_id:
        identity.update(source_id_field=original_id[0], source_id_value=original_id[1])
    else:
        identity["product_name"] = name_key(name)
    product_id = "sec_" + digest(identity)
    cleaned = {key: clean_text(value) if isinstance(value, str) else value for key, value in record.items()}
    normalized = {
        "product_risk_grade": parse_risk(record.get("risk_grade")),
        "principal_protected": parse_principal(record.get("protected_type")),
        "income_rate": parse_income(record.get("income_rate")),
        "maturity": parse_maturity(record.get("maturity_type")),
        "liquidity": parse_liquidity(record.get("liquidity_conditions")),
        "loss_rate": parse_loss(record.get("loss_rate")),
        "tax_benefit": parse_tax(record.get("tax_type")),
        "payment": parse_payment(record.get("payment_type")),
    }
    flags = [{"field": field, "code": "unparsed_value"} for field, value in normalized.items() if value["parse_status"] == "unknown"]
    flags.extend([{"field": "income_rate", "code": "basis_date_unverified"},
                  {"field": "liquidity_conditions", "code": "detailed_conditions_unverified"}])
    for field, value in record.items():
        if isinstance(value, str) and ("\ufffd" in value or re.search(r"_x[0-9a-fA-F]{4}_", cleaned[field])):
            flags.append({"field": field, "code": "damaged_or_unresolved_text"})
        if field in PRODUCT_FIELDS and isinstance(value, str) and not cleaned[field]:
            flags.append({"field": field, "code": "empty_after_cleaning"})
    loss = normalized["loss_rate"]["normalized_value"]
    if loss and loss["maximum_loss_percent"] is None:
        flags.append({"field": "loss_rate", "code": "maximum_loss_not_stated"})
    # Search text uses each parsed field's source wording, never a repaired number.
    structured = {"risk_grade": "product_risk_grade", "protected_type": "principal_protected",
                  "income_rate": "income_rate", "maturity_type": "maturity", "liquidity_conditions": "liquidity",
                  "loss_rate": "loss_rate", "tax_type": "tax_benefit", "payment_type": "payment"}
    text = []
    for field in PRODUCT_FIELDS:
        value = cleaned.get(field)
        if not value or (field in structured and normalized[structured[field]]["parse_status"] == "unknown"):
            value = "확인 필요(자료 누락 또는 형식 미확인)"
        text.append(f"{field}: {value}")
    text.append("income_rate_basis_date: 확인되지 않음; 현재 수익률로 사용 불가")
    return {
        "schema_version": NORMALIZATION_VERSION, "product_id": product_id, "category": "securities",
        "product_name": name_key(name), "company": company, "product_type": cleaned.get("product_type"),
        "id_method": "source_id" if original_id else "name_identity_hash",
        "source_type": "product_metadata", "source_file": source["source_file"],
        "source_record_hash": digest(record), "sources": [copy.deepcopy(source)],
        "attributes": copy.deepcopy(record), "cleaned_attributes": cleaned,
        "normalized_attributes": normalized, "search_text": "\n".join(text),
        "quality_flags": flags,
        "document_reference": {"status": "unlinked", "source_uri": None, "document_id": None},
    }


def deduplicate_products(products: list[dict]) -> tuple[list[dict], list[dict], int]:
    groups = defaultdict(list)
    for product in products:
        groups[product["product_id"]].append(product)
    accepted, rejected, duplicates = [], [], 0
    for product_id, variants in sorted(groups.items()):
        if len({p["source_record_hash"] for p in variants}) > 1:
            rejected.extend({"code": "product_identity_conflict", "product_id": product_id,
                             "source_record_hash": p["source_record_hash"], "sources": p["sources"]} for p in variants)
            continue
        product = copy.deepcopy(variants[0])
        product["sources"] = sorted((s for p in variants for s in p["sources"]),
                                    key=lambda s: (s["source_file"], s.get("member_index", -1), s["record_index"]))
        product["source_file"] = product["sources"][0]["source_file"]
        accepted.append(product)
        duplicates += len(variants) - 1
    return accepted, rejected, duplicates


class ProductLookup:
    def __init__(self, products):
        self.exact = defaultdict(set)
        self.normalized = defaultdict(set)
        for product in products:
            raw_name = product["attributes"]["product_name"]
            self.exact[raw_name].add(product["product_id"])
            self.normalized[name_key(raw_name)].add(product["product_id"])

    def link(self, name):
        exact = self.exact.get(name, set())
        candidates = exact or self.normalized.get(name_key(name), set())
        status = "unmatched" if not candidates else "ambiguous" if len(candidates) > 1 else "exact" if exact else "normalized"
        return {"requested_name": name, "status": status,
                "product_id": next(iter(candidates)) if len(candidates) == 1 else None,
                "candidate_product_ids": sorted(candidates)}


def normalize_label(record: dict, source: dict, split: str, lookup: ProductLookup) -> dict:
    from json import JSONDecodeError

    source_id = record.get("cot_id")
    valid_id = not isinstance(source_id, bool) and isinstance(source_id, (int, str)) and bool(str(source_id).strip())
    identity = {"category": "securities", "cot_id": source_id} if valid_id else {"source_record_hash": digest(record)}
    names = record.get("product_names")
    form, codes = "array", []
    if isinstance(names, str):
        form = "serialized_array"
        try:
            names = json.loads(names)
        except (JSONDecodeError, ValueError):
            names = None
    if not isinstance(names, list) or not names or not all(isinstance(n, str) and n.strip() for n in names):
        names = []
        codes.append("invalid_product_names")
    if not valid_id:
        codes.append("invalid_cot_id")
    question = record.get("question")
    if not isinstance(question, str) or not question.strip():
        question = None
        codes.append("invalid_question")
    if split not in {"training", "validation", "test"}:
        codes.append("unknown_split")
    links = [lookup.link(name) for name in names]
    codes.extend(sorted({f"{link['status']}_product_name" for link in links if link["status"] in {"unmatched", "ambiguous"}}))
    return {
        "label_id": "label_" + digest(identity), "category": "securities", "split": split,
        "source_cot_id": source_id if valid_id else None, "source_record_hash": digest(record), "sources": [copy.deepcopy(source)],
        "query_type": record.get("query_type") if isinstance(record.get("query_type"), str) else None,
        "question": question, "product_names_format": form,
        "product_links": links,
        "product_ids": sorted({link["product_id"] for link in links if link["product_id"]}),
        "evaluation_ready": not codes, "review_codes": codes,
        "usage": "evaluation_question_only_not_product_evidence",
    }


def finalize_labels(labels: list[dict]) -> tuple[list[dict], list[dict], int]:
    groups = defaultdict(list)
    question_splits = defaultdict(set)
    for label in labels:
        groups[label["label_id"]].append(label)
        if label["question"]:
            question_splits[name_key(label["question"])].add(label["split"])
    ready, review, duplicates = [], [], 0
    for _, variants in sorted(groups.items()):
        cross_split = len({v["split"] for v in variants}) > 1
        conflict = len({v["source_record_hash"] for v in variants}) > 1
        question_overlap = any(v["question"] and len(question_splits[name_key(v["question"])]) > 1 for v in variants)
        if cross_split or conflict or question_overlap:
            for label in variants:
                label = copy.deepcopy(label)
                label["evaluation_ready"] = False
                label["review_codes"].extend(code for code, applies in (
                    ("cross_split_label_id", cross_split), ("label_id_conflict", conflict),
                    ("cross_split_question", question_overlap)) if applies)
                review.append(label)
        else:
            label = copy.deepcopy(variants[0])
            label["sources"] = sorted((s for v in variants for s in v["sources"]),
                                      key=lambda s: (s["source_file"], s.get("member_index", -1), s["record_index"]))
            (ready if label["evaluation_ready"] else review).append(label)
            duplicates += len(variants) - 1
    return ready, review, duplicates
