"""Download public ONNX model weights only; never reads financial input data."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import DEFAULT_MODEL, IndexConfig, local_runtime_environment
from scripts.audit_data import file_hash, canonical_hash


def main():
    local_runtime_environment()
    from fastembed import TextEmbedding
    from onnxruntime import disable_telemetry_events
    disable_telemetry_events()
    config = IndexConfig()
    config.validate()
    config.model_cache.mkdir(parents=True, exist_ok=True)
    model = TextEmbedding(model_name=DEFAULT_MODEL, cache_dir=str(config.model_cache),
                          threads=config.threads, providers=["CPUExecutionProvider"])
    snapshot = Path(model.model._model_dir)
    names = ["config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "model_optimized.onnx"]
    files = {name: file_hash(snapshot / name) for name in names if (snapshot / name).is_file()}
    if "model_optimized.onnx" not in files or "tokenizer.json" not in files:
        raise ValueError("Incomplete model download")
    entry = next(item for item in model.list_supported_models() if item["model"] == DEFAULT_MODEL)
    manifest = {"model_name": DEFAULT_MODEL, "source_repo": entry["sources"]["hf"],
                "license": entry["license"], "revision": snapshot.name,
                "snapshot_dir": str(snapshot.resolve()), "files": files, "fingerprint": canonical_hash(files),
                "dimension": model.embedding_size, "max_tokens": 128,
                "downloaded_at": datetime.now(timezone.utc).isoformat(), "financial_input_read": False}
    (config.model_cache / "model_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key:manifest[key] for key in ("model_name", "source_repo", "revision", "license", "dimension", "fingerprint")}), flush=True)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
