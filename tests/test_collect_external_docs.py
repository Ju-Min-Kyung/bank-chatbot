"""Offline tests for official source boundaries, cache and class identity checks."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.collect_external_docs import (
    Fetcher, Links, confirmed_class_pages, digest, document_kind, pdf_pages,
    title_date, validate_url,
)


class CollectionTests(unittest.TestCase):
    def test_url_allowlist_blocks_external_and_credential_urls(self):
        self.assertEqual(validate_url("https://www.bnkasset.co.kr/download.aspx?seq=1"),
                         "https://www.bnkasset.co.kr/download.aspx?seq=1")
        for url in ("https://www.bnkasset.co.kr.evil.test/a.pdf", "http://www.bnkasset.co.kr/a.pdf",
                    "file:///etc/passwd", "https://user:secret@www.bnkasset.co.kr/a.pdf", "https://www.bnkasset.co.kr:444/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)

    def test_document_types_do_not_mislabel_amendment_tables(self):
        cases = {"정정대비표_투자설명서.pdf": "amendment_table", "집합투자규약 변경대비표.pdf": "amendment_table",
                 "[정정]투자설명서.pdf": "prospectus", "간이투자설명서.pdf": "summary_prospectus",
                 "[변경]집합투자규약.pdf": "trust_contract", "자산운용보고서.pdf": None}
        for text, expected in cases.items():
            self.assertEqual(document_kind(text), expected)

    def test_class_markers_preserve_token_boundaries(self):
        pages = ["Class A Class A-e Class C Class C-e", "Class C-P2, Class C-P2e", "종류 C-P (S-P)"]
        self.assertEqual(confirmed_class_pages(pages, "A"), [1])
        self.assertEqual(confirmed_class_pages(pages, "C-P"), [3])
        self.assertEqual(confirmed_class_pages(pages, "C-P2"), [2])
        self.assertEqual(confirmed_class_pages(pages, "Ae"), [])
        self.assertEqual(confirmed_class_pages(pages, "C-P2e"), [2])
        self.assertEqual(confirmed_class_pages(["(클래스) 종류 A A-e C C-e C-P2e"], "C-e"), [1])
        self.assertEqual(confirmed_class_pages(["(클래스) 종류 C-P2e"], "C-P"), [])

    def test_invalid_title_dates_not_used(self):
        self.assertEqual(title_date("펀드_2026.08.07 변경시행"), "2026-08-07")
        self.assertIsNone(title_date("2026.02.30"))
        self.assertIsNone(title_date("날짜 미제공"))

    def test_html_entities_iframes_and_notice_options_are_preserved(self):
        parser = Links()
        parser.feed('<iframe src="detail.aspx?a=1&amp;b=2"></iframe><select><option value="schctgr=1&amp;seq=9">규약</option></select>'
                    '<a href="/download.aspx?a=1&amp;b=2"><img alt="규약.pdf"/>규약.pdf</a>')
        self.assertEqual(parser.frames, ["detail.aspx?a=1&b=2"])
        self.assertEqual(parser.options, [{"value": "schctgr=1&seq=9", "text": "규약"}])
        self.assertEqual(parser.links[0]["href"], "/download.aspx?a=1&b=2")

    def test_html_disguised_as_pdf_is_rejected(self):
        with self.assertRaises(ValueError):
            pdf_pages(b"<html>download error</html>")

    def test_verified_cache_reuse_makes_no_request(self):
        with tempfile.TemporaryDirectory(prefix=".test-", dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder)
            fetcher = Fetcher(root)
            url, body = "https://www.bnkasset.co.kr/test.pdf", b"test bytes"
            key = digest(url.encode())
            (fetcher.cache / f"{key}.body").write_bytes(body)
            (fetcher.cache / f"{key}.json").write_text(json.dumps({"url": url, "sha256": digest(body)}), encoding="utf-8")
            actual, _ = fetcher.get(url)
            self.assertEqual(actual, body)
            self.assertEqual(fetcher.requests, 0)
            self.assertEqual(fetcher.cache_hits, 1)

    def test_offline_missing_cache_never_contacts_network(self):
        with tempfile.TemporaryDirectory(prefix=".test-", dir=Path(__file__).resolve().parent) as folder:
            fetcher = Fetcher(Path(folder), offline=True)
            with self.assertRaises(RuntimeError):
                fetcher.get("https://www.bnkasset.co.kr/test.pdf")
            self.assertEqual(fetcher.requests, 0)


if __name__ == "__main__":
    unittest.main()
