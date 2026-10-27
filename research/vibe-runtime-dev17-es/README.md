# Qianora dev17 public BASE-environment preflight

This job checks fixed upstream repository sources and the original BASE tests
before model generation. It does not load VibeMemBench test_patch or solution
patch columns. Existing image tests are overwritten from the fixed BASE git
object before execution; unknown Python test files block execution. Containers
have no network, no repository credentials and fixed CPU/RAM/process limits.

VibeMemBench is external public research data from Tongyi-ConvAI, not the
official CAMBench competition score. Upstream code remains the work of
ipython/traitlets, elastic/curator, and python-attrs/attrs and their contributors.
Image and source identities, original native commands, and no-oracle provenance
are explicit in plan.json. No generated candidate patch is executed here.

Failures remain in the record. A green job alone does not prove repair benefit,
deployment readiness, official Smoke success, Full readiness or an award.

An explicit pre-generation dependency amendment adds Elasticsearch6.7.1, a
version listed in the fixed upstream BASE CI matrix. The vendor image is pinned
by manifest and config digest. It has no external network or published host
ports; the Python test container shares only its isolated loopback namespace.
No original test command, source bytes, test selection or Python package is
changed. All future Curator baseline/candidate arms require this same service.
The original missing-service failure and both other repositories' successful
BASE preflights remain preserved, not replaced by this dependency-only run.
