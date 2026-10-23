# Qianora Coding source scope

This branch is the 0.2.0.dev5 single-core runtime candidate, based on immutable
dev4 commit 978f2ab2bea5ec9f55ce06ce23a31d3e0df4901d. The frozen Textual main
branch and older Coding refs remain unchanged. Dev4 is deployed; dev5 deployment
and API timing are pending at this source freeze. No independent Coding application,
official Smoke, Full run, official score, or prize is claimed.

Only the configurable one/two-thread runtime changes model execution. Pinned model,
input windows, RRF weight, candidate limits, source evidence, storage codec and
index signature stay unchanged. Local single-thread construction matches the old
scores on 10 already-used public/synthetic cases; Linux synthetic quota timing is
about twice as fast. This is not an independent quality or task-resolution result.

Old reports describe their original frozen versions. Historical source snapshots,
raw public runtime fixtures, full tuning reports and binary artifacts remain local.
Reproduction tools acquire the pinned public data separately. No credentials,
private benchmark data, databases, request logs, model weights or test-oracle
patches are published. Application and Smoke are authorized; Full is prohibited.
