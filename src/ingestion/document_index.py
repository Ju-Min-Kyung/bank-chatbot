"""RAG 2: verified PDF page chunks with per-product provenance."""
import json
from collections import defaultdict

from scripts.audit_data import canonical_hash, file_hash
from scripts.prepare_data import atomic_text
from src.ingestion.embeddings import text_chunks
from src.ingestion.vector_store import managed_collection, index_records


def document_pages(config, document):
    import pypdf
    path = (config.external_dir / document["source_file"]).resolve()
    if config.external_dir.resolve() not in path.parents or file_hash(path) != document["sha256"]:
        raise ValueError("Document path/hash mismatch")
    cache_file = config.products.parent / "document_text_cache" / (document["document_id"] + ".json")
    if cache_file.is_file():
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
        if cache.get("pdf_sha256") == document["sha256"] and cache.get("pypdf_version") == pypdf.__version__ and canonical_hash({"pages": cache["pages"]}) == cache.get("text_sha256"):
            return cache["pages"]
    reader = pypdf.PdfReader(path)
    if reader.is_encrypted or len(reader.pages) != document["page_count"]:
        raise ValueError("PDF page count/encryption mismatch")
    pages = [page.extract_text() or "" for page in reader.pages]
    if not any(text.strip() for text in pages):
        raise ValueError("No extractable PDF text")
    cache = {"pdf_sha256": document["sha256"], "pypdf_version": pypdf.__version__, "pages": pages,
             "text_sha256": canonical_hash({"pages": pages})}
    atomic_text(cache_file, json.dumps(cache, ensure_ascii=False) + "\n")
    return pages


def load_document_records(config, embedder, products):
    manifest = json.loads((config.external_dir / "manifest.json").read_text(encoding="utf-8"))
    documents = {document["document_id"]: document for document in manifest["documents"]}
    product_map = {product["product_id"]: product for product in products}
    links = [json.loads(line) for line in config.document_links.open(encoding="utf-8")]
    by_document = defaultdict(list)
    seen = set()
    for link in links:
        key = (link["product_id"], link["document_id"])
        if key in seen or link["product_id"] not in product_map or link["document_id"] not in documents:
            raise ValueError("Invalid/duplicate product-document relation")
        seen.add(key)
        doc = documents[link["document_id"]]
        if doc["document_type"] not in {"prospectus", "summary_prospectus", "trust_contract"} or not doc["family_identity_verified"]:
            raise ValueError("Unverified document or amendment table in evidence")
        if link["review_status"] != "verified_identity_only" or not link["family_identity_pages"]:
            raise ValueError("Unverified document link")
        by_document[link["document_id"]].append(link)
    records, unique_chunks, page_rows = [], [], []
    for document_id, relations in sorted(by_document.items()):
        document = documents[document_id]
        pages = document_pages(config, document)
        chunk_index = 0
        for page_number, text in enumerate(pages, 1):
            page_rows.append({"document_id": document_id, "page_number": page_number,
                              "source_file": document["source_file"], "text": text})
            for part in text_chunks(text, embedder.tokenizer, config.max_tokens, config.chunk_size, config.chunk_overlap):
                unique_chunks.append({"document_id": document_id, "page_number": page_number,
                                      "chunk_index": chunk_index, **part})
                for link in relations:
                    pid = link["product_id"]
                    meta = {"product_id": pid, "product_name": product_map[pid]["product_name"],
                            "document_id": document_id, "chunk_index": chunk_index, "page_number": page_number,
                            "char_start": part["char_start"], "char_end": part["char_end"], "token_count": part["token_count"],
                            "title": document["title"], "document_type": document["document_type"],
                            "source_type": "official_pdf", "source_uri": document["source_uri"], "source_page": document["source_page"],
                            "source_file": str((config.external_dir / document["source_file"]).resolve()),
                            "source_sha256": document["sha256"], "document_date": document.get("document_date") or "",
                            "collected_at": document["collected_at"], "link_scope": link["scope"]}
                    records.append({"id": f"{pid}:{document_id}:{chunk_index}", "text": part["text"], "metadata": meta})
                chunk_index += 1
    if not records:
        raise ValueError("No verified document chunks to index")
    for name, rows in (("document_pages.jsonl", page_rows), ("document_chunks.jsonl", records)):
        atomic_text(config.products.parent / name, "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows))
    stats = {"documents": len(by_document), "pages": len(page_rows), "unique_chunks": len(unique_chunks),
             "product_chunk_records": len(records), "linked_products": len({row["metadata"]["product_id"] for row in records}),
             "max_chunk_chars": max(len(part["text"]) for part in unique_chunks),
             "max_chunk_tokens": max(part["token_count"] for part in unique_chunks),
             "document_chunks_sha256": file_hash(config.products.parent / "document_chunks.jsonl")}
    return records, stats


def build_document_index(client, embedder, records, progress=None):
    collection = managed_collection(client, "financial_documents", embedder)
    return index_records(collection, records, embedder, embedder.config.batch_size, progress)
