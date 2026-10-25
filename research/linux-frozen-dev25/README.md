# Frozen public repair validation on Linux

This directory evaluates the seven already reviewed, frozen public repair proposals and preserves two invalid proposal records. It never generates new proposals, reads target solution patches, modifies the deployed service, or submits an official evaluation.

The workflow runs only on coding-linux-frozen-dev25 in a public repository using standard ubuntu-24.04 runners. Permissions are empty. It uses no account, server, or model secrets and uploads no Actions artifacts. Full remains disabled. GitHub documents standard public-repository runners as free: https://docs.github.com/en/billing/concepts/product-billing/github-actions

Each case resolves the upstream documented starryzhang instance image once, records its immutable image ID and repository digest, and uses that image for all arms. Before execution it checks the frozen base commit and original source digests, then applies only the reviewed proposal and public test patch. Each arm uses a separate container with no network, all capabilities dropped, no privilege escalation, 2 CPUs, 6 GB memory, a 256-process limit, and a 900-second execution limit.

The original complete pytest -rA suite is run. Concrete test node IDs and all phases are retained. Whitespace-colliding summary identities never overwrite members; missing evidence, collection errors, regressions, and environment failures remain explicit. Prior Windows receipts are preserved separately. This development protocol does not claim an official score or repair benefit before actual results are inspected.

Full compressed result evidence and SHA256 are written to job logs in ordered QIANORA_EVIDENCE chunks; no artifact storage is required. Raw model outputs remain frozen. Public test and source differences retain the respective upstream project licenses: pylint-dev/pylint, sphinx-doc/sphinx, and pydata/xarray. Upstream image documentation: https://github.com/microsoft/SWE-bench-Live/blob/ad79b850f15e33992e96f03f6e97f05ddf9aa0be/README.md
