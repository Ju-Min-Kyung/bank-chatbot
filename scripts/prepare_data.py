"""Prepare securities product metadata and isolated evaluation questions offline."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import uuid
import zipfile
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Reuse the exact reader/classifier used by the full audit, avoiding parser drift.
from scripts.audit_data import (  # noqa: E402
    audit_data, canonical_hash, classify, file_hash, parse_records, safe_files,
    sha256, table, validate_outputs,
)
from src.ingestion.normalize import (  # noqa: E402
    NORMALIZATION_VERSION, ProductLookup, deduplicate_products, finalize_labels,
    normalize_label, normalize_product,
)

DATA_OUTPUTS = (
    "products.jsonl", "evaluation/training.jsonl", "evaluation/validation.jsonl",
    "evaluation/test.jsonl", "review/labels.jsonl", "review/products.jsonl",
    "review/product_warnings.jsonl", "review/input_issues.jsonl",
)


def json_line(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def atomic_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def source_records(raw_dir: Path, inventory):
    """Re-read audited members by ZIP entry index, checking bytes against the audit."""
    with ExitStack() as stack:
        archives = {}
        for item in inventory:
            if item["status"] != "ok":
                continue
            path = raw_dir / item["path"]
            if "member" in item:
                if path not in archives:
                    archives[path] = stack.enter_context(zipfile.ZipFile(path))
                archive = archives[path]
                entry = archive.infolist()[item["member_index"]]
                if entry.filename != item["member"] or entry.file_size != item["bytes"]:
                    raise ValueError("input_changed_since_audit")
                with archive.open(entry) as stream:
                    data = stream.read(item["bytes"] + 1)
            else:
                with path.open("rb") as stream:
                    data = stream.read(item["bytes"] + 1)
            if sha256(data) != item["sha256"]:
                raise ValueError("input_changed_since_audit")
            records, encoding, layout = parse_records(data, item["effective_extension"])
            for index, record in enumerate(records):
                source = {"source_file": item["locator"], "record_index": index,
                          "encoding": encoding, "layout": layout}
                if "member_index" in item:
                    source["member_index"] = item["member_index"]
                yield record, source


def validate_targets(raw_dir, output_dir, report):
    validate_outputs(raw_dir, report, output_dir)
    raw = raw_dir.resolve()
    targets = [report, *(output_dir / name for name in (*DATA_OUTPUTS, "manifest.json"))]
    resolved = [target.resolve() for target in targets]
    if len(set(resolved)) != len(resolved):
        raise ValueError("output_paths_conflict")
    for target in resolved:
        if target == raw or raw in target.parents or target in raw.parents:
            raise ValueError("output_overlaps_raw_directory")
    files = list(safe_files(raw)) if raw.is_dir() else []
    for target in targets:
        if target.exists() and any(target.samefile(source) for source in files):
            raise ValueError("output_aliases_input_file")


def prepare_data(raw_dir: Path, output_dir: Path, report: Path):
    raw_dir, output_dir, report = raw_dir.resolve(), output_dir.resolve(), report.resolve()
    validate_targets(raw_dir, output_dir, report)
    # Fresh offline audit ensures new/changed inputs are inspected before conversion.
    audit, inventory = audit_data(raw_dir, raw_dir.parent / "external")
    if not audit["raw_integrity"]["before_after_equal"]:
        raise ValueError("input_changed_during_audit")
    products, labels, product_review = [], [], []
    skipped = Counter()
    input_issues = [{**error, "stage": "audit"} for error in audit["errors"]]
    for item in inventory:
        if item["status"] == "not_parsed":
            input_issues.append({"locator": item["locator"], "stage": "audit", "code": "unsupported_extension"})
    record_counts = Counter()
    # Labels are held in memory only until product linkage is available.
    for record, source in source_records(raw_dir, inventory):
        role, category, split = classify(record, source["source_file"])
        if category != "securities" or role not in {"products", "labels"}:
            skipped[f"{role}/{category}"] += 1
            if category == "unknown" or role == "unknown":
                input_issues.append({"source": source, "code": "unclassified_record", "stage": "classify"})
            continue
        record_counts[role] += 1
        if role == "products":
            try:
                products.append(normalize_product(record, source))
            except (ValueError, TypeError, OverflowError) as exc:
                # No exception message or full raw record in the report/log.
                product_review.append({"source": source, "code": "invalid_product_record", "error_type": type(exc).__name__})
        else:
            labels.append((record, source, split))
    products, conflicts, product_duplicates = deduplicate_products(products)
    product_review.extend(conflicts)
    if not products:
        raise ValueError("no_valid_securities_products")
    lookup = ProductLookup(products)
    normalized_labels = []
    for record, source, split in labels:
        try:
            normalized_labels.append(normalize_label(record, source, split, lookup))
        except (ValueError, TypeError, OverflowError) as exc:
            input_issues.append({"source": source, "code": "invalid_label_record", "error_type": type(exc).__name__, "stage": "normalize"})
    ready, label_review, label_duplicates = finalize_labels(normalized_labels)
    valid_ids = {product["product_id"] for product in products}
    if len(valid_ids) != len(products) or any(not p["search_text"].strip() for p in products):
        raise ValueError("product_validation_failed")
    for label in ready:
        if not label["product_ids"] or not set(label["product_ids"]) <= valid_ids:
            raise ValueError("dangling_evaluation_product_reference")
    for item in products:
        if any(key in item["attributes"] for key in ("question", "answer", "cot1", "cot2", "cot3", "customer_id")):
            raise ValueError("label_or_consumer_leak_into_products")
    raw_now = {p.relative_to(raw_dir).as_posix(): file_hash(p) for p in safe_files(raw_dir)}
    raw_before = {p["path"]: p["sha256"] for p in audit["physical_files"]}
    if raw_before != raw_now:
        raise ValueError("input_changed_during_preparation")
    validate_targets(raw_dir, output_dir, report)
    warning_rows = [{"product_id": p["product_id"], "source_file": p["source_file"], "quality_flags": p["quality_flags"]}
                    for p in products if p["quality_flags"]]
    rows = {"products.jsonl": products, "review/products.jsonl": product_review,
            "review/labels.jsonl": label_review, "review/product_warnings.jsonl": warning_rows,
            "review/input_issues.jsonl": input_issues}
    for split in ("training", "validation", "test"):
        rows[f"evaluation/{split}.jsonl"] = [label for label in ready if label["split"] == split]
    # Serialize before writing so a non-serializable record cannot publish a partial run.
    serialized = {name: "".join(json_line(row) for row in values) for name, values in rows.items()}
    outputs = {name: {"records": len(rows[name]), "bytes": len(text.encode("utf-8")),
                      "sha256": sha256(text.encode("utf-8"))} for name, text in sorted(serialized.items())}
    parse_stats = {}
    for field in products[0]["normalized_attributes"]:
        parse_stats[field] = dict(Counter(p["normalized_attributes"][field]["parse_status"] for p in products))
    link_counts = Counter(link["status"] for label in normalized_labels for link in label["product_links"])
    status = "partial" if input_issues or product_review else "ready_with_review" if label_review else "ready"
    manifest = {
        "schema_version": NORMALIZATION_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "securities_only", "status": status,
        "raw_dir": str(raw_dir), "raw_files": audit["physical_files"],
        "raw_integrity": {"sha256_unchanged": True, "files_checked": len(raw_now)},
        "input_records": dict(record_counts), "skipped_records": dict(skipped),
        "products": {"accepted": len(products), "duplicates_collapsed": product_duplicates,
                     "review_records": len(product_review), "one_search_text_per_product": True,
                     "meets_f2001_minimum_count": len(products) >= 100,
                     "changed_text_fields": dict(Counter(key for p in products for key, raw in p["attributes"].items()
                                                          if raw != p["cleaned_attributes"][key])),
                     "parse_stats": parse_stats,
                     "quality_flags": dict(Counter(f"{flag['field']}:{flag['code']}" for p in products for flag in p["quality_flags"]))},
        "labels": {"evaluation_ready": len(ready), "review_records": len(label_review),
                   "duplicates_collapsed": label_duplicates,
                   "ready_by_split": dict(Counter(label["split"] for label in ready)),
                   "review_codes": dict(Counter(code for label in label_review for code in label["review_codes"])),
                   "product_link_counts": dict(link_counts),
                   "profile_answer_cot_exported": False},
        "input_issue_count": len(input_issues), "outputs": outputs,
        "external_api_calls": 0, "embedding_performed": False,
        "limitations": ["No source financial document linkage", "Return basis dates unverified",
                        "No investor suitability mapping", "Early termination does not verify early redemption",
                        "Product identity is provisional when source ID/company are unavailable"],
    }
    report_text = preparation_report(manifest)
    for name, text in serialized.items():
        atomic_text(output_dir / name, text)
    atomic_text(report, report_text)
    # Manifest is the last publication step; consumers should verify its output hashes.
    atomic_text(output_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return manifest


def preparation_report(manifest):
    products, labels = manifest["products"], manifest["labels"]
    lines = ["# 증권 데이터 전처리 결과", "", f"생성 시각(UTC): {manifest['generated_at']}", "",
             "사용자 지시에 따라 보험은 제외합니다. API/임베딩/색인/외부 문서 수집은 수행하지 않았습니다.", "",
             f"- 상태: `{manifest['status']}` (검토 대상은 정상 평가 데이터와 분리)",
             f"- 원본 SHA-256 보존: `{manifest['raw_integrity']}`",
             f"- 입력 레코드: `{manifest['input_records']}`; 제외한 분야/역할: `{manifest['skipped_records']}`",
             f"- 정규화 상품: {products['accepted']:,}건; 중복 병합: {products['duplicates_collapsed']:,}건; 상품 검토: {products['review_records']:,}건",
             f"- 정상 평가용 라벨: {labels['evaluation_ready']:,}건; 분할: `{labels['ready_by_split']}`; 검토 라벨: {labels['review_records']:,}건",
             f"- 라벨 검토 코드: `{labels['review_codes']}`",
             f"- 상품명 연결(모든 파싱 가능 라벨 참조): `{labels['product_link_counts']}`",
             f"- 입력 읽기/분류 이슈: {manifest['input_issue_count']:,}건", "",
             "## 산출물", ""]
    lines += table(["data/processed/ 아래 기본 경로", "레코드", "bytes"],
                   [(name, item["records"], item["bytes"]) for name, item in manifest["outputs"].items()])
    lines += ["`manifest.json`에는 입력/출력 해시와 변환 버전이 있습니다. 재실행 시 전체를 다시 만들고 각 파일을 원자적으로 교체하며 manifest를 마지막에 씁니다.",
              "여러 파일 전체를 한 번에 교체하는 트랜잭션은 아니므로 후속 로더는 manifest의 출력 해시를 확인해야 합니다.", "",
              "## 원문과 정리본", "",
              "`attributes`는 원본 상품 객체를 유지하며 `cleaned_attributes`는 NFC/공백/Office 제어 escape/사설영역 글리프를 정리합니다.",
              "`normalized_attributes`는 raw_value, normalized_value, parse_status를 제공합니다. 미확인값은 null/unknown으로 유지합니다.",
              "대체문자(�)는 임의 복원하지 않습니다. 인식하지 못한 Office escape는 남기고 품질 표시합니다.",
              f"변경된 텍스트 필드 수: `{products['changed_text_fields']}`", "",
              "## 정형 파싱", ""]
    lines += table(["내부 필드", "파싱 상태/건수"], products["parse_stats"].items())
    lines += ["- 위험등급은 상품의 1~6등급 숫자/원문 설명만 파싱합니다. 투자자 성향으로 자동 매핑하지 않습니다.",
              "- 수익률은 %와 명시된 기간이 완전한 형식일 때만 파싱하며 기준일과 현재성은 미확인입니다. 오타/누락 %는 보정하지 않습니다.",
              "- `중도해지 가능`은 early_termination_allowed만 true로 변환합니다. 중도환매·환매일·비용은 미확인입니다.",
              "- 만기는 명시 날짜 또는 단순 기간만 파싱합니다. 날짜가 과거라고 현재 판매/만기 상태를 추정하지 않습니다.",
              "- 세제혜택 `유`는 원문 표기만 뜻하며 사용자 적용 자격이 확인되었다는 뜻이 아닙니다.",
              "- `최대`가 없는 손실률은 상한으로 사용하지 않습니다. 일시납을 추가납입 불가로 추정하지 않습니다.", "",
              "## 상품 ID·중복·라벨 격리", "",
              "상품 ID는 명시 원본 ID를 우선하고, 없으면 분야+회사(미제공 시 null)+NFC/공백 정리한 상품명 해시입니다. 클래스 괄호·구두점은 유지합니다.",
              "동일 ID/동일 콘텐츠만 중복 제거하고 출처를 합칩니다. 동일 ID에 다른 콘텐츠가 있으면 모두 상품 검토로 분리합니다.",
              "라벨은 질문과 상품 링크만 평가용으로 내보냅니다. 인구통계/answer/cot1~3은 제외하며 원문은 원본 경로로 추적합니다.",
              "불완전 product_names는 자동 복구하지 않습니다. 미연결/다중연결/분할 간 ID·질문 중복/ID 충돌이 있으면 라벨 전체를 정상 평가에서 제외합니다.",
              "파일 오류는 원본을 옮기지 않고 review/input_issues.jsonl에 기록합니다. partial 상태 산출물을 완전한 데이터로 사용하지 마세요.", "",
              "## 품질 표시", ""]
    lines += table(["필드:코드", "상품 수"], products["quality_flags"].items())
    lines += ["## 남은 작업", "",
              "공식 상품 설명서·약관과 상품 ID를 연결하고 이용 조건을 확인해야 합니다. 외부 문서 RAG와 Chroma 색인은 아직 수행하지 않았습니다.",
              "원금보장/환매 등 추천 조건 필터는 후속 단계에서 구조화 값과 미확인 상태를 함께 검증해야 합니다.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT / "data/raw")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data/processed")
    parser.add_argument("--report", type=Path, default=PROJECT_ROOT / "docs/data_preparation.md")
    args = parser.parse_args(argv)
    try:
        manifest = prepare_data(args.raw_dir, args.output_dir, args.report)
    except (OSError, ValueError, TypeError, csv.Error, zipfile.BadZipFile, RuntimeError, RecursionError) as exc:
        print(f"Preparation failed: {type(exc).__name__}; check input/output paths and audit report.", file=sys.stderr)
        return 2
    print(f"Securities products={manifest['products']['accepted']}; evaluation_labels={manifest['labels']['evaluation_ready']}; label_review={manifest['labels']['review_records']}; status={manifest['status']}; raw_unchanged=True")
    print(f"Report: {args.report}")
    return 1 if manifest["status"] == "partial" else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
