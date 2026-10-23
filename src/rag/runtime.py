"""Create local retrieval resources; no model call until a question is submitted."""
import os
from dataclasses import replace

from config import load_chat_config, load_index_config
from scripts.build_indexes import offline_network
from src.ingestion.embeddings import LocalEmbedder
from src.ingestion.product_index import load_product_records
from src.ingestion.document_fetcher import DocumentCatalog
from src.rag.gms import GMSClient
from src.rag.graph import RAGService
from src.rag.retriever import VectorRetriever


class ChatRuntime:
    def __init__(self):
        # Never trace questions/source passages to third-party telemetry.
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ["LANGSMITH_TRACING"] = "false"
        # Querying short questions does not need eight additional ONNX processes.
        self.config = replace(load_index_config(), workers=1)
        self.chat_config = load_chat_config()
        self.embedder = None
        self.retriever = None
        try:
            self.products, _ = load_product_records(self.config)
            with offline_network():
                self.embedder = LocalEmbedder(self.config)
                self.retriever = VectorRetriever(self.config, self.embedder)
            self.catalog = DocumentCatalog(self.config)
            self.client = GMSClient(self.chat_config)
            self.service = RAGService(self.products, self.retriever, self.catalog, self.client, self.chat_config.document_top_k,
                search_mode=self.chat_config.search_mode, fetch_k=self.chat_config.document_fetch_k, lambda_mult=self.chat_config.mmr_lambda_mult)
        except Exception:
            self.close()
            raise

    def close(self):
        if self.retriever:
            self.retriever.client.close()
        if self.embedder:
            self.embedder.close()
