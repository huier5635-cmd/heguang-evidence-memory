# dev16 deployment preparation

The17 frozen application modules are unchanged. observed_api.py adds only bounded request/response counters outside the application: no queries, identities, source text, credentials or exception text. Validated Add message counts propagate through the existing thread operation; successful Search counts are parsed only after the original response has been sent, under a1MiB capture bound. Journal errors never replace HTTP results.

Fifteen checks cover exact HTTP response and persistent database equality, real Add/Search, authentication/validation/conflict status, concurrent per-request thread context isolation, journal failure, original ASGI frames and transport errors, bounded capture, exact JSON-array replay and rejected ambiguous sources. The81MB public historical file was streamed in64KiB pieces without a full Unicode JSON copy.

stage_runner.py is loopback-only port8753. It validates the manifest and frozen core, then explicitly replaces data and configuration in Python, because an EnvironmentFile could otherwise override a systemd Environment setting and select the old data directory. The stage data directory is unique to this runtime manifest. It uses a separate dependency environment and keeps the existing protected credential on the server.

server_probe.py sends the complete original messages array with Content-Length, checks full audit integrity and source counts, model reload, auth/error counters and persistence after restart. It saves resource counters before restart so the large request peak cannot be erased by restarting the unit. No official endpoint or Full call exists in these scripts.

Actual staging must wait for independent native repair benefit. These files are preparation, not evidence of deployment, Full-scale capacity, or a higher official score. The protected download/install/unit/rollback orchestration remains to be finalized before execution. Never reuse the production data directory with this source_view6 index.
