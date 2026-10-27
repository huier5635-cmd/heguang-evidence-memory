# Prospective dev18 common-solver BASE preflight

Three repositories were chosen using the pre-existing frozen SHA order before reading their issues. This preflight verifies1782production-file digests against pinned BASE source and immutable image IDs, restores only original tracked BASE tests, and uses the exact dataset-declared test commands. No target test patch, FAIL_TO_PASS/PASS_TO_PASS fields or solution patch is loaded.

The Docker runtime has no network or host ports, no capabilities, no privilege escalation,2CPU/3GiB/pids128. The newly created container ID is owned by this host script and always removed, including on timeout. The fixed repository working directory comes from the pinned image. Only BASE-test diagnostics and resource/identity metadata are printed. Passing this preflight is not a repair score or proof of memory benefit.

The candidate remains the unchanged dev17 memory implementation. A new common local solver protocol adds one bounded source-based repair-plan call and uses existing one-feedback allowance for source warnings. It must apply equally to every future arm; prior dev17 static-review rejection is retained. The separate synthetic protocol canary passed but explicitly included the intended toy fix and is not benchmark evidence. Full stays closed.
