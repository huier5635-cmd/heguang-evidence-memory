# Qianora dev17 bounded Linux replay

This is one public-history ingestion and model lifecycle test. It does not run
target tests, an agent repair task, an official Smoke, or Full. It uses a
synthetic local credential and a standard free public GitHub runner.

The parent verifies and sends all 87 messages from the fixed 81 MB historical
trajectory. Reference parsing and audit verification occur outside the service
budget. Only the application runs in the constrained container: one CPU,
memory.high 320 MiB, memory.max 384 MiB, memory.swap.max 512 MiB, 64 processes,
and a 300-second service deadline. The harness reads the actual cgroup limits
and OOM counters before and after the request; a missing or different limit
fails the test. Limits are never raised after a failure.

The test warms the actual pinned MiniLM, performs the large Add, and requires
the same original search result after model reload. It independently verifies
the full largest original audit, all 87 sources, a bounded searchable visible
observation, database integrity, unchanged inputs, and service cleanup.

An online 64 MiB limit is not changed here. The candidate uses 128 MiB and a
fresh database. Even success is only Linux capacity evidence; protected cloud
deployment, independent repair benefit, and a new official Smoke remain
separate requirements. Full stays disabled.

## Diagnostic-only amendment

Original run 40391356164/job121812860988 failed before the application became ready. The original failure is retained. This version additionally records Docker versions/cgroup mode and the bounded stderr tail on failure. Candidate source, native input, model, memory/CPU/swap/process/deadline bounds are unchanged. The base Python image is pinned to the exact first-run digest. No target tests are opened.

## Docker 28 cgroup configuration amendment

Diagnostic run40392183896/job121815742699 proved Docker28.0.4 rejected --cgroup-conf before creating a container. This version keeps every hard limit from docker run, then uses a strictly scoped host helper to set memory.high on only this container before warming the model or sending Add. It first requires the startup memory peak to be below320MiB and verifies384MiB memory.max,512MiB memory.swap.max. The helper does not alter other groups or raise limits. All original failures remain failures. Kernel semantics: https://docs.kernel.org/admin-guide/cgroup-v2.html ; Docker total memory+swap semantics: https://docs.docker.com/engine/containers/resource_constraints/ .

## Observed assembly replay

This supplemental run adds the external observed_api.py counter layer. It keeps all17 frozen core modules, the native input, the model and every resource limit unchanged. Four real request records must contain only allowed counters and report validated Add counts1 and87 and two nonempty searches. It is not a new repair-generation trial or an official evaluation.

The frozen dev17 application adds an independent historical-problem index. This new replay additionally checks that a real warm-user problem is indexed transactionally and that the reloaded Search response is byte-identical. Same original81MB public history, model and cgroup limits; no repair score, target tests, production change or Full submission.

Before the first execution, the prospective protocol was extended to16 concurrent streamed Add calls in16 separate users, reusing the same complete81MB original messages array. The parent shares one immutable buffer and emits64KiB chunks. Every user must retain87messages and its full original canonical audit, with integrity/FK checks, followed by byte-identical warm Search. The same300second service deadline and320/384/512MiB high/max/swap/CPU1 limits remain. This is a bounded stress case, not proof of the entire official300execution workload.

Supplemental environment-only trial after run40440874698 failed with a150s client write timeout: same frozen app,16same complete81MB histories,320/384/512MiB and CPU1/pids64. Only the client wait is285s, below the unchanged300s service wall guard and official30minute request bound (https://agentmemoryleaderboard.ai/api-guide). Add a read-only host cgroup snapshot before cleanup so RAM/swap/OOM counters survive a failure. Never claim the original150s trial passed.
