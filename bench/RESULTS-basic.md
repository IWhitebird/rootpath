# Benchmark: graph tools vs baseline tools

**Telemetry: `basic`** (error rate and latency only: no tracing, the agent has to work out how failures propagate)

Same injected failure, same model (headless Claude Code), same remediation actions. **graph** = FalkorDB reasoning tools; **baseline** = health + logs + flat change log only.

*Correct fix* = the expected action on the expected target was taken. *Clean* = that, and no other (wrong) remediation action.

| scenario | mode | correct fix | wrong actions | calls to root cause | logs read | total calls | agent time | input tokens | cost |
|---|---|---|---|---|---|---|---|---|---|
| bad-deploy | graph | ✅ | - | 4 | 1 | 9 | 50.0s | 103,644 | $0.137 |
| bad-deploy | graph | ✅ | - | 4 | 1 | 9 | 45.7s | 104,266 | $0.135 |
| bad-deploy | baseline | ✅ | - | 4 | 2 | 7 | 34.9s | 47,325 | $0.088 |
| bad-deploy | baseline | ✅ | - | 4 | 2 | 7 | 34.6s | 46,674 | $0.089 |
| flag | graph | ✅ | - | 4 | 1 | 9 | 41.3s | 103,794 | $0.135 |
| flag | graph | ✅ | - | 4 | 1 | 9 | 43.2s | 104,079 | $0.139 |
| flag | baseline | ✅ | - | 5 | 3 | 8 | 40.4s | 56,975 | $0.102 |
| flag | baseline | ✅ | - | 4 | 2 | 7 | 39.0s | 46,406 | $0.092 |
| pool | graph | ✅ | - | 4 | 1 | 9 | 36.6s | 90,191 | $0.119 |
| pool | graph | ✅ | - | 4 | 1 | 9 | 38.8s | 100,287 | $0.119 |
| pool | baseline | ✅ | - | 6 | 4 | 9 | 33.6s | 53,846 | $0.088 |
| pool | baseline | ✅ | - | 6 | 4 | 9 | 32.9s | 53,532 | $0.088 |
| deploy-noise | graph | ✅ | - | 4 | 1 | 9 | 46.6s | 105,743 | $0.137 |
| deploy-noise | graph | ✅ | - | 4 | 1 | 11 | 58.3s | 133,912 | $0.159 |
| deploy-noise | baseline | ✅ | - | 5 | 3 | 10 | 49.1s | 75,028 | $0.129 |
| deploy-noise | baseline | ✅ | - | 4 | 2 | 9 | 47.1s | 70,354 | $0.113 |
| flag-collision | graph | ✅ | - | 4 | 1 | 9 | 44.7s | 107,049 | $0.146 |
| flag-collision | graph | ✅ | - | 4 | 1 | 9 | 41.7s | 91,293 | $0.136 |
| flag-collision | baseline | ✅ | - | 9 | 6 | 17 | 156.5s | 224,652 | $0.466 |
| flag-collision | baseline | ✅ | - | 6 | 3 | 12 | 76.5s | 100,414 | $0.227 |
| pool-after-deploy | graph | ✅ | - | 4 | 1 | 9 | 41.7s | 79,130 | $0.129 |
| pool-after-deploy | graph | ✅ | - | 4 | 1 | 9 | 43.5s | 98,485 | $0.125 |
| pool-after-deploy | baseline | ✅ | - | 8 | 6 | 14 | 71.3s | 96,840 | $0.162 |
| pool-after-deploy | baseline | ✅ | - | 7 | 5 | 13 | 59.0s | 98,549 | $0.159 |

## Totals

| mode | correct fix | clean fix | calls to root cause | logs read | total calls | agent time | input tokens | cost |
|---|---|---|---|---|---|---|---|---|
| graph | 12/12 | 12/12 | 48 | 12 | 110 | 532s | 1,221,873 | $1.62 |
| baseline | 12/12 | 12/12 | 68 | 42 | 122 | 675s | 970,595 | $1.80 |

*Graph-mode totals include incident bookkeeping (open_incident, notify_owner, resolve_incident) that baseline has no tools for; "calls to root cause" counts only the calls before the first fix.*

Tool sequences:

- **bad-deploy / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → rollback_service → rollback_service → verify_recovery → notify_owner → resolve_incident
- **bad-deploy / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → rollback_service → rollback_service → verify_recovery → notify_owner → resolve_incident
- **bad-deploy / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → rollback_service → rollback_service → verify_recovery
- **bad-deploy / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → rollback_service → rollback_service → verify_recovery
- **flag / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → set_feature_flag → set_feature_flag → verify_recovery → notify_owner → resolve_incident
- **flag / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → set_feature_flag → set_feature_flag → verify_recovery → notify_owner → resolve_incident
- **flag / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → get_logs → set_feature_flag → set_feature_flag → verify_recovery
- **flag / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → set_feature_flag → set_feature_flag → verify_recovery
- **pool / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → restart_service → restart_service → verify_recovery → notify_owner → resolve_incident
- **pool / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → restart_service → restart_service → verify_recovery → notify_owner → resolve_incident
- **pool / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → get_logs → get_logs → restart_service → restart_service → verify_recovery
- **pool / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → get_logs → get_logs → restart_service → restart_service → verify_recovery
- **deploy-noise / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → rollback_service → rollback_service → verify_recovery → notify_owner → resolve_incident
- **deploy-noise / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → rollback_service → rollback_service → verify_recovery → get_service_health → verify_recovery → notify_owner → resolve_incident
- **deploy-noise / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → get_logs → rollback_service → rollback_service → verify_recovery → get_service_health → verify_recovery
- **deploy-noise / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → rollback_service → rollback_service → verify_recovery → get_service_health → get_logs
- **flag-collision / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → set_feature_flag → set_feature_flag → verify_recovery → notify_owner → resolve_incident
- **flag-collision / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → set_feature_flag → set_feature_flag → verify_recovery → notify_owner → resolve_incident
- **flag-collision / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → get_logs → get_logs → get_logs → get_logs → list_changes_flat → set_feature_flag → set_feature_flag → verify_recovery → get_service_health → get_logs → get_service_health → verify_recovery → get_service_health
- **flag-collision / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → list_changes_flat → get_logs → set_feature_flag → set_feature_flag → verify_recovery → get_service_health → get_logs → get_logs
- **pool-after-deploy / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → restart_service → restart_service → verify_recovery → notify_owner → resolve_incident
- **pool-after-deploy / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → restart_service → restart_service → verify_recovery → notify_owner → resolve_incident
- **pool-after-deploy / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → get_logs → get_logs → get_logs → get_logs → restart_service → restart_service → verify_recovery → get_service_health → verify_recovery → verify_recovery
- **pool-after-deploy / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → get_logs → get_logs → get_logs → restart_service → restart_service → verify_recovery → get_service_health → verify_recovery → get_service_health
