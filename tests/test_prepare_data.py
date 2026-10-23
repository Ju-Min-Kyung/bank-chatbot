"""Synthetic ZIP integration tests; all temporary files stay in the workspace."""
import contextlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.audit_data import audit_data, file_hash
from scripts.prepare_data import main, prepare_data, source_records, validate_targets


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".test-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.output = self.root / "processed"
        self.report = self.root / "preparation.md"

    def archive(self, name, entries):
        path = self.raw / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for member, value in entries:
                archive.writestr(member, value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8"))
        return path

    def product(self, name="상품(A-e)", **changes):
        return {"product_name": name, "product_type": "주식형", "protected_type": "원금 비보장형",
                "risk_grade": "2등급(높은위험)", "income_rate": "1.2%(기준 6개월)",
                "maturity_type": "만기 없음", "investment_strategy": "전략_x000D_검증",
                "liquidity_conditions": "중도해지 가능", "loss_rate": "최대 100%",
                "tax_type": "무", "payment_type": "추가납입", **changes}

    def label(self, cot_id=1, names=None):
        return {"cot_id": cot_id, "category": "증권", "question": f"질문 {cot_id}",
                "query_type": "상품 비교 추천형", "product_names": ["상품(A-e)"] if names is None else names,
                "answer": "PRIVATE_ANSWER", "cot1": "PRIVATE_REASON", "age": "PRIVATE_PROFILE"}

    def read_rows(self, relative):
        return [json.loads(line) for line in (self.output / relative).read_text(encoding="utf-8").splitlines()]

    def test_full_pipeline_securities_only_and_review_isolation(self):
        raw_product = self.product()
        self.archive("products/TS_securities.zip", [("/a.json ", raw_product)])
        self.archive("products/TS_insurance.zip", [("/i.json", {"product_name": "특약", "mother_product_name": "보험주계약"})])
        self.archive("labels/TL_securities.zip", [("/a.json", self.label()),
                                                  ("/bad.json", self.label(2, '["상품(A-e)"')),
                                                  ("/missing.json", self.label(3, ["없는 상품"]))])
        self.archive("labels/VL_securities.zip", [("/a.json", self.label(4))])
        before = {p.relative_to(self.raw).as_posix(): p.read_bytes() for p in self.raw.rglob("*.zip")}
        manifest = prepare_data(self.raw, self.output, self.report)
        self.assertEqual(manifest["scope"], "securities_only")
        self.assertEqual(manifest["status"], "ready_with_review")
        self.assertEqual(manifest["skipped_records"], {"products/insurance": 1})
        self.assertEqual(manifest["products"]["accepted"], 1)
        self.assertEqual(manifest["labels"]["evaluation_ready"], 2)
        self.assertEqual(manifest["labels"]["review_records"], 2)
        product = self.read_rows("products.jsonl")[0]
        self.assertEqual(product["attributes"], raw_product)
        self.assertTrue(product["source_file"].endswith("/a.json "))
        train = self.read_rows("evaluation/training.jsonl")[0]
        self.assertEqual(train["product_ids"], [product["product_id"]])
        self.assertEqual(self.read_rows("evaluation/test.jsonl"), [])
        all_output = "".join((self.output / name).read_text(encoding="utf-8") for name in manifest["outputs"])
        for secret in ("PRIVATE_ANSWER", "PRIVATE_REASON", "PRIVATE_PROFILE"):
            self.assertNotIn(secret, all_output)
        after = {p.relative_to(self.raw).as_posix(): p.read_bytes() for p in self.raw.rglob("*.zip")}
        self.assertEqual(before, after)
        self.assertEqual(len(list(self.raw.rglob("*.json"))), 0)

    def test_outputs_and_ids_identical_on_repeated_runs(self):
        self.archive("TS_securities.zip", [("/a.json", self.product())])
        self.archive("VS_securities.zip", [("/a.json", self.product())])
        self.archive("TL_securities.zip", [("/l.json", self.label())])
        first = prepare_data(self.raw, self.output, self.report)
        files_before = {name: (self.output / name).read_bytes() for name in first["outputs"]}
        second = prepare_data(self.raw, self.output, self.report)
        first.pop("generated_at")
        second.pop("generated_at")
        self.assertEqual(first, second)
        self.assertEqual(first["products"]["duplicates_collapsed"], 1)
        self.assertEqual(files_before, {name: (self.output / name).read_bytes() for name in second["outputs"]})
        for name, info in second["outputs"].items():
            self.assertEqual(file_hash(self.output / name), info["sha256"])

    def test_identity_conflicts_excluded_and_partial_status(self):
        self.archive("TS_securities.zip", [("/a.json", self.product()),
                                             ("/b.json", self.product(risk_grade="3등급(다소높은위험)")),
                                             ("/c.json", self.product("다른상품(C)"))])
        manifest = prepare_data(self.raw, self.output, self.report)
        self.assertEqual((manifest["products"]["accepted"], manifest["products"]["review_records"]), (1, 2))
        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(self.read_rows("products.jsonl")[0]["product_name"], "다른상품(C)")

    def test_corrupt_input_records_error_and_cli_returns_partial(self):
        self.archive("TS_securities.zip", [("/a.json", self.product()), ("/bad.json", b'{"PRIVATE": broken}')])
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            code = main(["--raw-dir", str(self.raw), "--output-dir", str(self.output), "--report", str(self.report)])
        self.assertEqual(code, 1)
        self.assertEqual(self.read_rows("review/input_issues.jsonl")[0]["stage"], "audit")
        self.assertNotIn("PRIVATE", log.getvalue() + self.report.read_text(encoding="utf-8"))

    def test_input_tampering_between_audit_and_normalization_detected(self):
        self.archive("TS_securities.zip", [("/a.json", self.product())])
        _, inventory = audit_data(self.raw, self.root / "external")
        self.archive("TS_securities.zip", [("/a.json", self.product("변경상품"))])
        with self.assertRaises(ValueError):
            list(source_records(self.raw, inventory))

    def test_raw_output_and_report_conflicts_rejected(self):
        self.archive("TS_securities.zip", [("/a.json", self.product())])
        for output, report in ((self.raw / "out", self.report), (self.output, self.raw / "report.md"),
                               (self.output, self.output / "products.jsonl")):
            with self.assertRaises(ValueError):
                validate_targets(self.raw, output, report)

    def test_existing_output_hardlink_to_raw_is_rejected(self):
        archive = self.archive("TS_securities.zip", [("/a.json", self.product())])
        self.output.mkdir()
        target = self.output / "products.jsonl"
        try:
            os.link(archive, target)
        except OSError as exc:
            self.skipTest(f"Hardlinks unavailable: {type(exc).__name__}")
        before = archive.read_bytes()
        with self.assertRaises(ValueError):
            prepare_data(self.raw, self.output, self.report)
        self.assertEqual(before, archive.read_bytes())

    def test_missing_products_does_not_replace_previous_outputs(self):
        self.archive("TS_securities.zip", [("/a.json", self.product())])
        prepare_data(self.raw, self.output, self.report)
        before = (self.output / "products.jsonl").read_bytes()
        # Overwrite synthetic input only: the next run has no securities products.
        self.archive("TS_securities.zip", [("/a.json", {"product_name": "보험", "mother_product_name": "주계약"})])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--raw-dir", str(self.raw), "--output-dir", str(self.output), "--report", str(self.report)]), 2)
        self.assertEqual(before, (self.output / "products.jsonl").read_bytes())


if __name__ == "__main__":
    unittest.main()
