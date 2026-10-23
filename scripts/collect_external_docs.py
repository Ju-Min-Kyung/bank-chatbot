"""Fetch a reviewed list of official BNK fund PDFs; no model/API/indexing calls.

Discovery lists links actually present in official HTML. Collection uses an
explicit local plan and never guesses PDF URLs or automatically links share
classes. Source bodies are cached locally and inputs in data/raw stay untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_HOSTS = {"www.bnkasset.co.kr", "bnkasset.co.kr"}
MAX_BYTES = 64 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_url(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError("URL outside official allowlist")
    return url


class OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.current = [], None
        self.text = []
        self.frames = []
        self.options, self.option = [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.current = {"href": attrs.get("href", ""), "text": "", "onclick": attrs.get("onclick")}
        elif tag == "iframe":
            self.frames.append(attrs.get("src", ""))
        elif tag == "option":
            self.option = {"value": attrs.get("value", ""), "text": ""}
        elif tag == "img" and self.current:
            self.current["text"] += attrs.get("alt", "") + " "

    def handle_data(self, data):
        self.text.append(data)
        if self.current:
            self.current["text"] += data
        if self.option:
            self.option["text"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current:
            self.current["text"] = " ".join(self.current["text"].split())
            self.links.append(self.current)
            self.current = None
        elif tag == "option" and self.option:
            self.option["text"] = " ".join(self.option["text"].split())
            self.options.append(self.option)
            self.option = None


class Fetcher:
    def __init__(self, external_dir, offline=False):
        self.cache = external_dir / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.requests = 0
        self.cache_hits = 0
        self.offline = offline
        self.opener = urllib.request.build_opener(OfficialRedirects())

    def get(self, url):
        validate_url(url)
        key = digest(url.encode("utf-8"))
        body_path, info_path = self.cache / f"{key}.body", self.cache / f"{key}.json"
        if body_path.is_file() and info_path.is_file():
            body = body_path.read_bytes()
            info = json.loads(info_path.read_text(encoding="utf-8"))
            if digest(body) == info["sha256"] and info["url"] == url:
                self.cache_hits += 1
                return body, info
        if self.offline:
            raise RuntimeError("Verified cache entry unavailable in offline mode")
        req = urllib.request.Request(url, headers={"User-Agent": "bank-chatbot-document-collector/1.0 (local educational research)",
                                                  "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.5"})
        self.requests += 1
        with self.opener.open(req, timeout=30) as response:
            validate_url(response.url)
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("Response exceeds file limit")
            info = {"url": url, "final_url": response.url, "collected_at": now(),
                    "content_type": response.headers.get("Content-Type"),
                    "charset": response.headers.get_content_charset(),
                    "content_disposition": response.headers.get("Content-Disposition"),
                    "last_modified": response.headers.get("Last-Modified"),
                    "sha256": digest(body), "bytes": len(body)}
        body_path.write_bytes(body)
        info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return body, info

    def html(self, url):
        body, info = self.get(url)
        parser = Links()
        # BNK declares EUC-KR; CP949 is its common compatible superset.
        charset = info.get("charset") or "euc-kr"
        parser.feed(body.decode("cp949" if charset.lower() in {"euc-kr", "ks_c_5601-1987"} else charset))
        links = []
        for link in parser.links:
            absolute = urllib.parse.urljoin(url, link["href"])
            if urllib.parse.urlsplit(absolute).hostname in ALLOWED_HOSTS:
                links.append({"url": absolute, "text": link["text"], "onclick": link["onclick"]})
        return parser, links, info


def discover(fetcher, external_dir):
    urls = ["https://www.bnkasset.co.kr/robots.txt", *(
        f"https://www.bnkasset.co.kr/product/{page}.aspx"
        for page in ("stocks", "bond", "mixed", "mmf", "tdf", "etc"))]
    results = []
    for url in urls:
        try:
            if url.endswith("robots.txt"):
                body, info = fetcher.get(url)
                result = {"url": url, "text": body.decode("utf-8-sig"), "metadata": info}
            else:
                _, links, info = fetcher.html(url)
                result = {"url": url, "links": [link for link in links if "fund_cd=" in link["url"]], "metadata": info}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:
            results.append({"url": url, "error": type(exc).__name__})
            print(json.dumps(results[-1]), flush=True)
    (external_dir / "discovery.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compact(value):
    return re.sub(r"\s+", "", value)


def title_date(title):
    from datetime import date
    match = re.search(r"(20\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", title)
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups())).isoformat()
    except ValueError:
        return None


def document_kind(title):
    if "대비표" in title:
        return "amendment_table"
    if "간이투자설명서" in title:
        return "summary_prospectus"
    if "투자설명서" in title:
        return "prospectus"
    if any(word in title for word in ("집합투자규약", "신탁계약서", "약관")):
        return "trust_contract"
    return None


def pdf_pages(data):
    from pypdf import PdfReader
    if not data.lstrip().startswith(b"%PDF-"):
        raise ValueError("Not a PDF response")
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted or not reader.pages:
        raise ValueError("Encrypted or empty PDF")
    return [page.extract_text() or "" for page in reader.pages]


def confirmed_class_pages(pages, share_class):
    marker = r"\s*".join(re.escape(part) for part in share_class)
    pattern = re.compile(r"(?:종류|클래스|Class)\s*[(\[]?\s*" + marker + r"(?![A-Za-z0-9-])|[（(]\s*" + marker + r"\s*[）)]")
    bare_cell = re.compile(r"(?<![A-Za-z0-9-])" + marker + r"(?![A-Za-z0-9-])")
    matches = []
    for i, text in enumerate(pages):
        # Some official tables have one '(클래스) 종류' header and bare cells.
        table_rows = [line for line in text.splitlines() if re.search(r"\(클래스\)\s*종류", line)]
        if pattern.search(text) or any(bare_cell.search(line) for line in table_rows):
            matches.append(i + 1)
    return matches


def collect(fetcher, external_dir, plan_path, products_path):
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    products = [json.loads(line) for line in products_path.open(encoding="utf-8")]
    documents, families, failures = [], [], []
    for target in plan["funds"]:
        family = target["fund_name"]
        group, group_links, group_info = fetcher.html(target["fund_page"])
        detail, _, detail_info = fetcher.html(target["detail_page"])
        if target["detail_page"] not in [urllib.parse.urljoin(target["fund_page"], frame) for frame in group.frames]:
            raise ValueError("Plan detail URL is not the official page iframe")
        # Only classes explicitly listed by the official group page are accepted.
        classes = set()
        for link in group_links:
            callback = link.get("onclick") or ""
            if callback.startswith("clickJaFund("):
                strings = re.findall(r'"([^"\n]*)"', callback)
                if strings:
                    classes.add(re.sub(r"^Class", "", strings[-1].strip()))
        candidates = []
        for option in detail.options:
            if not re.fullmatch(r"schctgr=[12]&seq=\d+", option["value"]):
                continue
            if compact(family) not in compact(option["text"]):
                continue
            candidates.append({"url": "https://www.bnkasset.co.kr/customer/disclosure_view.aspx?" + option["value"],
                               "title": option["text"], "document_date": title_date(option["text"]),
                               "category": urllib.parse.parse_qs(option["value"])["schctgr"][0]})
        selected = []
        for category in ("2", "1"):
            subset = [item for item in candidates if item["category"] == category]
            if subset:
                selected.append(max(subset, key=lambda x: (x["document_date"] or "", x["url"])))
            else:
                failures.append({"fund_name": family, "code": "no_listed_contract" if category == "1" else "no_listed_prospectus"})
        fund_docs = []
        for notice in selected:
            _, attachments, notice_info = fetcher.html(notice["url"])
            for attachment in attachments:
                if urllib.parse.urlsplit(attachment["url"]).path != "/download.aspx":
                    continue
                kind = document_kind(attachment["text"])
                if kind is None:
                    continue
                body, metadata = fetcher.get(attachment["url"])
                pages = pdf_pages(body)
                matching_pages = [i + 1 for i, text in enumerate(pages) if compact(family) in compact(text)]
                doc_id = "doc_" + metadata["sha256"]
                dest = external_dir / "bnk" / f"{doc_id}.pdf"
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists() or digest(dest.read_bytes()) != metadata["sha256"]:
                    dest.write_bytes(body)
                entry = {
                    "document_id": doc_id, "fund_name": family, "document_type": kind,
                    "title": attachment["text"].split(" 다운로드 받기", 1)[0],
                    "source_type": "official_fund_document", "source_uri": attachment["url"],
                    "source_page": notice["url"], "fund_page": target["fund_page"],
                    "document_date": notice["document_date"], "date_basis": "official_notice_title",
                    "collected_at": metadata["collected_at"], "source_file": dest.relative_to(external_dir).as_posix(),
                    "sha256": metadata["sha256"], "bytes": len(body), "page_count": len(pages),
                    "family_name_pages": matching_pages,
                    "family_identity_verified": any(page <= 2 for page in matching_pages) and compact(family) in compact(attachment["text"]),
                    "content_identity_check": "family name in filename and first two PDF pages",
                    "version_scope": "latest_dated_entry_listed_on_fund_page_at_collection",
                    "source_page_sha256": notice_info["sha256"],
                    "rights": {"notice": "COPYRIGHT BNK ASSET MANAGEMENT. ALL RIGHTS RESERVED",
                               "local_reference_collected": True, "redistribution_permission_verified": False,
                               "external_model_transmission_permission_verified": False},
                }
                documents.append(entry)
                fund_docs.append((entry, pages))
                print(json.dumps({"document": entry["title"], "pages": len(pages), "family_verified": entry["family_identity_verified"]}, ensure_ascii=False), flush=True)
        relations, review = [], []
        for product in products:
            match = re.fullmatch(r"(.*)\(([^()]*)\)", product["product_name"])
            if not match or compact(match[1]) != compact(family):
                continue
            share_class = match[2]
            if share_class not in classes:
                review.append({"product_id": product["product_id"], "product_name": product["product_name"],
                               "code": "class_not_exactly_listed_on_current_official_page"})
                continue
            for document, pages in fund_docs:
                if not document["family_identity_verified"]:
                    continue
                if document["document_type"] == "amendment_table":
                    continue
                # Require a class marker in financial class context for prospectuses.
                # Contracts link at fund level; they do not verify class-specific fees.
                class_pages = confirmed_class_pages(pages, share_class)
                if document["document_type"] != "trust_contract" and not class_pages:
                    review.append({"product_id": product["product_id"], "document_id": document["document_id"],
                                   "code": "class_not_confirmed_in_pdf"})
                    continue
                relations.append({"product_id": product["product_id"], "product_name": product["product_name"],
                                  "document_id": document["document_id"], "source_file": document["source_file"],
                                  "source_uri": document["source_uri"], "fund_name": family,
                                  "share_class": share_class, "scope": "fund_contract" if document["document_type"] == "trust_contract" else "fund_and_listed_class",
                                  "family_identity_pages": document["family_name_pages"], "class_marker_pages": class_pages,
                                  "class_listing_source": target["fund_page"], "review_status": "verified_identity_only"})
        families.append({"fund_name": family, "fund_page": target["fund_page"], "detail_page": target["detail_page"],
                         "listed_share_classes": sorted(classes), "selected_notices": selected,
                         "documents": len(fund_docs), "relations": relations, "review": review,
                         "fund_page_sha256": group_info["sha256"], "detail_page_sha256": detail_info["sha256"]})
    all_relations = [r for family in families for r in family["relations"]]
    for document in documents:
        document["product_ids"] = sorted({r["product_id"] for r in all_relations if r["document_id"] == document["document_id"]})
    manifest = {"schema_version": 1, "generated_at": now(), "collection_method": "curated_official_links_no_llm",
                "network_requests": fetcher.requests, "cache_hits": fetcher.cache_hits,
                "funds": families, "documents": documents, "failures": failures,
                "totals": {"funds": len(families), "pdfs": len(documents), "pages": sum(d["page_count"] for d in documents),
                           "primary_documents": sum(d["document_type"] != "amendment_table" for d in documents),
                           "amendment_tables": sum(d["document_type"] == "amendment_table" for d in documents),
                           "linked_products": len({r["product_id"] for r in all_relations}), "product_document_relations": len(all_relations),
                           "review_items": sum(len(f["review"]) for f in families)},
                "embedding_performed": False, "external_model_calls": 0,
                "limitations": ["Not coverage of all 8000 products", "No guarantee of globally latest version",
                                "No external model transmission or redistribution permission established",
                                "Share class markers establish identity, not validation of all product terms"]}
    (external_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    relations_path = ROOT / "data/processed/document_links.jsonl"
    relations_path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in all_relations), encoding="utf-8")
    write_collection_report(manifest, external_dir)
    print(json.dumps(manifest["totals"], ensure_ascii=False), flush=True)
    return manifest


def write_collection_report(manifest, external_dir):
    kinds = {"prospectus": "투자설명서", "summary_prospectus": "간이투자설명서", "trust_contract": "집합투자규약",
             "amendment_table": "변경·정정대비표"}
    totals = manifest["totals"]
    lines = ["# 공식 상품문서 수집 결과", "", f"기록 시각(UTC): {manifest['generated_at']}", "",
             f"BNK자산운용 공식 사이트에서 **{totals['funds']}개 상품군**, PDF **{totals['pdfs']}개 / {totals['pages']}페이지**를 확보했습니다.",
             f"핵심 문서 {totals['primary_documents']}개(상품군별 투자설명서·간이투자설명서·집합투자규약)와 대비표 {totals['amendment_tables']}개를 구분합니다.",
             f"현재 데이터의 클래스별 상품 ID {totals['linked_products']}개와 문서 관계 {totals['product_document_relations']}개를 기록했습니다.", "",
             f"이 실행의 외부 HTTP 요청: {manifest['network_requests']}회; 검증된 캐시 사용: {manifest['cache_hits']}회.",
             "원본 AI Hub 데이터와 products.jsonl은 수정하지 않았습니다. 임베딩·외부 모델 호출은 하지 않았습니다.", "",
             "## 문서 목록", "", "| 상품군 | 문서 | 공시 시행일 | 페이지 | 원본 PDF | 공식 공시 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for doc in manifest["documents"]:
        local = "/" + (external_dir / doc["source_file"]).resolve().as_posix()
        lines.append(f"| {doc['fund_name']} | {kinds[doc['document_type']]} | {doc['document_date'] or '미확인'} | {doc['page_count']} | [열기]({local}) | [출처]({doc['source_page']}) |")
    lines += ["", "## 상품 연결 범위", "",
              "상품명에서 마지막 클래스 괄호만 분리하고 펀드명은 공백만 제외해 대조했습니다. 모·자펀드, H/UH, 번호, 종류 표기를 임의 변경하지 않았습니다.",
              "공식 상품 페이지에 같은 클래스가 명시된 경우에만 연결했습니다. Ae→A-e 같은 별칭도 자동 적용하지 않았습니다.",
              "설명서는 본문 클래스 표기를 추가 확인하고 해당 물리 PDF 페이지 번호(1부터)를 관계에 저장했습니다.",
              "규약은 상품군 공통 계약 범위로 연결합니다. 클래스별 보수·환매 조건까지 모두 검증했다는 뜻은 아닙니다.",
              "대비표는 완전한 규약/설명서로 사용하지 않으며 상품 근거 관계에서 제외했습니다.", "",
              "`data/external/manifest.json`: 원문 파일명·공식 URL·수집일·공시일·SHA-256·페이지 수·정합성 검사·권리 확인 상태.",
              "`data/processed/document_links.jsonl`: product_id↔document_id 관계와 확인한 페이지·연결 범위.",
              "기존 상품의 document_reference=unlinked 필드는 재작성하지 않았습니다. 문서 검색 단계에서는 이 별도 연결표를 결합해야 합니다.", "",
              "## 검토 대상", "", "| 상품군 | 상품 ID | 항목 |", "| --- | --- | --- |"]
    for family in manifest["funds"]:
        for item in family["review"]:
            suffix = f" / {item['document_id']}" if "document_id" in item else ""
            lines.append(f"| {family['fund_name']} | {item['product_id']} | {item['code']}{suffix} |")
    lines += ["", "클래스 미확인은 현재 페이지 또는 PDF 텍스트로 확인하지 못했다는 뜻입니다. 문서가 적용되지 않는다고 단정하지 않습니다.", "",
              "## 시점·사용 범위", "",
              "각 상품 상세의 관련공시 목록에서 날짜가 가장 최근인 설명서 공시와 규약 공시를 선택했습니다. 사이트 전체의 절대적 최신 버전을 보장하지 않습니다.",
              "표의 날짜는 공시 제목에 명시된 시행일입니다. PDF 자체의 작성기준일과 다를 수 있고 AI Hub 메타데이터 기준일을 대체하지 않습니다.",
              "공식 사이트는 ALL RIGHTS RESERVED를 표시합니다. 공개 첨부를 로컬 참고용으로 확보했으며 재배포 및 외부 모델 전송 허용은 확인되지 않았습니다.",
              "PDF·HTML 캐시는 Git 제외 경로에 보관하며 외부 LLM에는 전송하지 않았습니다. 이용 허용 범위를 확인한 뒤 모델 연동 여부를 결정해야 합니다.",
              "이 수집은 명시적인 공식 링크를 선별한 초기 묶음입니다. F2003/F2004의 LLM 검색어·후보 선별 프롬프트 및 F2005 문서 색인은 아직 구현하지 않았습니다.", ""]
    (ROOT / "docs/external_documents.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--inspect", nargs="*")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--products", type=Path, default=ROOT / "data/processed/products.jsonl")
    parser.add_argument("--offline", action="store_true", help="Use verified cache only, fail on any missing cache entry")
    parser.add_argument("--external-dir", type=Path, default=ROOT / "data/external")
    args = parser.parse_args()
    external = args.external_dir.resolve()
    raw = (ROOT / "data/raw").resolve()
    if external == raw or raw in external.parents or external in raw.parents:
        raise ValueError("External output overlaps raw input")
    external.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(external, offline=args.offline)
    if args.discover:
        discover(fetcher, external)
    elif args.plan:
        collect(fetcher, external, args.plan, args.products)
    elif args.inspect:
        for url in args.inspect:
            parsed, links, info = fetcher.html(url)
            relevant = [link for link in links if any(part in link["url"] for part in ("disclosure_view", "download.aspx", "?fund_cd="))]
            # Text only from the content region isn't available in this minimal parser;
            # links plus date/title snippets suffice for manual source review.
            print(json.dumps({"url": url, "links": relevant, "iframes": [urllib.parse.urljoin(url, f) for f in parsed.frames], "metadata": info}, ensure_ascii=False), flush=True)
    else:
        parser.error("Choose --discover, --inspect or --plan")
    print(json.dumps({"network_requests": fetcher.requests, "cache_hits": fetcher.cache_hits}), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
