import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data/local-coding")
    api_key: str = ""
    profile: str = "rarity"
    chunk_chars: int = 3600
    overlap_chars: int = 360
    block_chars: int = 8192
    candidates: int = 300
    response_chars: int = 32000
    session_penalty: float = 0.0
    reranker_dir: Path | None = None
    reranker_weight: float = 4.0
    rerank_response_chars: int = 128000
    max_concurrency: int = 2
    max_body_bytes: int = 64 * 1024 * 1024
    large_body_bytes: int = 16 * 1024 * 1024
    retention_days: int = 29

    def __post_init__(self):
        if not self.api_key:
            raise ValueError("Set HCM_API_KEY")
        if self.profile not in {"baseline", "code", "rarity"}:
            raise ValueError("Unknown retrieval profile")
        if not 512 <= self.chunk_chars <= 16000 or not 0 <= self.overlap_chars < self.chunk_chars:
            raise ValueError("Invalid source chunk configuration")
        if not 1024 <= self.block_chars <= 32768:
            raise ValueError("Invalid compressed block size")
        if not 10 <= self.candidates <= 1000 or self.response_chars < self.chunk_chars + 1024:
            raise ValueError("Invalid retrieval budget")
        if not 0 <= self.session_penalty <= 2:
            raise ValueError("Invalid session diversity penalty")
        if not .25 <= self.reranker_weight <= 16 or not self.chunk_chars + 1024 <= self.rerank_response_chars <= 512000:
            raise ValueError("Invalid reranker weight/evidence budget")
        if not 1 <= self.retention_days <= 29 or not 1 <= self.max_concurrency <= 8:
            raise ValueError("Invalid retention/concurrency")
        if not 1024 <= self.large_body_bytes <= 64 * 1024 * 1024:
            raise ValueError("Invalid large-request admission threshold")

    @classmethod
    def from_env(cls):
        return cls(data_dir=Path(os.getenv("HCM_DATA_DIR", "data/local-coding")),
                   api_key=os.getenv("HCM_API_KEY", ""),
                   profile=os.getenv("HCM_PROFILE", "rarity"),
                   session_penalty=float(os.getenv("HCM_SESSION_PENALTY", "0")),
                   reranker_dir=Path(os.environ["HCM_RERANKER_DIR"]) if os.getenv("HCM_RERANKER_DIR") else None,
                   reranker_weight=float(os.getenv("HCM_RERANKER_WEIGHT", "4")),
                   rerank_response_chars=int(os.getenv("HCM_RERANK_RESPONSE_CHARS", "128000")),
                   max_concurrency=int(os.getenv("HCM_MAX_CONCURRENCY", "2")),
                   large_body_bytes=int(os.getenv("HCM_LARGE_BODY_BYTES", "16777216")))
