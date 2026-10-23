"""Offline ONNX embeddings, complete token windows and content-addressed cache."""
from __future__ import annotations

import json
import sqlite3
from threading import RLock
from collections import Counter
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

from config import IndexConfig, local_runtime_environment
from scripts.audit_data import canonical_hash, file_hash, sha256

_WORKER_MODEL = None


def _worker_initialize(model_name, model_cache, snapshot, threads):
    import os
    import socket
    os.environ["HF_HUB_OFFLINE"] = "1"
    local_runtime_environment()
    from fastembed import TextEmbedding
    from onnxruntime import disable_telemetry_events
    disable_telemetry_events()
    def denied(*args, **kwargs):
        raise RuntimeError("Embedding worker network access disabled")
    socket.create_connection = denied
    socket.getaddrinfo = denied
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    global _WORKER_MODEL
    _WORKER_MODEL = TextEmbedding(model_name=model_name, cache_dir=model_cache, specific_model_path=snapshot,
                                  local_files_only=True, threads=threads, providers=["CPUExecutionProvider"])


def _worker_embed(windows):
    return list(_WORKER_MODEL.embed(windows, batch_size=32))


def text_chunks(text, tokenizer, max_tokens=128, chunk_size=1000, chunk_overlap=200):
    """Exact character slices bounded by both character count and model tokens.

    Overlap is capped at 20% of each actual chunk, so a short Korean token
    window cannot accidentally advance one character at a time.
    """
    if not 0 <= chunk_overlap < chunk_size or max_tokens < 4:
        raise ValueError("Invalid chunk settings")
    special = tokenizer.num_special_tokens_to_add(False)
    budget = max_tokens - special
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        encoded = tokenizer.encode(text[start:end], add_special_tokens=False)
        if len(encoded.ids) > budget:
            end = start + encoded.offsets[budget - 1][1]
            while end > start and len(tokenizer.encode(text[start:end], add_special_tokens=False).ids) > budget:
                end -= 1
        if end <= start:
            raise ValueError("Tokenizer did not advance")
        content = text[start:end]
        tokens = len(tokenizer.encode(content, add_special_tokens=False).ids) + special
        if tokens > max_tokens:
            raise ValueError("Token window exceeded model limit")
        if content.strip():
            yield {"text": content, "char_start": start, "char_end": end, "token_count": tokens}
        if end == len(text):
            break
        overlap = min(chunk_overlap, (end - start) // 5)
        start = end - overlap


class LocalEmbedder:
    def __init__(self, config: IndexConfig):
        import os
        local_runtime_environment()
        os.environ["HF_HUB_OFFLINE"] = "1"
        from fastembed import TextEmbedding
        from onnxruntime import disable_telemetry_events
        from tokenizers import Tokenizer
        import numpy as np
        disable_telemetry_events()
        config.validate()
        self.config, self.np = config, np
        manifest_file = config.model_cache / "model_manifest.json"
        if not manifest_file.is_file():
            raise ValueError("Download the local embedding model first")
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        snapshot = Path(manifest["snapshot_dir"]).resolve()
        if config.model_cache.resolve() not in snapshot.parents:
            raise ValueError("Model snapshot must be inside the project model cache")
        if manifest["model_name"] != config.model_name or canonical_hash(manifest["files"]) != manifest["fingerprint"]:
            raise ValueError("Model manifest mismatch")
        for name, expected in manifest["files"].items():
            if file_hash(snapshot / name) != expected:
                raise ValueError("Model file checksum mismatch")
        self.manifest = manifest
        self.snapshot = snapshot
        self.pool = None
        self.dimension = manifest["dimension"]
        self.signature = canonical_hash({"model_name": config.model_name, "fingerprint": manifest["fingerprint"],
                                         "max_tokens": config.max_tokens, "aggregation": "mean_all_windows_l2_v1",
                                         "window_chars": config.chunk_size, "overlap": config.chunk_overlap})
        self.model = TextEmbedding(model_name=config.model_name, cache_dir=str(config.model_cache),
                                   specific_model_path=str(snapshot), local_files_only=True,
                                   threads=config.threads, providers=["CPUExecutionProvider"])
        # An independent, non-truncating tokenizer is used for boundary checks.
        self.tokenizer = Tokenizer.from_file(str(snapshot / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.config.embedding_cache.parent.mkdir(parents=True, exist_ok=True)
        # Streamlit reruns a session on a new script thread; serialize its use.
        self.cache = sqlite3.connect(config.embedding_cache, check_same_thread=False, timeout=30)
        self.cache_lock = RLock()
        self.cache.execute("CREATE TABLE IF NOT EXISTS vectors (signature TEXT, kind TEXT, hash TEXT, vector BLOB, PRIMARY KEY(signature,kind,hash))")
        self.stats = Counter()

    def get_cached(self, kind, text):
        row = self.cache.execute("SELECT vector FROM vectors WHERE signature=? AND kind=? AND hash=?",
                                 (self.signature, kind, sha256(text.encode("utf-8")))).fetchone()
        if row is None:
            return None
        vector = self.np.frombuffer(row[0], dtype="<f4").copy()
        if vector.shape != (self.dimension,) or not self.np.isfinite(vector).all():
            raise ValueError("Invalid cached embedding")
        return vector

    def put_cached(self, kind, text, vector):
        self.cache.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?,?)",
                           (self.signature, kind, sha256(text.encode("utf-8")), vector.astype("<f4").tobytes()))

    def embed(self, texts):
        with self.cache_lock:
            return self._embed(texts)

    def _embed(self, texts):
        np = self.np
        texts = list(texts)
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Cannot embed empty/non-text input")
        results = [self.get_cached("text", text) for text in texts]
        missing = {text: [chunk["text"] for chunk in text_chunks(text, self.tokenizer, self.config.max_tokens,
                                                               self.config.chunk_size, self.config.chunk_overlap)]
                   for text, vector in zip(texts, results) if vector is None}
        windows = dict.fromkeys(window for parts in missing.values() for window in parts)
        window_vectors, uncached = {}, []
        for window in windows:
            vector = self.get_cached("window", window)
            if vector is None:
                uncached.append(window)
            else:
                window_vectors[window] = vector
                self.stats["window_cache_hits"] += 1
        if len(uncached) >= 8 and self.config.workers > 1:
            if self.pool is None:
                self.pool = ProcessPoolExecutor(max_workers=self.config.workers, initializer=_worker_initialize,
                                                initargs=(self.config.model_name, str(self.config.model_cache), str(self.snapshot), self.config.threads))
            width = max(1, min(32, (len(uncached) + self.config.workers - 1) // self.config.workers))
            batches = [uncached[i:i + width] for i in range(0, len(uncached), width)]
            vectors = (vector for batch in self.pool.map(_worker_embed, batches) for vector in batch)
        else:
            vectors = self.model.embed(uncached, batch_size=32)
        for window, vector in zip(uncached, vectors):
            vector = np.asarray(vector, dtype=np.float32)
            if vector.shape != (self.dimension,) or not np.isfinite(vector).all() or np.linalg.norm(vector) == 0:
                raise ValueError("Invalid model embedding")
            vector = vector / np.linalg.norm(vector)
            window_vectors[window] = vector
            self.put_cached("window", window, vector)
            self.stats["windows_inferred"] += 1
        if any(window not in window_vectors for window in windows):
            raise ValueError("Embedding model did not return every requested vector")
        for text, parts in missing.items():
            # All windows contribute; long search text is never silently truncated.
            vector = np.mean([window_vectors[window] for window in parts], axis=0).astype(np.float32)
            norm = np.linalg.norm(vector)
            if not norm or not np.isfinite(norm):
                raise ValueError("Invalid aggregated embedding")
            vector /= norm
            self.put_cached("text", text, vector)
            self.stats["texts_embedded"] += 1
            self.stats["text_windows"] += len(parts)
            self.stats["multi_window_texts"] += int(len(parts) > 1)
        for i, text in enumerate(texts):
            if results[i] is None:
                results[i] = self.get_cached("text", text)
            else:
                self.stats["text_cache_hits"] += 1
        self.cache.commit()
        return [vector.tolist() for vector in results]

    def close(self):
        if self.pool is not None:
            self.pool.shutdown(wait=True)
        self.cache.close()
