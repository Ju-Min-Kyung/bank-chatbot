"""Check GMS with a synthetic structured response; no financial source data."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import load_chat_config
from src.rag.gms import GMSClient, ModelError


def main():
    config = load_chat_config()
    client = GMSClient(config)
    schema = {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"], "additionalProperties": False}
    started = time.perf_counter()
    try:
        result = client.structured("Return JSON with message set to GMS_OK.", {"input": "Hello, World!"}, schema, "connection_check", source_data=False)
        if result["message"] != "GMS_OK":
            raise ModelError("GMS 연결 응답의 값이 일치하지 않습니다.")
    except (ModelError, ValueError) as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False))
        return 1
    evidence = {"status": "passed", "endpoint": config.endpoint, "model": config.model, "request_type": "synthetic_only",
                "structured_output": True, "store": False, "latency_ms": round((time.perf_counter()-started)*1000, 2), "usage": client.usage}
    (ROOT / "docs/evidence/gms_connection.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
