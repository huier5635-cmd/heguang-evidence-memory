import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    mode: str = "lexical"
    api_key: str = ""
    allow_unauthenticated: bool = False
    embedding_key: str = ""
    embedding_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_key: str = ""
    llm_url: str = "https://api.openai.com/v1"
    llm_provider: str = "openai"
    chunk_chars: int = 1800
    overlap_chars: int = 200
    candidate_limit: int = 400
    max_concurrency: int = 4
    max_body_bytes: int = 64 * 1024 * 1024
    retention_days: int = 29
    context_chars: int = 1600
    bridge_weight: float = 0.15
    window_stride: int = 2
    compact_headers: bool = False

    def __post_init__(self):
        if self.mode not in {"lexical", "hybrid", "research"}:
            raise ValueError("AML_MODE must be lexical, hybrid, or research")
        if self.mode != "lexical" and not self.embedding_key:
            raise ValueError("Hybrid/research mode requires AML_EMBEDDING_KEY")
        if self.mode == "research" and not self.llm_key:
            raise ValueError("Research mode requires AML_LLM_KEY")
        if not self.api_key and not self.allow_unauthenticated:
            raise ValueError("Set AML_API_KEY, or allow unauthenticated LOCAL smoke explicitly")
        if self.chunk_chars < 300 or not 0 <= self.overlap_chars < self.chunk_chars:
            raise ValueError("Invalid chunk size/overlap")
        if self.max_concurrency < 1 or not 1 <= self.retention_days <= 29:
            raise ValueError("Invalid concurrency/retention")
        if self.window_stride not in {2, 4}:
            raise ValueError("Window stride must be 2 or 4")
        for endpoint in (self.embedding_url, self.llm_url):
            if not endpoint.startswith("https://"):
                raise ValueError("Model endpoints must use HTTPS")
        if self.llm_provider not in {"openai", "vercel"}:
            raise ValueError("AML_LLM_PROVIDER must be openai or vercel")
        gateway_url = "https://ai-gateway.vercel.sh/v1"
        if self.llm_provider == "vercel" and self.llm_url.rstrip("/") != gateway_url:
            raise ValueError("Vercel LLM provider requires the official AI Gateway endpoint")
        if self.llm_provider != "vercel" and self.llm_url.rstrip("/") == gateway_url:
            raise ValueError("Set AML_LLM_PROVIDER=vercel for AI Gateway")

    @classmethod
    def from_env(cls):
        llm_provider = os.getenv("AML_LLM_PROVIDER", "openai")
        default_llm_url = "https://ai-gateway.vercel.sh/v1" if llm_provider == "vercel" else cls.llm_url
        return cls(
            data_dir=Path(os.getenv("AML_DATA_DIR", "data")),
            mode=os.getenv("AML_MODE", "lexical"),
            api_key=os.getenv("AML_API_KEY", ""),
            allow_unauthenticated=os.getenv("AML_ALLOW_UNAUTHENTICATED") == "1",
            embedding_key=os.getenv("AML_EMBEDDING_KEY", ""),
            embedding_url=os.getenv("AML_EMBEDDING_URL", cls.embedding_url),
            llm_key=os.getenv("AML_LLM_KEY", ""),
            llm_url=os.getenv("AML_LLM_URL", default_llm_url),
            llm_provider=llm_provider,
            chunk_chars=int(os.getenv("AML_CHUNK_CHARS", "1800")),
            overlap_chars=int(os.getenv("AML_OVERLAP_CHARS", "200")),
            max_concurrency=int(os.getenv("AML_MAX_CONCURRENCY", "4")),
            context_chars=int(os.getenv("AML_CONTEXT_CHARS", "1600")),
            bridge_weight=float(os.getenv("AML_BRIDGE_WEIGHT", "0.15")),
            window_stride=int(os.getenv("AML_WINDOW_STRIDE", "2")),
            compact_headers=os.getenv("AML_COMPACT_HEADERS", "0") == "1",
        )

