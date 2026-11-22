# agriculture-quality-assessment-and-predictive-irrigation
An Internet of Things project focusing on automated seedling quality assessment and predictive irrigation management.


## Node fallback irrigation

Edge now provisions per-node fallback configuration and an application heartbeat. Updated Things firmware samples soil while offline and can perform one bounded fallback run per dry episode. Fallback is disabled until explicitly configured in `AI/fallback_policies.json`. Both Edge and Things must be updated; see [AI/NODE_FALLBACK.md](AI/NODE_FALLBACK.md) for configuration, recovery, and validation limits.
