"""Build local Chroma product/document collections; financial text stays offline."""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import load_index_config
from scripts.audit_data import file_hash
from scripts.prepare_data import atomic_text
from src.ingestion.embeddings import LocalEmbedder
from src.ingestion.product_index import load_product_records, build_product_index
from src.ingestion.document_index import load_document_records, build_document_index
from src.ingestion.vector_store import persistent_client


@contextmanager
def offline_network():
    """Deny Python network access during loading, indexing and local queries."""
    def denied(*args, **kwargs):
        raise RuntimeError("Network access is disabled during local indexing")
    originals = [(socket, "create_connection", socket.create_connection), (socket, "getaddrinfo", socket.getaddrinfo),
                 (socket.socket, "connect", socket.socket.connect), (socket.socket, "connect_ex", socket.socket.connect_ex)]
    try:
        for owner, name, _ in originals:
            setattr(owner, name, denied)
        yield
    finally:
        for owner, name, original in originals:
            setattr(owner, name, original)


def build(config, collection="all"):
    config.validate()
    started = time.perf_counter()
    config.chroma_dir.mkdir(parents=True, exist_ok=True)
    status_path = config.chroma_dir / "build_status.json"
    status = {"status": "building", "started_at": datetime.now(timezone.utc).isoformat()}
    atomic_text(status_path, json.dumps(status) + "\n")
    embedder = None
    client = None
    try:
        with offline_network():
            source_paths = {"products_sha256": config.products, "preparation_manifest_sha256": config.preparation_manifest,
                            "document_manifest_sha256": config.external_dir / "manifest.json", "document_links_sha256": config.document_links}
            source_before = {key: file_hash(path) for key, path in source_paths.items()}
            embedder = LocalEmbedder(config)
            products, product_records = load_product_records(config)
            document_records, document_stats = (None, None)
            if collection in {"all", "documents"}:
                print("Validating PDF pages and preparing token-bounded chunks...", flush=True)
                document_records, document_stats = load_document_records(config, embedder, products)
                print(json.dumps({"document_preparation": document_stats}), flush=True)
            client = persistent_client(config.chroma_dir)
            path = config.chroma_dir / "index_manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"collections": {}}
            if manifest.get("embedding_signature", embedder.signature) != embedder.signature:
                raise ValueError("Model/settings changed; use a new CHROMA_DIR")

            def progress(name):
                last = [0.0]
                def update(done, total, changed):
                    current = time.perf_counter()
                    if done == total or current - last[0] > 10:
                        print(f"{name}: {done}/{total}, upserted={changed}, elapsed={current-started:.1f}s", flush=True)
                        last[0] = current
                return update

            if collection in {"all", "products"}:
                result = build_product_index(client, embedder, product_records, progress("financial_products"))
                manifest["collections"]["financial_products"] = result
            if collection in {"all", "documents"}:
                result = build_document_index(client, embedder, document_records, progress("financial_documents"))
                manifest["collections"]["financial_documents"] = result
                manifest["document_preparation"] = document_stats
            if source_before != {key: file_hash(path) for key, path in source_paths.items()}:
                raise ValueError("Source inputs changed while indexing")
            manifest.update({"schema_version": 1, "status": "ready", "built_at": datetime.now(timezone.utc).isoformat(),
                             "embedding_provider": "fastembed_local_onnx_cpu", "embedding_model": config.model_name,
                             "embedding_signature": embedder.signature, "model_revision": embedder.manifest["revision"],
                             "model_fingerprint": embedder.manifest["fingerprint"], "model_license": embedder.manifest["license"],
                             "embedding_dimension": embedder.dimension, "max_tokens": config.max_tokens,
                             "chunk_size_chars": config.chunk_size, "requested_overlap_chars": config.chunk_overlap,
                             "overlap_policy": "min(requested_overlap,actual_chunk_length//5)",
                             "long_product_embedding": "mean of all 128-token-bounded window vectors then L2 normalization",
                             "embedding_stats_this_run": dict(embedder.stats), "elapsed_seconds": round(time.perf_counter()-started, 3),
                             "cpu_threads_per_worker": config.threads, "cpu_workers": config.workers,
                             "sources": source_before,
                             "privacy": {"python_network_blocked": True, "telemetry_disabled": True,
                                         "external_embedding_api_calls": 0, "labels_and_cot_indexed": False}})
            atomic_text(path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            atomic_text(status_path, json.dumps({**status, "status": "ready", "finished_at": manifest["built_at"]}) + "\n")
            print(json.dumps({"collections": manifest["collections"], "embedding_stats": dict(embedder.stats), "elapsed_seconds": manifest["elapsed_seconds"]}), flush=True)
            return manifest
    except Exception as exc:
        atomic_text(status_path, json.dumps({**status, "status": "failed", "error_type": type(exc).__name__}) + "\n")
        raise
    finally:
        if client is not None:
            client.close()
        if embedder:
            embedder.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", choices=("all", "products", "documents"), default="all")
    parser.add_argument("--chroma-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        config = load_index_config()
        if args.chroma_dir:
            config = replace(config, chroma_dir=args.chroma_dir.resolve())
        build(config, args.collection)
    except Exception as exc:
        print(f"Index build failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
