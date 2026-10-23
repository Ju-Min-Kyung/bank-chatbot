"""Local indexing configuration. API keys are not needed or read by indexing."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
GMS_BASE_URL = "https://gms.ssafy.io/gmsapi/api.openai.com/v1"


def local_runtime_environment():
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    os.environ["OTEL_SDK_DISABLED"] = "true"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"


@dataclass(frozen=True)
class IndexConfig:
    products: Path = PROJECT_ROOT / "data/processed/products.jsonl"
    preparation_manifest: Path = PROJECT_ROOT / "data/processed/manifest.json"
    external_dir: Path = PROJECT_ROOT / "data/external"
    document_links: Path = PROJECT_ROOT / "data/processed/document_links.jsonl"
    chroma_dir: Path = PROJECT_ROOT / "storage/chroma"
    model_cache: Path = PROJECT_ROOT / "storage/models"
    embedding_cache: Path = PROJECT_ROOT / "storage/embedding_cache.sqlite3"
    model_name: str = DEFAULT_MODEL
    chunk_size: int = 1000
    chunk_overlap: int = 200
    max_tokens: int = 128
    batch_size: int = 128
    threads: int = 1
    workers: int = 8

    def validate(self):
        if self.model_name != DEFAULT_MODEL:
            raise ValueError("Only the verified local multilingual model is supported")
        if self.max_tokens != 128:
            raise ValueError("The verified model uses 128 tokens")
        if not 0 <= self.chunk_overlap < self.chunk_size or self.chunk_size < 1:
            raise ValueError("Invalid chunk size/overlap")
        if self.batch_size < 1 or self.threads < 1 or self.workers < 1:
            raise ValueError("Batch size and threads must be positive")
        protected = (PROJECT_ROOT / "data/raw", self.external_dir, self.products.parent)
        for target in (self.chroma_dir, self.model_cache, self.embedding_cache):
            target = target.resolve()
            for source in protected:
                source = source.resolve()
                if target == source or source in target.parents or target in source.parents:
                    raise ValueError("Index storage overlaps source data")


def load_index_config():
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)
    if os.getenv("EMBEDDING_PROVIDER", "fastembed_local") != "fastembed_local":
        raise ValueError("This indexing pipeline only allows local embedding")
    config = IndexConfig()
    fields = {"CHROMA_DIR": "chroma_dir", "MODEL_CACHE_DIR": "model_cache", "EMBEDDING_CACHE_FILE": "embedding_cache"}
    values = {}
    for key, field in fields.items():
        if os.getenv(key):
            path = Path(os.environ[key])
            values[field] = path if path.is_absolute() else PROJECT_ROOT / path
    for key, field in (("CHUNK_SIZE", "chunk_size"), ("CHUNK_OVERLAP", "chunk_overlap"), ("INDEX_BATCH_SIZE", "batch_size"), ("EMBEDDING_THREADS", "threads"), ("EMBEDDING_WORKERS", "workers")):
        if os.getenv(key):
            values[field] = int(os.environ[key])
    if os.getenv("EMBEDDING_MODEL"):
        values["model_name"] = os.environ["EMBEDDING_MODEL"]
    return replace(config, **values)


@dataclass(frozen=True)
class ChatConfig:
    api_key: str = field(default="", repr=False)
    base_url: str = GMS_BASE_URL
    model: str = "gpt-6-astra"
    timeout: int = 90
    max_output_tokens: int = 2500
    source_transmission_allowed: bool = False
    document_top_k: int = 10
    search_mode: str = "mmr"
    document_fetch_k: int = 30
    mmr_lambda_mult: float = 0.5

    @property
    def endpoint(self):
        value = self.base_url.rstrip("/")
        if value.endswith("/responses"):
            value = value[:-len("/responses")]
        if value != GMS_BASE_URL:
            raise ValueError("GMS_BASE_URL must use the configured SSAFY GMS HTTPS endpoint")
        return value + "/responses"


def load_chat_config():
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    config = ChatConfig(api_key=os.getenv("GMS_API_KEY") or os.getenv("GMS_KEY", ""),
                        base_url=os.getenv("GMS_BASE_URL") or GMS_BASE_URL,
                        model=os.getenv("CHAT_MODEL") or "gpt-6-astra",
                        timeout=int(os.getenv("GMS_TIMEOUT_SECONDS", "90")),
                        max_output_tokens=int(os.getenv("GMS_MAX_OUTPUT_TOKENS", "2500")),
                        source_transmission_allowed=os.getenv("GMS_ALLOW_SOURCE_TRANSMISSION", "false").lower() == "true",
                        document_top_k=int(os.getenv("DOCUMENT_TOP_K", "10")),
                        search_mode=os.getenv("SEARCH_MODE", "mmr"),
                        document_fetch_k=int(os.getenv("DOCUMENT_FETCH_K", "30")),
                        mmr_lambda_mult=float(os.getenv("MMR_LAMBDA_MULT", "0.5")))
    config.endpoint
    if not 1 <= config.document_top_k <= 30 or not 1 <= config.timeout <= 180 or config.max_output_tokens < 128:
        raise ValueError("Invalid GMS timeout/output/document settings")
    from src.rag.mmr import validate_search
    validate_search(config.search_mode, config.document_top_k, config.document_fetch_k, config.mmr_lambda_mult)
    return config

