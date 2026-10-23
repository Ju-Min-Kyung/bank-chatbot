"""Build only the financial_documents collection."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.build_indexes import main

if __name__ == "__main__":
    raise SystemExit(main(["--collection", "documents", *sys.argv[1:]]))
