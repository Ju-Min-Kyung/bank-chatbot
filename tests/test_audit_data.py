"""Synthetic fixtures only: offline audit correctness and raw-data protection."""
import codecs
import contextlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.audit_data import (
    Schema, audit_data, canonical_hash, classify, main,
    markdown_report, parse_product_names, parse_records, validate_outputs,
)


class ParsingTests(unittest.TestCase):
    def test_object_array_jsonl_and_nested_schema(self):
        cases = [(b'{"x": 1}', ".json", "object", 1),
                 (b'[{"x": 1}, {"x": 2}]', ".json", "array", 2),
                 (b'{"x": 1}\n{"x": 2}\n', ".jsonl", "jsonl", 2),
                 (b'{"x": 1}\n{"x": 2}\n', ".json", "jsonl", 2)]
        for data, suffix, layout, count in cases:
            with self.subTest(layout=layout, suffix=suffix):
                records, encoding, actual = parse_records(data, suffix)
                self.assertEqual((encoding, actual, len(records)), ("utf-8", layout, count))
        schema = Schema()
        schema.add({"wrapper": {"items": [{"x": ""}, {"x": 0}]} , "flag": False})
        schema.add({"wrapper": None})
        fields = schema.report()["fields"]
        self.assertEqual(fields["/wrapper/items/*/x"]["empty_rate_present"], 0.5)
        self.assertEqual(fields["/wrapper/items/*/x"]["missing_records"], 1)
        self.assertEqual(fields["/flag"]["empty"], 0)

    def test_strict_encodings_and_csv(self):
        text = json.dumps({"product_name": "검증상품"}, ensure_ascii=False)
        for encoding in ("utf-8-sig", "utf-16", "utf-32", "cp949"):
            with self.subTest(encoding=encoding):
                records, detected, _ = parse_records(text.encode(encoding), ".json")
                self.assertEqual(records[0]["product_name"], "검증상품")
                self.assertEqual(detected, encoding)
        records, _, layout = parse_records(codecs.BOM_UTF8 + b'name,note\r\nx,"line1\nline2"\r\n', ".csv")
        self.assertEqual((layout, records[0]["note"]), ("csv", "line1\nline2"))

    def test_rejects_invalid_json_and_csv(self):
        for data, suffix in [(b'{"x":1,"x":2}', ".json"), (b'{"x":NaN}', ".json"),
                             (b'[1,2]', ".json"), (b'"root"', ".json"),
                             (b'{broken}', ".json"), (b'a,a\n1,2', ".csv"),
                             (b'a,b\n1,2,3', ".csv"), (b'a,b\n1', ".csv")]:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    parse_records(data, suffix)

    def test_product_names_are_strict_and_never_eval(self):
        self.assertEqual(parse_product_names(["상품"]), ["상품"])
        self.assertEqual(parse_product_names('["상품"]'), ["상품"])
        for value in ('["상품"', "['상품']", '[1]', '__import__("os")', None, [""]):
            with self.subTest(value=value):
                self.assertIsNone(parse_product_names(value))

    def test_content_hash_ignores_key_order_but_preserves_values(self):
        self.assertEqual(canonical_hash({"a": 1, "b": 2}), canonical_hash({"b": 2, "a": 1}))
        self.assertNotEqual(canonical_hash({"a": "x"}), canonical_hash({"a": " x"}))

    def test_risk_context_and_split_classification(self):
        self.assertEqual(classify({"customer_id": "private", "risk_grade": "1"}, "insurance/train/x.csv"),
                         ("consumers", "insurance", "training"))
        self.assertEqual(classify({"product_name": "rider", "mother_product_name": "x"}, "VL_x.zip!/x.json"),
                         ("products", "insurance", "validation"))


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix=".test-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.external = self.root / "external"

    def write_zip(self, filename, entries):
        path = self.raw / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for member, value in entries:
                data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
                archive.writestr(member, data)
        return path

    def test_actual_zip_quirks_and_raw_integrity(self):
        archive = self.write_zip("TS_securities.zip", [
            ("/상품.json ", {"product_name": "A", "investment_strategy": "x"}),
            ("../outside.json", {"product_name": "B", "investment_strategy": "y"})])
        before = archive.read_bytes()
        result, inventory = audit_data(self.raw, self.external)
        self.assertEqual(result["deduplication"]["products/securities"]["records"], 2)
        self.assertEqual(result["trailing_filename_whitespace"], 1)
        self.assertEqual(result["absolute_zip_member_names"], 1)
        self.assertEqual(result["parent_traversal_zip_member_names"], 1)
        self.assertEqual(inventory[0]["member"], "/상품.json ")
        self.assertEqual(archive.read_bytes(), before)
        self.assertTrue(result["raw_integrity"]["before_after_equal"])
        self.assertFalse((self.root / "outside.json").exists())
        self.assertEqual(list(self.raw.iterdir()), [archive])

    def test_cross_split_duplicates_name_conflicts_and_linkage(self):
        product = {"product_name": "A  B", "investment_strategy": "x"}
        self.write_zip("TS_securities.zip", [("/a.json", product),
                                                ("/b.json", {**product, "investment_strategy": "other"})])
        self.write_zip("VS_securities.zip", [("/a.json", product)])
        label = {"cot_id": 1, "category": "증권", "question": "secret", "answer": "secret", "product_names": ["A B", "missing"]}
        self.write_zip("TL_labels.zip", [("/l.json", label)])
        self.write_zip("VL_labels.zip", [("/l.json", label)])
        result, _ = audit_data(self.raw, self.external)
        stats = result["deduplication"]["products/securities"]
        self.assertEqual((stats["records"], stats["unique_content_hashes"], stats["duplicate_records"]), (3, 2, 1))
        self.assertEqual(stats["candidate_name_conflicts"], 1)
        self.assertEqual(stats["split_intersections"], {"training:validation": 1})
        self.assertEqual(result["label_product_names"]["linkage"],
                         {"normalized_only": 2, "unmatched": 2, "ambiguous_content_matches": 2})
        self.assertEqual(result["label_ids_shared_between_splits"], 1)

    def test_bad_file_does_not_stop_remaining_members_or_expose_content(self):
        self.write_zip("TL_securities.zip", [("/bad.json", b'{"secret_person": broken}'),
                                                  ("/ok.json", {"cot_id": 1, "product_names": '["broken"',
                                                                "question": "PRIVATE_TEXT", "answer": "COT_PRIVATE"})])
        result, inventory = audit_data(self.raw, self.external)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["code"], "JSONDecodeError")
        self.assertEqual(result["findings"][0]["code"], "invalid_product_names")
        self.assertEqual(result["schemas"]["labels/securities/training"]["records"], 1)
        output = markdown_report(result, inventory) + json.dumps(result) + json.dumps(inventory)
        for secret in ("PRIVATE_TEXT", "COT_PRIVATE", "secret_person"):
            self.assertNotIn(secret, output)

    def test_no_false_url_from_maturity_and_pdf_filename_matching(self):
        self.write_zip("TS_securities.zip", [("/a.json", {"product_name": "A", "maturity_type": "만기 없음",
                                                               "pdf_filename": "doc.pdf", "source_url": "https://example.invalid",
                                                               "income_rate": "3% (기준 6개월)"}),
                                                       ("/doc.pdf", b"%PDF-test")])
        result, _ = audit_data(self.raw, self.external)
        self.assertEqual(result["pdf"]["matched_reference_records"], 1)
        self.assertEqual(result["product_metadata_field_signals"]["url"], {"/source_url": 1})
        self.assertEqual(result["income_rate"].get("explicit_date_pattern", 0), 0)

    def test_corrupt_archive_and_member_size_limit(self):
        (self.raw / "bad.zip").write_bytes(b"not a ZIP")
        self.write_zip("large.zip", [("/large.json", {"x": "x" * 100})])
        result, _ = audit_data(self.raw, self.external, max_member_bytes=20)
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual(result["schemas"], {})

    def test_outputs_inside_or_ancestors_of_raw_are_rejected(self):
        for report, output in [(self.raw / "audit.md", self.root / "out"),
                               (self.root / "audit.md", self.raw / "out"),
                               (self.root / "audit.md", self.root),
                               (self.root / "out" / "summary.json", self.root / "out")]:
            with self.subTest(report=report, output=output):
                with self.assertRaises(ValueError):
                    validate_outputs(self.raw, report, output)

    def test_cli_reports_are_repeatable_except_timestamp(self):
        self.write_zip("TS_securities.zip", [("/a.json", {"product_name": "A"})])
        report, output = self.root / "audit.md", self.root / "out"
        args = ["--raw-dir", str(self.raw), "--external-dir", str(self.external),
                "--report", str(report), "--output-dir", str(output)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 0)
            before = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            inventory_before = (output / "inventory.jsonl").read_bytes()
            self.assertEqual(main(args), 0)
        after = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        before.pop("generated_at")
        after.pop("generated_at")
        self.assertEqual(before, after)
        self.assertEqual(inventory_before, (output / "inventory.jsonl").read_bytes())

    def test_missing_input_and_cli_parse_error_exit_codes(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--raw-dir", str(self.root / "missing")]), 2)
        self.write_zip("bad.zip", [("/bad.json", b"{bad}")])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--raw-dir", str(self.raw), "--report", str(self.root / "audit.md"),
                                   "--output-dir", str(self.root / "out")]), 1)


if __name__ == "__main__":
    unittest.main()
