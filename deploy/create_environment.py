"""Create a new protected Coding-only environment. Run explicitly as Linux root."""
import os
import secrets
import sys
from pathlib import Path


def create():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise SystemExit("Linux root required; no files changed")
    target = Path("/etc/heguang-coding.env")
    # Exclusive creation: never replace an existing service's configuration/key.
    body = "\n".join([
        "HCM_API_KEY=" + secrets.token_urlsafe(48),
        "HCM_DATA_DIR=/var/lib/heguang-coding",
        "HCM_PROFILE=rarity",
        "HCM_SESSION_PENALTY=0",
        "HCM_RERANKER_DIR=/opt/heguang-coding/model",
        "HCM_RERANKER_WEIGHT=4",
        "HCM_RERANK_RESPONSE_CHARS=128000",
        "HCM_MAX_CONCURRENCY=1",
        "HCM_LARGE_BODY_BYTES=16777216", ""])
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as destination:
        destination.write(body)
    print("Created Coding-only environment with mode 0600; credential value not printed")


if __name__ == "__main__":
    create()
