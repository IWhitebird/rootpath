# Benchmark: graph tools vs baseline tools

**Telemetry: `full`** (error rate, latency and where errors originate: what distributed tracing gives you)

Same injected failure, same model (headless Claude Code), same remediation actions. **graph** = FalkorDB reasoning tools; **baseline** = health + logs + flat change log only.

| scenario | mode | correct fix | wrong actions | calls to root cause | logs read | total calls | agent time | input tokens | cost |
|---|---|---|---|---|---|---|---|---|---|
| bad-deploy | graph | ✅ | - | 5 | 1 | 10 | 46.1s | 93,633 | $0.135 |
| bad-deploy | baseline | ✅ | - | 3 | 1 | 6 | 36.3s | 44,143 | $0.078 |
| flag | graph | ✅ | - | 4 | 1 | 9 | 45.2s | 94,449 | $0.123 |
| flag | baseline | ✅ | - | 4 | 2 | 10 | 55.8s | 78,471 | $0.121 |
| pool | graph | ✅ | - | 7 | 1 | 12 | 49.0s | 100,522 | $0.129 |
| pool | baseline | ✅ | - | 3 | 1 | 6 | 33.5s | 41,570 | $0.076 |
| deploy-noise | graph | ✅ | - | 5 | 1 | 10 | 43.9s | 96,402 | $0.141 |
| deploy-noise | baseline | ✅ | - | 3 | 1 | 6 | 39.2s | 47,694 | $0.095 |
| flag-collision | graph | ✅ | - | 6 | 1 | 11 | 46.2s | 95,024 | $0.155 |
| flag-collision | baseline | ✅ | - | 4 | 2 | 9 | 56.1s | 69,125 | $0.124 |
| pool-after-deploy | graph | ✅ | - | 7 | 1 | 13 | 66.4s | 128,843 | $0.179 |
| pool-after-deploy | baseline | ✅ | - | 5 | 3 | 9 | 64.0s | 74,151 | $0.167 |

## Totals

| mode | correct fix | calls to root cause | logs read | total calls | agent time | input tokens | cost |
|---|---|---|---|---|---|---|---|
| graph | 6/6 | 34 | 6 | 65 | 297s | 608,873 | $0.86 |
| baseline | 6/6 | 22 | 10 | 46 | 285s | 355,154 | $0.66 |

*Graph-mode totals include incident bookkeeping (open_incident, notify_owner, resolve_incident) that baseline has no tools for; "calls to root cause" counts only the calls before the first fix.*

Tool sequences:

- **bad-deploy / graph**: rank_suspects → get_service_health → get_logs → find_similar_incidents → open_incident → rollback_service → rollback_service → verify_recovery → notify_owner → resolve_incident
- **bad-deploy / baseline**: get_service_health → list_changes_flat → get_logs → rollback_service → rollback_service → verify_recovery
- **flag / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → set_feature_flag → set_feature_flag → verify_recovery → notify_owner → resolve_incident
- **flag / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → set_feature_flag → set_feature_flag → verify_recovery → get_service_health → verify_recovery → verify_recovery
- **pool / graph**: get_service_health → rank_suspects → get_logs → find_similar_incidents → recent_changes → open_incident → get_owner → restart_service → restart_service → verify_recovery → notify_owner → resolve_incident
- **pool / baseline**: get_service_health → list_changes_flat → get_logs → restart_service → restart_service → verify_recovery
- **deploy-noise / graph**: rank_suspects → get_service_health → get_logs → find_similar_incidents → open_incident → rollback_service → rollback_service → verify_recovery → notify_owner → resolve_incident
- **deploy-noise / baseline**: get_service_health → list_changes_flat → get_logs → rollback_service → rollback_service → verify_recovery
- **flag-collision / graph**: rank_suspects → get_service_health → get_logs → find_similar_incidents → recent_changes → open_incident → set_feature_flag → set_feature_flag → verify_recovery → notify_owner → resolve_incident
- **flag-collision / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → set_feature_flag → set_feature_flag → verify_recovery → get_service_health → verify_recovery
- **pool-after-deploy / graph**: rank_suspects → get_service_health → get_logs → find_similar_incidents → recent_changes → get_owner → open_incident → restart_service → restart_service → verify_recovery → get_service_health → notify_owner → resolve_incident
- **pool-after-deploy / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → get_logs → restart_service → restart_service → verify_recovery → get_service_health
