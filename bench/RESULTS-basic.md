# Benchmark: graph tools vs baseline tools

**Telemetry: `basic`** (error rate and latency only: no tracing, the agent has to work out how failures propagate)

Same injected failure, same model (headless Claude Code), same remediation actions. **graph** = FalkorDB reasoning tools; **baseline** = health + logs + flat change log only.

| scenario | mode | correct fix | wrong actions | calls to root cause | logs read | total calls | agent time | input tokens | cost |
|---|---|---|---|---|---|---|---|---|---|
| bad-deploy | graph | ✅ | - | 5 | 1 | 10 | 40.0s | 79,677 | $0.136 |
| bad-deploy | baseline | ✅ | - | 4 | 2 | 7 | 35.7s | 47,527 | $0.095 |
| flag | graph | ✅ | - | 6 | 2 | 11 | 54.1s | 114,533 | $0.153 |
| flag | baseline | ✅ | - | 4 | 2 | 7 | 34.0s | 45,553 | $0.089 |
| pool | graph | ✅ | - | 7 | 1 | 12 | 45.1s | 91,162 | $0.133 |
| pool | baseline | ✅ | - | 5 | 3 | 8 | 40.6s | 53,680 | $0.091 |
| deploy-noise | graph | ✅ | - | 5 | 1 | 10 | 58.2s | 106,861 | $0.145 |
| deploy-noise | baseline | ✅ | - | 5 | 3 | 8 | 37.9s | 51,752 | $0.107 |
| flag-collision | graph | ✅ | - | 5 | 1 | 12 | 61.0s | 130,438 | $0.168 |
| flag-collision | baseline | ✅ | - | 6 | 3 | 9 | 52.5s | 63,392 | $0.145 |
| pool-after-deploy | graph | ✅ | - | 5 | 1 | 11 | 56.5s | 119,463 | $0.172 |
| pool-after-deploy | baseline | ❌ | rollback inventory | 4 | 2 | 7 | 42.4s | 46,795 | $0.096 |

## Totals

| mode | correct fix | calls to root cause | logs read | total calls | agent time | input tokens | cost |
|---|---|---|---|---|---|---|---|
| graph | 6/6 | 33 | 7 | 66 | 315s | 642,134 | $0.91 |
| baseline | 5/6 | 28 | 15 | 46 | 243s | 308,699 | $0.62 |

*Graph-mode totals include incident bookkeeping (open_incident, notify_owner, resolve_incident) that baseline has no tools for; "calls to root cause" counts only the calls before the first fix.*

Tool sequences:

- **bad-deploy / graph**: rank_suspects → get_service_health → get_logs → find_similar_incidents → open_incident → rollback_service → rollback_service → verify_recovery → notify_owner → resolve_incident
- **bad-deploy / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → rollback_service → rollback_service → verify_recovery
- **flag / graph**: rank_suspects → get_service_health → get_logs → find_similar_incidents → get_logs → open_incident → set_feature_flag → set_feature_flag → verify_recovery → notify_owner → resolve_incident
- **flag / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → set_feature_flag → set_feature_flag → verify_recovery
- **pool / graph**: get_service_health → rank_suspects → get_logs → find_similar_incidents → recent_changes → open_incident → get_owner → restart_service → restart_service → verify_recovery → notify_owner → resolve_incident
- **pool / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → get_logs → restart_service → restart_service → verify_recovery
- **deploy-noise / graph**: get_service_health → rank_suspects → get_logs → find_similar_incidents → open_incident → rollback_service → rollback_service → verify_recovery → notify_owner → resolve_incident
- **deploy-noise / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → get_logs → rollback_service → rollback_service → verify_recovery
- **flag-collision / graph**: rank_suspects → get_service_health → get_logs → find_similar_incidents → open_incident → set_feature_flag → set_feature_flag → verify_recovery → get_service_health → verify_recovery → notify_owner → resolve_incident
- **flag-collision / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → get_logs → list_changes_flat → set_feature_flag → set_feature_flag → verify_recovery
- **pool-after-deploy / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → get_owner → restart_service → restart_service → verify_recovery → get_service_health → notify_owner → resolve_incident
- **pool-after-deploy / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → rollback_service → rollback_service → verify_recovery
