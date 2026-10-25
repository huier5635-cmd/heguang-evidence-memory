# Metadata-only Vibe image preflight

This checks the immutable prepared public image ID, repository HEAD, original production-file SHA256 values, and available Python/pytest runtime. It never runs tests, installs dependencies, applies model patches, or reads target evaluator columns. The plan contains public solver metadata and base-source file hashes only; no task statement, answer patch, test patch, or user credentials are published.

Only standard ubuntu-24.04 runners on the public repository are permitted. Credentials/permissions are empty, containers have no network or capabilities, and no Actions artifacts are uploaded. This is environment preparation, not an official evaluation or repair result. Full stays disabled.
