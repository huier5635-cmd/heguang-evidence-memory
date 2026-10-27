Explicit before-generation local environment amendment for legacy Woodwork. Same pinned public BASE image, source hashes, original tests/argv, resource limits and owned-container cleanup. Restores only CPython3.9 Hashable alias and overlays verified PyPI pandas1.5.3/numpy1.26.4 cp310 Linux wheels under /tmp using offline no-index/no-deps installation. Applies identically to unpatched BASE and every future arm. Project/test code unchanged. Original12failed and alias-only2failed trials remain reported. This is not a pristine-runtime pass, target repair evidence or official score.


compat3 invalidates import caches after the offline overlay is created and refuses preloaded native dependencies. All sources/tests/argv/images/wheels/resources unchanged. Prior failures retained.
