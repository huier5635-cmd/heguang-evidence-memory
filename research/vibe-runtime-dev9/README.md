# Fixed-image metadata preflight

Checks immutable image/config identities, repository HEAD, exact solver-visible production bytes, pinned STC/gFTL gitlinks, Python/pytest and compiler availability. It never runs target tests or imports project code. Only explicit checksum-pinned Versioneer export bytes can be restored in the disposable image. No task statements, target test patches, solutions, memory payloads or user credentials are included. Public standard Ubuntu runner only, empty permissions, offline constrained containers, no artifact uploads. Full remains disabled.
