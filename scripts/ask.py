"""Ask the local two-collection RAG through GMS; selected IDs are repeatable."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.rag.runtime import ChatRuntime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--product-id", action="append", default=[])
    args = parser.parse_args()
    runtime = None
    try:
        runtime = ChatRuntime()
        result = runtime.service.ask(args.question, args.product_id)
        print(result["answer"])
        print(json.dumps({"status": result["status"], "warnings": result.get("warnings", []), "timings": result.get("timings", {}),
            "sources": [{k: row[k] for k in ("evidence_id", "product_id", "title", "source_uri", "source_file", "page_number") if k in row} for row in result["cited_evidence"]]}, ensure_ascii=False, indent=2))
        return 1 if result["status"] == "api_error" else 0
    except (ValueError, OSError, KeyError):
        print("설정·자료·색인을 읽을 수 없습니다. README의 준비 절차를 확인하세요.")
        return 1
    finally:
        if runtime:
            runtime.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
