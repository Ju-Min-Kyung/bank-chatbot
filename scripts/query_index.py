"""Search the local vector DB; prints source metadata, not generated advice."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import load_index_config, load_chat_config
from scripts.build_indexes import offline_network
from src.ingestion.embeddings import LocalEmbedder
from src.rag.retriever import VectorRetriever


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--product-id", action="append", default=[])
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--mode", choices=["similarity", "mmr"])
    parser.add_argument("--fetch-k", type=int, default=30)
    parser.add_argument("--lambda-mult", type=float, default=0.5)
    args = parser.parse_args()
    embedder = None
    retriever = None
    try:
        with offline_network():
            config = load_index_config()
            embedder = LocalEmbedder(config)
            retriever = VectorRetriever(config, embedder)
            rows = retriever.retrieve(args.question, args.product_id, args.top_k, search_mode=args.mode or load_chat_config().search_mode,
                fetch_k=args.fetch_k, lambda_mult=args.lambda_mult) if args.product_id else retriever.search_products(args.question, args.top_k)
            # Source text stays in the DB; terminal output is a compact source list.
            print(json.dumps([{key: row[key] for key in ("evidence_id", "product_id", "product_name", "source_type", "title", "page_number", "source_uri", "distance", "match_type", "search_mode", "candidate_rank", "selection_rank", "candidate_count") if key in row} for row in rows], ensure_ascii=False, indent=2))
    finally:
        if retriever:
            retriever.client.close()
        if embedder:
            embedder.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
