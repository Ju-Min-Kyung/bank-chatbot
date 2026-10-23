"""Read-only, offline AI Hub inventory and schema audit (Python 3.10+, stdlib).

ZIP members are read in memory, never extracted. Reports contain schema/statistics
and source locators, never consumer rows, questions, answers, or CoT text.
"""
from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import io
import itertools
import json
import re
import sys
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_SUFFIXES = {".json", ".jsonl", ".ndjson", ".csv"}
EXPECTED_SECURITIES = set("product_name product_type protected_type maturity_type income_rate risk_grade tax_type payment_type loss_rate liquidity_conditions pdf_filename investment_strategy".split())
ID_KEYS = {"id", "product_id", "product_code", "source_id", "fund_code", "isin"}
PLACEHOLDERS = {"n/a", "na", "null", "none", "미상", "정보없음", "정보 없음", "-", "해당없음", "해당 없음"}
DATE_PATTERN = re.compile(r"(?:19|20)\d{2}(?:[-./년]\s*\d{1,2}(?:[-./월]\s*\d{1,2})?)")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(record: dict) -> str:
    return sha256(json.dumps(record, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8"))


def normalized_name(value: str) -> str:
    # Conservative linkage candidate only: do not remove punctuation/share classes.
    return " ".join(unicodedata.normalize("NFC", value).split())


def logical_suffix(name: str) -> str:
    # AI Hub contains member names ending in '.json '. Keep the source name intact.
    return Path(name.rstrip()).suffix.lower()


def empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == [] or value == {}


def type_name(value) -> str:
    return {type(None): "null", bool: "boolean", int: "integer", float: "number",
            str: "string", list: "array", dict: "object"}[type(value)]


def decode_bytes(data: bytes) -> tuple[str, str]:
    # Strict decoding only. ASCII-only UTF-8 is compatible, not uniquely detectable.
    for bom, encoding in ((codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
                          (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"),
                          (codecs.BOM_UTF8, "utf-8-sig")):
        if data.startswith(bom):
            return data.decode(encoding), encoding
    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError("unsupported_encoding")


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("nonfinite_json_number")


def load_json(text):
    return json.loads(text, object_pairs_hook=strict_object, parse_constant=reject_constant)


def parse_records(data: bytes, suffix: str) -> tuple[list[dict], str, str]:
    text, encoding = decode_bytes(data)
    if suffix == ".csv":
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        headers = reader.fieldnames
        if not headers or len(set(headers)) != len(headers):
            raise ValueError("invalid_csv_header")
        records = list(reader)
        if any(None in row or any(v is None for v in row.values()) for row in records):
            raise ValueError("inconsistent_csv_columns")
        return records, encoding, "csv"
    if suffix in {".jsonl", ".ndjson"}:
        records = [load_json(line) for line in text.splitlines() if line.strip()]
        layout = "jsonl"
    else:
        try:
            root = load_json(text)
        except json.JSONDecodeError:
            # Also recognize JSONL shipped with a .json extension, strictly.
            lines = [line for line in text.splitlines() if line.strip()]
            if len(lines) < 2:
                raise
            records = [load_json(line) for line in lines]
            layout = "jsonl"
        else:
            if isinstance(root, dict):
                records, layout = [root], "object"
            elif isinstance(root, list):
                records, layout = root, "array"
            else:
                raise ValueError("unsupported_json_root")
    if not all(isinstance(record, dict) for record in records):
        raise ValueError("non_object_record")
    return records, encoding, layout


def classify(record: dict, locator: str) -> tuple[str, str, str]:
    lower = locator.lower().replace("\\", "/")
    if "cot_id" in record or {"question", "answer"} <= record.keys():
        role = "labels"
    elif "customer_id" in record:
        role = "consumers"
    elif "product_name" in record or "product_full_name" in record:
        role = "products"
    else:
        role = "unknown"
    category_value = str(record.get("category", "")).lower()
    if "insurance" in lower or "보험" in lower or "보험" in category_value or "insurance" in category_value or "mother_product_name" in record:
        category = "insurance"
    elif "securities" in lower or "증권" in lower or "증권" in category_value or "securities" in category_value or "investment_strategy" in record:
        category = "securities"
    else:
        category = "unknown"
    parts = re.split(r"[/!\\]", lower)
    if any(p in {"training", "train"} or p.startswith(("ts_", "tl_")) for p in parts):
        split = "training"
    elif any(p in {"validation", "valid", "val"} or p.startswith(("vs_", "vl_")) for p in parts):
        split = "validation"
    elif any(p == "test" or p.startswith(("es_", "el_")) for p in parts):
        split = "test"
    else:
        split = "unknown"
    return role, category, split


def walk_fields(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            path = prefix + "/" + key.replace("~", "~0").replace("/", "~1")
            yield path, child
            yield from walk_fields(child, path)
    elif isinstance(value, list):
        for child in value:
            path = prefix + "/*"
            yield path, child
            yield from walk_fields(child, path)


class Schema:
    def __init__(self):
        self.records = 0
        self.fields = defaultdict(lambda: {"records_present": 0, "occurrences": 0,
                                          "empty": 0, "placeholder": 0, "types": Counter()})

    def add(self, record):
        self.records += 1
        seen = set()
        for path, value in walk_fields(record):
            stats = self.fields[path]
            stats["occurrences"] += 1
            stats["types"][type_name(value)] += 1
            stats["empty"] += int(empty(value))
            stats["placeholder"] += int(isinstance(value, str) and value.strip().lower() in PLACEHOLDERS)
            if path not in seen:
                stats["records_present"] += 1
                seen.add(path)

    def report(self):
        return {"records": self.records, "fields": {
            path: {**stats, "types": dict(sorted(stats["types"].items())),
                   "missing_records": self.records - stats["records_present"],
                   "empty_rate_present": round(stats["empty"] / stats["occurrences"], 6)}
            for path, stats in sorted(self.fields.items())}}


def parse_product_names(value):
    if isinstance(value, str):
        try:
            value = load_json(value)
        except (ValueError, TypeError):
            return None
    if isinstance(value, list) and all(isinstance(name, str) and name.strip() for name in value):
        return value
    return None


class Audit:
    def __init__(self):
        self.schemas = defaultdict(Schema)
        self.hashes = defaultdict(Counter)
        self.split_hashes = defaultdict(lambda: defaultdict(set))
        self.identities = defaultdict(lambda: defaultdict(set))
        self.id_values = defaultdict(lambda: defaultdict(Counter))
        self.exact_names = defaultdict(set)
        self.normal_names = defaultdict(set)
        self.label_names = Counter()
        self.label_references = []
        self.label_name_types = Counter()
        self.label_name_formats = Counter()
        self.label_ids = defaultdict(set)
        self.query_types = Counter()
        self.product_types = Counter()
        self.artifacts = Counter()
        self.metadata_fields = defaultdict(Counter)
        self.pdf_refs = Counter()
        self.income = Counter()
        self.findings = []
        self.product_values = defaultdict(Counter)

    def add(self, record, locator):
        role, category, split = classify(record, locator)
        group = f"{role}/{category}"
        self.schemas[f"{group}/{split}"].add(record)
        digest = canonical_hash(record)
        self.hashes[group][digest] += 1
        self.split_hashes[group][split].add(digest)
        if role == "products":
            self.product_types[str(record.get("product_type", "<missing>"))] += 1
            for key in ("risk_grade", "protected_type", "maturity_type", "liquidity_conditions", "tax_type"):
                if key in record:
                    self.product_values[key][str(record[key])] += 1
            name = record.get("product_full_name") or record.get("product_name")
            if isinstance(name, str) and name.strip():
                key = (category, normalized_name(str(record.get("company") or "")),
                       normalized_name(str(record.get("mother_product_name") or "")), normalized_name(name))
                self.identities[group][key].add(digest)
            for name in (record.get("product_name"), record.get("product_full_name")):
                if isinstance(name, str) and name.strip():
                    self.exact_names[(category, name)].add(digest)
                    self.normal_names[(category, normalized_name(name))].add(digest)
            for key in ID_KEYS & record.keys():
                if not empty(record[key]):
                    self.id_values[group][key][canonical_hash({key: record[key]})] += 1
            for path, value in walk_fields(record):
                field = path.rsplit("/", 1)[-1].lower()
                if empty(value):
                    continue
                # Key presence is a candidate signal, not verification of provenance.
                for kind, pattern in (("url", r"(?:^|_)(?:url|uri|link)(?:$|_)"), ("page", r"(?:^|_)pages?(?:$|_)|페이지"),
                                      ("date", r"(?:^|_)date(?:$|_)|as_of|기준일|시점|작성일"),
                                      ("license", r"license|licence|copyright|이용조건|저작권"),
                                      ("document", r"pdf|document|source_file")):
                    if re.search(pattern, field):
                        self.metadata_fields[kind][path] += 1
                if isinstance(value, str):
                    signals = {
                        "replacement_character": "\ufffd" in value,
                        "private_use_character": any(unicodedata.category(c) == "Co" for c in value),
                        "office_escape": bool(re.search(r"_x[0-9a-fA-F]{4}_", value)),
                        "control_character": any(unicodedata.category(c) == "Cc" and c not in "\n\r\t" for c in value),
                        "edge_whitespace": value != value.strip(),
                        "embedded_http_url": bool(re.search(r"https?://", value)),
                    }
                    for signal, present in signals.items():
                        if present:
                            self.artifacts[f"{path}:{signal}"] += 1
                if field == "pdf_filename" and isinstance(value, str):
                    self.pdf_refs[Path(value.replace("\\", "/")).name] += 1
            if not empty(record.get("income_rate")):
                self.income["nonempty"] += 1
                if DATE_PATTERN.search(str(record["income_rate"])):
                    self.income["explicit_date_pattern"] += 1
        elif role == "labels":
            value = record.get("product_names")
            self.label_name_types[type_name(value)] += 1
            names = parse_product_names(value)
            form = "invalid" if names is None else ("serialized_array" if isinstance(value, str) else "array")
            self.label_name_formats[form] += 1
            if names is None:
                self.findings.append({"locator": locator, "field": "product_names", "code": "invalid_product_names",
                                      "value_type": type_name(value)})
            if names is not None:
                self.label_names.update((category, name) for name in names)
                self.label_references.extend((category, name, locator) for name in names)
            self.query_types[str(record.get("query_type", "<missing>"))] += 1
            if "cot_id" in record:
                self.label_ids[(category, canonical_hash({"cot_id": record["cot_id"]}))].add(split)

    def report(self):
        dedup = {}
        for group, counts in sorted(self.hashes.items()):
            identities = self.identities[group]
            dedup[group] = {
                "records": sum(counts.values()), "unique_content_hashes": len(counts),
                "duplicate_records": sum(counts.values()) - len(counts),
                "candidate_name_identities": len(identities),
                "candidate_name_conflicts": sum(len(v) > 1 for v in identities.values()),
                "id_fields": {key: {"nonempty": sum(values.values()), "unique": len(values)}
                              for key, values in sorted(self.id_values[group].items())},
                "split_unique_content": {key: len(value) for key, value in sorted(self.split_hashes[group].items())},
                "split_intersections": {
                    f"{left}:{right}": len(self.split_hashes[group][left] & self.split_hashes[group][right])
                    for left, right in itertools.combinations(sorted(self.split_hashes[group]), 2)},
            }
        links = Counter()
        for (category, name), count in self.label_names.items():
            exact = self.exact_names.get((category, name), set())
            normalized = self.normal_names.get((category, normalized_name(name)), set())
            status = "exact" if exact else "normalized_only" if normalized else "unmatched"
            links[status] += count
            if len(exact or normalized) > 1:
                links["ambiguous_content_matches"] += count
        linkage_findings = []
        for category, name, locator in self.label_references:
            matches = self.exact_names.get((category, name), set()) or self.normal_names.get((category, normalized_name(name)), set())
            if not matches or len(matches) > 1:
                linkage_findings.append({"locator": locator, "field": "product_names",
                                         "code": "unmatched_product_name" if not matches else "ambiguous_product_name",
                                         "name_sha256": sha256(name.encode("utf-8"))})
        return {
            "schemas": {key: schema.report() for key, schema in sorted(self.schemas.items())},
            "deduplication": dedup,
            "label_product_names": {"types": dict(self.label_name_types), "formats": dict(self.label_name_formats),
                                    "references": sum(self.label_names.values()), "unique_category_names": len(self.label_names),
                                    "linkage": dict(links)},
            "label_ids_shared_between_splits": sum(len(v) > 1 for v in self.label_ids.values()),
            "query_types": dict(sorted(self.query_types.items())),
            "product_types": dict(sorted(self.product_types.items())),
            "product_values": {k: dict(sorted(v.items())) for k, v in sorted(self.product_values.items())},
            "product_text_artifacts": dict(sorted(self.artifacts.items())),
            "product_metadata_field_signals": {k: dict(sorted(v.items())) for k, v in sorted(self.metadata_fields.items())},
            "income_rate": dict(self.income),
            "findings": self.findings + linkage_findings,
        }


def safe_files(root):
    """Do not follow directory symlinks/junctions or read symlinked files."""
    if not root.exists():
        return
    for path in sorted(root.iterdir()):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("symlink_or_junction_in_input")
        if path.is_dir():
            yield from safe_files(path)
        elif path.is_file():
            yield path


def audit_data(raw_dir: Path, external_dir: Path, max_member_bytes=64 * 1024 * 1024):
    raw_dir = raw_dir.resolve()
    if not raw_dir.is_dir():
        raise ValueError("raw_directory_missing")
    files = list(safe_files(raw_dir))
    if not files:
        raise ValueError("raw_directory_empty")
    before = {p.relative_to(raw_dir).as_posix(): file_hash(p) for p in files}
    audit = Audit()
    inventory, errors, physical, pdfs = [], [], [], []

    def inspect(data, item):
        name = item.get("member", item["path"])
        suffix = logical_suffix(name)
        item["effective_extension"] = suffix
        item["trailing_filename_whitespace"] = name != name.rstrip()
        item["sha256"] = sha256(data)
        if suffix == ".pdf":
            pdfs.append(item["locator"])
        if suffix in DATA_SUFFIXES:
            try:
                records, encoding, layout = parse_records(data, suffix)
                item.update(encoding=encoding, layout=layout, records=len(records), status="ok")
                groups = Counter("/".join(classify(r, item["locator"])) for r in records)
                item["groups"] = dict(groups)
                for record in records:
                    audit.add(record, item["locator"])
            except (ValueError, TypeError, csv.Error, RecursionError, OverflowError) as exc:
                item["status"] = "error"
                # Never copy exception messages: JSONDecodeError can contain source text.
                error = {"locator": item["locator"], "code": type(exc).__name__}
                if isinstance(exc, json.JSONDecodeError):
                    error.update(line=exc.lineno, column=exc.colno)
                errors.append(error)
        else:
            item["status"] = "not_parsed"
        inventory.append(item)

    for path in files:
        rel = path.relative_to(raw_dir).as_posix()
        physical.append({"path": rel, "bytes": path.stat().st_size, "sha256": before[rel]})
        if path.suffix.lower() != ".zip":
            if path.stat().st_size > max_member_bytes:
                errors.append({"locator": rel, "code": "file_size_limit"})
            else:
                inspect(path.read_bytes(), {"path": rel, "locator": rel, "bytes": path.stat().st_size})
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                for index, entry in enumerate(archive.infolist()):
                    if entry.is_dir():
                        continue
                    item = {"path": rel, "member": entry.filename, "member_index": index,
                            "locator": f"{rel}!{entry.filename}", "bytes": entry.file_size,
                            "compressed_bytes": entry.compress_size,
                            "zip_name_encoding": "utf-8" if entry.flag_bits & 0x800 else "cp437",
                            "absolute_member_name": entry.filename.startswith(("/", "\\")),
                            "parent_traversal_name": ".." in entry.filename.replace("\\", "/").split("/")}
                    try:
                        if entry.file_size > max_member_bytes:
                            raise ValueError("member_size_limit")
                        with archive.open(entry) as stream:
                            data = stream.read(max_member_bytes + 1)
                        if len(data) > max_member_bytes:
                            raise ValueError("member_size_limit")
                        inspect(data, item)
                    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
                        item["status"] = "error"
                        inventory.append(item)
                        errors.append({"locator": item["locator"], "code": type(exc).__name__})
        except (OSError, zipfile.BadZipFile, NotImplementedError) as exc:
            errors.append({"locator": rel, "code": type(exc).__name__})
    # External files: inventory PDF names only; no downloading, text extraction or indexing.
    if external_dir.exists():
        for path in safe_files(external_dir):
            if path.suffix.lower() == ".pdf":
                pdfs.append("external/" + path.relative_to(external_dir).as_posix())
    after_files = list(safe_files(raw_dir))
    after = {p.relative_to(raw_dir).as_posix(): file_hash(p) for p in after_files}
    unchanged = before == after
    if not unchanged:
        errors.append({"locator": "data/raw", "code": "input_changed_during_audit"})
    result = audit.report()
    pdf_basenames = Counter(Path(name.replace("\\", "/")).name for name in pdfs)
    result.update({
        "audit_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {"raw_dir": str(raw_dir), "external_dir": str(external_dir.resolve()),
                  "mode": "full_scan_no_extraction", "max_member_bytes": max_member_bytes},
        "physical_files": physical, "physical_bytes": sum(p["bytes"] for p in physical),
        "logical_files": len(inventory), "logical_bytes": sum(p["bytes"] for p in inventory),
        "encodings": dict(Counter(p["encoding"] for p in inventory if "encoding" in p)),
        "layouts": dict(Counter(p["layout"] for p in inventory if "layout" in p)),
        "extensions": dict(Counter(Path(p.get("member", p["path"])).suffix.lower() or "<none>" for p in inventory)),
        "effective_extensions": dict(Counter(p.get("effective_extension", "<unread>" ) for p in inventory)),
        "trailing_filename_whitespace": sum(p.get("trailing_filename_whitespace", False) for p in inventory),
        "raw_integrity": {"algorithm": "sha256", "before_after_equal": unchanged, "files_checked": len(before)},
        "pdf": {"files": pdfs, "reference_records": sum(audit.pdf_refs.values()),
                "unique_reference_names": len(audit.pdf_refs),
                "matched_reference_records": sum(count for name, count in audit.pdf_refs.items() if name in pdf_basenames),
                "ambiguous_reference_records": sum(count for name, count in audit.pdf_refs.items() if pdf_basenames[name] > 1)},
        "absolute_zip_member_names": sum(p.get("absolute_member_name", False) for p in inventory),
        "parent_traversal_zip_member_names": sum(p.get("parent_traversal_name", False) for p in inventory),
        "errors": errors,
    })
    return result, inventory


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def table(headers, rows):
    return ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |",
            *("| " + " | ".join(cell(v) for v in row) + " |" for row in rows), ""]


def markdown_report(result, inventory):
    lines = ["# AI Hub 금융데이터 감사 보고서", "", f"생성 시각(UTC): {result['generated_at']}", "",
             "전체 파일/레코드 실측 결과입니다. 원본 수정·ZIP 추출·전처리 산출·임베딩·외부 API 호출은 수행하지 않았습니다.",
             "상품정보와 라벨(추천 시나리오)은 별도 집계했으며, 질문·답변·CoT·소비자 원문은 출력하지 않습니다.", "",
             "## 범위와 무결성", "", f"- 원본 경로: `{result['scope']['raw_dir']}`",
             f"- 물리 파일 {len(result['physical_files']):,}개 / {result['physical_bytes']:,} bytes",
             f"- ZIP 멤버 포함 논리 파일 {result['logical_files']:,}개 / {result['logical_bytes']:,} bytes",
             f"- 원본 SHA-256 전후 일치: **{result['raw_integrity']['before_after_equal']}**",
             f"- 파싱/읽기 오류: {len(result['errors']):,}건 (오류 원본 이동 없이 manifest에 경로·오류종류만 기록)",
             f"- 인코딩(엄격 디코딩에 성공한 방식): `{json.dumps(result['encodings'], ensure_ascii=False)}`",
             f"- 구조: `{json.dumps(result['layouts'], ensure_ascii=False)}`",
             f"- 논리 확장자: `{json.dumps(result['extensions'], ensure_ascii=False)}`", "",
             f"- 파일명 뒤 공백: {result['trailing_filename_whitespace']:,}개; 판독용 확장자: `{result['effective_extensions']}`",
             "  확장자 인식 시에만 끝 공백을 제외하고 읽습니다. 원래 ZIP 멤버명·파일명은 변경하지 않습니다.", "",
             "UTF-8로 읽힌 ASCII-only 파일은 고유 인코딩을 확정하지 않습니다. 지원 범위는 JSON 객체/객체 배열/JSONL 및 쉼표 CSV입니다.",
             "중첩 래퍼 객체는 임의로 상품 목록으로 펼치지 않고 스키마에 기록합니다. unknown 분류는 수동 확인이 필요합니다.", "",
             "## 실제 파일 구성", ""]
    rows = []
    for physical in result["physical_files"]:
        members = [p for p in inventory if p["path"] == physical["path"]]
        rows.append((physical["path"], physical["bytes"], len(members), sum(p.get("records", 0) for p in members)))
    lines += table(["상대 경로", "압축/파일 bytes", "논리 파일", "레코드"], rows)
    lines += ["모든 멤버의 실제 경로·크기·해시·인코딩·형식은 `data/processed/audit/inventory.jsonl` 기본 출력에 기록됩니다.", "",
              "## 상품 수·중복·분할", "",
              "콘텐츠 중복은 키 순서를 정렬한 전체 JSON 레코드 SHA-256으로 측정합니다. 문자열/필드값을 바꾸지 않습니다.",
              "상품 이름 식별 후보는 category + company + mother_product_name + (product_full_name 또는 product_name)에 NFC/공백 정리만 적용합니다.",
              "이름 후보는 확정된 상품 ID가 아니며, 같은 이름의 서로 다른 콘텐츠를 자동 병합하지 않습니다. 콘텐츠 수 또한 법적 상품 수를 확정하지 않습니다.", ""]
    lines += table(["데이터/분야", "레코드", "고유 콘텐츠", "중복 레코드", "이름 식별 후보", "다중 콘텐츠 이름 후보"],
                   [(g, d["records"], d["unique_content_hashes"], d["duplicate_records"], d["candidate_name_identities"], d["candidate_name_conflicts"])
                    for g, d in result["deduplication"].items()])
    for group, stats in result["deduplication"].items():
        lines += [f"- `{group}` 분할별 고유 콘텐츠: `{stats['split_unique_content']}`; 분할 간 교집합: `{stats['split_intersections']}`",
                  f"  비어 있지 않은 원본 ID 후보: `{stats['id_fields']}`"]
    lines += ["", "한 분할만 있으면 분할 간 중복을 검증할 수 없습니다. 제공되지 않은 분할/보험/소비자 데이터의 규모는 추정하지 않습니다.", "",
              "## 스키마와 결측", "",
              "경로는 JSON Pointer 형식이며 `/*`는 배열 원소를 나타냅니다. 누락은 전체 레코드 대비 부재 건수입니다.",
              "빈값은 null/공백 문자열/빈 배열/빈 객체입니다. 빈값률은 해당 경로의 관측값 수 대비 비율이며, 배열 원소는 여러 번 관측될 수 있습니다.",
              "`-`, `해당없음`, `N/A` 등의 표기는 별도 placeholder로 집계하며 자동 결측 변환하지 않습니다. `무`와 0/False는 빈값이 아닙니다.", ""]
    for group, schema in result["schemas"].items():
        lines += [f"### {group} ({schema['records']:,}건)", ""]
        lines += table(["필드/중첩 경로", "관측 타입(횟수)", "누락 레코드", "빈값 관측", "빈값률", "placeholder"],
                       [(path, json.dumps(s["types"], ensure_ascii=False), s["missing_records"], s["empty"], f"{s['empty_rate_present']:.2%}", s["placeholder"])
                        for path, s in schema["fields"].items()])
        if group.startswith("products/securities/"):
            observed = {path[1:] for path in schema["fields"] if path.count("/") == 1}
            lines += [f"설명서 증권 필드 중 미관측: `{', '.join(sorted(EXPECTED_SECURITIES - observed)) or '없음'}`", ""]
    names = result["label_product_names"]
    lines += ["## 라벨 스키마 및 상품명 연결", "",
              f"- `product_names` 실제 타입: `{names['types']}`; 파싱 형태: `{names['formats']}`",
              f"- 총 상품명 참조 {names['references']:,}개 / 분야+원문 이름 {names['unique_category_names']:,}개",
              f"- 연결 집계(참조 횟수 기준): `{names['linkage']}`",
              f"- 분할 간 공통 cot_id(분야별): {result['label_ids_shared_between_splits']:,}개", "",
              "exact는 원문 이름 일치, normalized_only는 NFC/공백 정리 후에만 일치입니다. ambiguous_content_matches는 여러 콘텐츠에 연결되는 참조로, 다른 상태와 중복 집계됩니다.",
              "unmatched는 현재 확보한 상품 묶음에서만 미일치한다는 뜻입니다. 잘못된 추천으로 단정하지 않습니다.",
              "문자열 배열은 JSON으로만 해석하며 eval을 사용하지 않습니다. CoT/answer는 상품 사실 근거 또는 RAG 2 문서로 사용하지 않습니다.", ""]
    lines += table(["질문 유형", "레코드"], result["query_types"].items())
    lines += table(["상품 유형(원문)", "레코드"], result["product_types"].items())
    lines += ["### 상품 정형조건 분포(원문)", ""]
    for key, values in result["product_values"].items():
        lines += table([key, "레코드"], values.items())
    lines += ["## 원문 문서·현재성·이용 조건", "",
              f"- 확인한 PDF: {len(result['pdf']['files']):,}개 (`raw` 논리 파일 + `external`의 일반 PDF 파일명 조사)",
              f"- pdf_filename 참조: {result['pdf']['reference_records']:,}건 / 파일명 일치 {result['pdf']['matched_reference_records']:,}건 / 중의적 일치 {result['pdf']['ambiguous_reference_records']:,}건",
              f"- 상품의 출처/페이지/시점/라이선스 키 신호(비어 있지 않은 관측): `{result['product_metadata_field_signals']}`",
              f"- income_rate: `{result['income_rate']}` (날짜 정규식은 탐지 신호이며 유효 기준일 검증이 아님)", "",
              "6개월/1년 같은 측정 기간은 기준일이 아닙니다. 문서 안의 연도·전략 설명은 수익률 기준일로 해석하지 않습니다.",
              "파일명 매칭은 원문 내용·상품 정합성·페이지 검증을 대체하지 않습니다. URL의 접근성/공식성, 이용약관, 외부 API 전송/재배포 허용은 이 오프라인 감사에서 검증하지 않았습니다.",
              "PDF/출처 키가 없으면 상품 JSON만으로 원본 문서 연결을 확정할 수 없으며 F2004~F2005를 충족했다고 표시할 수 없습니다.", "",
              "## 텍스트 정리 필요 신호", ""]
    lines += table(["필드:신호", "관측값 수"], result["product_text_artifacts"].items())
    lines += ["전략의 사설영역 글리프·Office escape·줄바꿈/공백은 후속 전처리에서 원문(raw_value)을 보존한 채 별도 정리본을 만들어야 합니다.",
              f"ZIP 절대경로형 멤버명 {result['absolute_zip_member_names']:,}개, `..` 경로 {result['parent_traversal_zip_member_names']:,}개를 관측했습니다. 이 스크립트는 압축을 해제하지 않습니다.", "",
              "## 다음 전처리 설계", "",
              "1. 상품과 라벨 파이프라인을 분리하고 보험/소비자/미제공 분할의 확보 범위를 먼저 확인합니다.",
              "2. 원본 ID 후보가 없으면 회사/주계약/클래스를 보존한 안정적 ID를 설계합니다. 이름 충돌·콘텐츠 차이는 검토 대상으로 유지합니다.",
              "3. 원문 값과 정리/숫자 파싱 값을 분리합니다. 위험등급·원금보장·기간·환매 조건은 미확인값을 충족으로 간주하지 않습니다.",
              "4. 라벨의 product_names 형식을 검증하고 미연결/다중연결을 검토합니다. 추천 시나리오는 평가 질문 용도로 분리합니다.",
              "5. 공식 원문 문서와 이용 조건·기준일을 확보한 후 문서 연결과 임베딩 단계로 진행합니다.", ""]
    if result["findings"]:
        lines += ["## 필드 검증 이슈 (자동 복구하지 않음)", ""]
        lines += table(["경로", "필드", "코드"], [(e["locator"], e["field"], e["code"]) for e in result["findings"]])
    if result["errors"]:
        lines += ["## 오류 (원본 미이동)", ""]
        lines += table(["경로", "오류 종류"], [(e["locator"], e["code"]) for e in result["errors"]])
    return "\n".join(lines)


def validate_outputs(raw_dir: Path, report: Path, output_dir: Path):
    raw = raw_dir.resolve()
    report, output_dir = report.resolve(), output_dir.resolve()
    for target in (report, output_dir, (output_dir / "summary.json").resolve(), (output_dir / "inventory.jsonl").resolve()):
        if target == raw or raw in target.parents or target in raw.parents:
            raise ValueError("output_overlaps_raw_directory")
    if report == output_dir or report in output_dir.parents or report in (output_dir / "summary.json", output_dir / "inventory.jsonl"):
        raise ValueError("output_paths_conflict")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT / "data/raw")
    parser.add_argument("--external-dir", type=Path, default=PROJECT_ROOT / "data/external")
    parser.add_argument("--report", type=Path, default=PROJECT_ROOT / "docs/data_audit.md")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data/processed/audit")
    args = parser.parse_args(argv)
    try:
        validate_outputs(args.raw_dir, args.report, args.output_dir)
        result, inventory = audit_data(args.raw_dir, args.external_dir)
        # Build report before creating any output, and refuse to overwrite input aliases.
        content = markdown_report(result, inventory)
        input_paths = list(safe_files(args.raw_dir.resolve()))
        for target in (args.report, args.output_dir / "summary.json", args.output_dir / "inventory.jsonl"):
            if target.exists() and any(target.samefile(p) for p in input_paths):
                raise ValueError("output_aliases_input_file")
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        args.report.write_text(content, encoding="utf-8")
        (args.output_dir / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with (args.output_dir / "inventory.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for item in inventory:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    except (OSError, ValueError, RecursionError) as exc:
        print(f"Audit failed: {type(exc).__name__} ({str(exc) if isinstance(exc, ValueError) and str(exc).isascii() else 'check input/output paths'})", file=sys.stderr)
        return 2
    print(f"Audited {len(result['physical_files'])} physical files, {result['logical_files']} logical files; errors={len(result['errors'])}; findings={len(result['findings'])}; raw_unchanged={result['raw_integrity']['before_after_equal']}")
    print(f"Report: {args.report}")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
