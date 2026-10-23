"""Search the collected official-source catalog and verify real local PDFs.

The initial external BNK discovery/download runs via collect_external_docs.py.
This catalog is bounded to those reviewed sources; it is not an internet-wide
search engine. LLM search queries rank its verified product-linked candidates.
"""
import json
import re
from pathlib import Path

from scripts.audit_data import canonical_hash, file_hash
from scripts.prepare_data import atomic_text


class DocumentCatalog:
    def __init__(self, config):
        self.config = config
        self.manifest_file = config.external_dir / "manifest.json"
        manifest = json.loads(self.manifest_file.read_text(encoding="utf-8"))
        self.documents = {d["document_id"]: d for d in manifest["documents"]}
        with config.document_links.open(encoding="utf-8") as source:
            self.links = [json.loads(line) for line in source]
        self.cache = config.chroma_dir.parent / "document_search_cache"
        self.fingerprint = canonical_hash({"manifest": file_hash(self.manifest_file), "links": file_hash(config.document_links)})
        self.cache_hits = 0

    def search(self, product_id, queries):
        key = canonical_hash({"version": 1, "sources": self.fingerprint, "product_id": product_id, "queries": queries})
        path = self.cache / (key + ".json")
        linked = {row["document_id"] for row in self.links if row["product_id"] == product_id and row["review_status"] == "verified_identity_only"}
        if path.is_file():
            cached = json.loads(path.read_text(encoding="utf-8"))
            ids = cached.get("ids", [])
            if set(ids) == linked and cached.get("sources") == self.fingerprint:
                self.cache_hits += 1
            else:
                ids = sorted(linked)
        else:
            terms = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", " ".join(queries)))
            ids = sorted(linked, key=lambda key: (-sum(term in self.documents[key]["title"] for term in terms), key))
        result = []
        for key in ids:
            d = self.documents[key]
            source = (self.config.external_dir / d["source_file"]).resolve()
            if self.config.external_dir.resolve() not in source.parents or file_hash(source) != d["sha256"]:
                raise ValueError("Official PDF source integrity check failed")
            if not d["family_identity_verified"] or d["document_type"] not in {"prospectus", "summary_prospectus", "trust_contract"}:
                continue
            result.append({"id": key, "title": d["title"], "summary": "공식 상품군 문서이며 본문 주장/현재 조건은 별도 검증 필요",
                           "source_uri": d["source_uri"], "source": "BNK자산운용 공식 공시", "product_id": product_id,
                           "verified_product_link": True, "document_type": d["document_type"]})
        atomic_text(path, json.dumps({"sources": self.fingerprint, "ids": ids}, ensure_ascii=False)+"\n")
        return result
