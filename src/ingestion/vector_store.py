"""Persistent Chroma with explicit embeddings and managed, repeatable upserts."""
from __future__ import annotations

from config import local_runtime_environment
from scripts.audit_data import canonical_hash


def persistent_client(path):
    local_runtime_environment()
    import chromadb
    from chromadb.config import Settings
    return chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))


def managed_collection(client, name, embedder):
    metadata = {"index_owner": "bank-chatbot-v1", "embedding_signature": embedder.signature,
                "embedding_model": embedder.config.model_name, "embedding_dimension": embedder.dimension}
    collection = client.get_or_create_collection(name, embedding_function=None, metadata=metadata,
                                                  configuration={"hnsw": {"space": "cosine"}})
    if any((collection.metadata or {}).get(key) != value for key, value in metadata.items()):
        raise ValueError("Collection model/schema mismatch; choose a new CHROMA_DIR")
    return collection


def index_records(collection, records, embedder, batch_size=128, progress=None):
    ids = [record["id"] for record in records]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate input IDs")
    for record in records:
        if not record["text"].strip():
            raise ValueError("Empty search text")
        if any(not isinstance(v, (str, bool, int, float)) for v in record["metadata"].values()):
            raise ValueError("Non-scalar Chroma metadata")
    before = collection.count()
    upserted = unchanged = 0
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        stored = collection.get(ids=[row["id"] for row in batch], include=["metadatas"])
        existing = {key: meta.get("index_record_hash") for key, meta in zip(stored["ids"], stored["metadatas"])}
        changed, hashes = [], []
        for row in batch:
            fingerprint = canonical_hash({"text": row["text"], "metadata": row["metadata"], "embedding_signature": embedder.signature})
            if existing.get(row["id"]) == fingerprint:
                unchanged += 1
            else:
                changed.append(row)
                hashes.append(fingerprint)
        if changed:
            vectors = embedder.embed([row["text"] for row in changed])
            collection.upsert(ids=[row["id"] for row in changed], embeddings=vectors,
                              documents=[row["text"] for row in changed],
                              metadatas=[{**row["metadata"], "index_record_hash": fingerprint} for row, fingerprint in zip(changed, hashes)])
            upserted += len(changed)
        if progress:
            progress(min(start + batch_size, len(records)), len(records), upserted)
    # Reconcile only this managed collection, after the entire input succeeded.
    stored_ids = set(collection.get(include=[])["ids"])
    stale = sorted(stored_ids - set(ids))
    for start in range(0, len(stale), batch_size):
        collection.delete(ids=stale[start:start + batch_size])
    after = collection.count()
    if after != len(ids):
        raise ValueError("Stored collection count differs from input")
    return {"before": before, "after": after, "upserted": upserted, "unchanged": unchanged,
            "stale_removed": len(stale), "ids_sha256": canonical_hash({"ids": sorted(ids)})}
