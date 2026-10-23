"""Read two local vector collections with mandatory selected product evidence."""
import json
import unicodedata

from src.ingestion.vector_store import persistent_client
from src.rag.mmr import compact, diversity_metrics, select_indices, validate_search


class VectorRetriever:
    def __init__(self, config, embedder):
        status = json.loads((config.chroma_dir / "build_status.json").read_text(encoding="utf-8"))
        if status["status"] != "ready":
            raise ValueError("Vector index build is incomplete")
        self.embedder = embedder
        self.client = persistent_client(config.chroma_dir)
        # get_collection avoids creating an empty collection during read-only use.
        for name in ("financial_products", "financial_documents"):
            current = self.client.get_collection(name, embedding_function=None)
            if (current.metadata or {}).get("embedding_signature") != embedder.signature:
                raise ValueError("Query/index embedding signature mismatch")
        self.products = self.client.get_collection("financial_products", embedding_function=None)
        self.documents = self.client.get_collection("financial_documents", embedding_function=None)

    def search_products(self, question, top_k=4, *, name_first=True, allowed_ids=None):
        if not 1 <= top_k <= 100:
            raise ValueError("top_k must be between 1 and 100")
        if not question.strip():
            raise ValueError("Enter a product name or search question")
        allowed = None if allowed_ids is None else set(allowed_ids)
        if allowed is not None and not allowed:
            return []
        # Official identifiers are often poorly ranked by a sentence encoder.
        # Match literal names without dropping class punctuation or inventing aliases.
        matches = []
        if name_first:
            normalize = lambda value: "".join(unicodedata.normalize("NFC", value).casefold().split())
            query_name = normalize(question)
            if len(query_name) >= 4:
                catalog = self.products.get(include=["metadatas"])
                for key, meta in zip(catalog["ids"], catalog["metadatas"]):
                    if allowed is not None and key not in allowed:
                        continue
                    name = normalize(meta.get("product_name", ""))
                    if name and (query_name in name or name in query_name):
                        rank = (query_name != name, query_name not in name, abs(len(name)-len(query_name)), name, key)
                        matches.append((rank, key))
        named_ids = [key for _, key in sorted(matches)[:top_k]]
        rows = []
        if named_ids:
            data = self.products.get(ids=named_ids, include=["documents", "metadatas"])
            by_id = {key: {"id": key, "content": text, **meta, "distance": None, "match_type": "official_name"}
                     for key, text, meta in zip(data["ids"], data["documents"], data["metadatas"])}
            rows = [by_id[key] for key in named_ids]
            if len(rows) == top_k:
                return rows
        options = {} if allowed is None else {"where": {"product_id": {"$in": sorted(allowed)}}}
        result = self.products.query(query_embeddings=self.embedder.embed([question]),
                                     n_results=min(top_k, self.products.count(), len(allowed) if allowed is not None else top_k),
                                     include=["documents", "metadatas", "distances"], **options)
        rows.extend({"id": key, "content": text, **meta, "distance": distance, "match_type": "semantic"}
                    for key, text, meta, distance in zip(result["ids"][0], result["documents"][0], result["metadatas"][0], result["distances"][0])
                    if key not in named_ids)
        return rows[:top_k]

    def document_candidates(self, question, selected, fetch_k, document_ids=None):
        if not question.strip():
            raise ValueError("Enter a search question")
        if not selected:
            raise ValueError("Select at least one product")
        if not self.documents.count() or document_ids == []:
            return [], [], None
        filters = [{"product_id": {"$in": selected}}]
        if document_ids is not None:
            filters.append({"document_id": {"$in": list(dict.fromkeys(document_ids))}})
        query = self.embedder.embed([question])[0]
        result = self.documents.query(query_embeddings=[query],
            where=filters[0] if len(filters) == 1 else {"$and": filters},
            n_results=min(fetch_k, self.documents.count()), include=["documents", "metadatas", "distances", "embeddings"])
        rows, vectors, seen = [], [], set()
        hits = zip(result["ids"][0], result["documents"][0], result["metadatas"][0], result["distances"][0], result["embeddings"][0])
        # Deterministic tie-breaks matter when shared fund/class vectors coincide.
        for key, text, meta, distance, vector in sorted(hits, key=lambda hit: (hit[3], hit[0])):
            if meta["product_id"] not in selected:
                raise ValueError("Cross-product document evidence")
            if document_ids is not None and meta["document_id"] not in document_ids:
                raise ValueError("Document outside selected source list")
            # Preserve distinct product relationships; remove repeated contents
            # within a product even if the same paragraph has different IDs.
            identity = (meta["product_id"], compact(text))
            if identity in seen:
                continue
            seen.add(identity)
            rows.append({"record_id": key, "content": text, **meta, "distance": distance})
            vectors.append(vector)
        return rows, vectors, query

    def compare_documents(self, question, selected_product_ids, *, document_top_k=10, fetch_k=30, lambda_mult=0.5, document_ids=None):
        validate_search("mmr", document_top_k, fetch_k, lambda_mult)
        selected = list(dict.fromkeys(selected_product_ids))
        if not selected or set(self.products.get(ids=selected, include=[])["ids"]) != set(selected):
            raise ValueError("Unknown or missing selected product ID")
        rows, vectors, query = self.document_candidates(question, selected, fetch_k, document_ids)
        report = {"fetch_k": fetch_k, "k": document_top_k, "lambda_mult": lambda_mult,
                  "unique_candidates": len(rows), "same_candidate_pool": True, "results": {}}
        for mode in ("similarity", "mmr"):
            indices = select_indices(query, vectors, document_top_k, mode=mode, lambda_mult=lambda_mult) if rows else []
            chosen = [{**rows[i], "search_mode": mode, "candidate_rank": i+1, "selection_rank": rank}
                      for rank, i in enumerate(indices, 1)]
            report["results"][mode] = {"rows": chosen, "metrics": diversity_metrics(chosen, [vectors[i] for i in indices])}
        a = {row["record_id"] for row in report["results"]["similarity"]["rows"]}
        b = {row["record_id"] for row in report["results"]["mmr"]["rows"]}
        report["shared_selected_ids"] = len(a & b)
        report["different_selected_ids"] = len(a ^ b)
        return report

    def retrieve(self, question, selected_product_ids, document_top_k=5, document_ids=None, *,
                 search_mode="similarity", fetch_k=30, lambda_mult=0.5):
        validate_search(search_mode, document_top_k, fetch_k, lambda_mult)
        selected = list(dict.fromkeys(selected_product_ids))
        if not selected:
            raise ValueError("Select at least one product")
        product_data = self.products.get(ids=selected, include=["documents", "metadatas"])
        if set(product_data["ids"]) != set(selected):
            raise ValueError("Unknown selected product ID")
        by_id = {key: {"record_id": key, "content": text, **meta}
                 for key, text, meta in zip(product_data["ids"], product_data["documents"], product_data["metadatas"])}
        evidence = [by_id[key] for key in selected]
        if document_top_k and self.documents.count() and document_ids != []:
            rows, vectors, query = self.document_candidates(question, selected, fetch_k, document_ids)
            indices = select_indices(query, vectors, document_top_k, mode=search_mode, lambda_mult=lambda_mult) if rows else []
            evidence.extend({**rows[i], "search_mode": search_mode, "candidate_rank": i+1, "selection_rank": rank,
                             "candidate_count": len(rows)} for rank, i in enumerate(indices, 1))
        for i, row in enumerate(evidence, 1):
            row["evidence_id"] = i
        return evidence
