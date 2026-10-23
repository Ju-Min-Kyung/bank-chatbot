"""RAG 1: one securities product, one text and one vector."""
import json

from scripts.audit_data import file_hash
from src.ingestion.vector_store import index_records, managed_collection


def load_product_records(config):
    manifest = json.loads(config.preparation_manifest.read_text(encoding="utf-8"))
    if manifest["status"] == "partial":
        raise ValueError("Partial preparation output cannot be indexed")
    if file_hash(config.products) != manifest["outputs"]["products.jsonl"]["sha256"]:
        raise ValueError("Product input checksum mismatch")
    with config.products.open(encoding="utf-8") as source:
        products = [json.loads(line) for line in source]
    records = []
    for product in products:
        if product["category"] != "securities" or product["source_type"] != "product_metadata":
            raise ValueError("Unexpected product category/source")
        if any(key in product["attributes"] for key in ("cot1", "cot2", "cot3", "answer", "question", "customer_id")):
            raise ValueError("Scenario or consumer data in product evidence")
        meta = {key: product.get(key) or "" for key in ("product_id", "category", "product_name", "company", "product_type", "source_type", "source_file", "source_record_hash")}
        normalized = product["normalized_attributes"]
        risk = normalized["product_risk_grade"]["normalized_value"]
        protected = normalized["principal_protected"]["normalized_value"]
        if risk is not None:
            meta["product_risk_grade"] = risk["grade"]
        if protected is not None:
            meta["principal_protected"] = protected
        meta["income_rate_current_verified"] = False
        records.append({"id": product["product_id"], "text": product["search_text"], "metadata": meta})
    if not records:
        raise ValueError("No products to index")
    return products, records


def build_product_index(client, embedder, records, progress=None):
    collection = managed_collection(client, "financial_products", embedder)
    return index_records(collection, records, embedder, embedder.config.batch_size, progress)
