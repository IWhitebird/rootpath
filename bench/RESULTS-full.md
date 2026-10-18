# Benchmark: graph tools vs baseline tools

**Telemetry: `full`** (error rate, latency and where errors originate: what distributed tracing gives you)

Same injected failure, same model (headless Claude Code), same remediation actions. **graph** = FalkorDB reasoning tools; **baseline** = health + logs + flat change log only.

*Correct fix* = the expected action on the expected target was taken. *Clean* = that, and no other (wrong) remediation action.

| scenario | mode | correct fix | wrong actions | calls to root cause | logs read | total calls | agent time | input tokens | cost |
|---|---|---|---|---|---|---|---|---|---|
| bad-deploy | graph | yes | - | 4 | 1 | 9 | 44.4s | 103,778 | $0.188 |
| bad-deploy | graph | yes | - | 4 | 1 | 9 | 41.3s | 92,660 | $0.134 |
| bad-deploy | baseline | yes | - | 3 | 1 | 6 | 32.0s | 45,079 | $0.081 |
| bad-deploy | baseline | yes | - | 3 | 1 | 6 | 34.1s | 45,186 | $0.080 |
| flag | graph | yes | - | 4 | 1 | 9 | 47.7s | 106,022 | $0.146 |
| flag | graph | yes | - | 4 | 1 | 9 | 42.4s | 103,996 | $0.139 |
| flag | baseline | yes | - | 4 | 2 | 7 | 36.3s | 48,280 | $0.098 |
| flag | baseline | yes | - | 4 | 2 | 7 | 35.8s | 47,673 | $0.091 |
| pool | graph | yes | - | 4 | 1 | 9 | 37.7s | 101,264 | $0.121 |
| pool | graph | yes | - | 4 | 1 | 9 | 37.9s | 100,924 | $0.119 |
| pool | baseline | yes | - | 3 | 1 | 6 | 34.0s | 42,523 | $0.075 |
| pool | baseline | yes | - | 3 | 1 | 6 | 35.4s | 42,671 | $0.077 |
| deploy-noise | graph | yes | - | 4 | 1 | 9 | 47.6s | 106,275 | $0.143 |
| deploy-noise | graph | yes | - | 4 | 1 | 9 | 43.9s | 105,041 | $0.139 |
| deploy-noise | baseline | yes | - | 3 | 1 | 6 | 40.2s | 48,908 | $0.097 |
| deploy-noise | baseline | yes | - | 3 | 1 | 8 | 48.4s | 68,260 | $0.109 |
| flag-collision | graph | yes | - | 4 | 1 | 9 | 44.0s | 95,010 | $0.142 |
| flag-collision | graph | yes | - | 4 | 1 | 9 | 46.1s | 92,047 | $0.146 |
| flag-collision | baseline | yes | - | 4 | 2 | 7 | 41.6s | 49,274 | $0.103 |
| flag-collision | baseline | yes | rollback fraud-check | 7 | 4 | 15 | 245.4s | 164,082 | $0.460 |
| pool-after-deploy | graph | yes | - | 4 | 1 | 9 | 42.5s | 89,228 | $0.129 |
| pool-after-deploy | graph | yes | - | 4 | 1 | 9 | 43.7s | 92,639 | $0.134 |
| pool-after-deploy | baseline | yes | - | 4 | 2 | 7 | 44.7s | 56,454 | $0.114 |
| pool-after-deploy | baseline | yes | - | 5 | 3 | 10 | 59.3s | 83,071 | $0.163 |

## Totals

| mode | correct fix | clean fix | calls to root cause | logs read | total calls | agent time | input tokens | cost |
|---|---|---|---|---|---|---|---|---|
| graph | 12/12 | 12/12 | 48 | 12 | 108 | 519s | 1,188,884 | $1.68 |
| baseline | 12/12 | 11/12 | 46 | 21 | 91 | 687s | 741,461 | $1.55 |

*Graph-mode totals include incident bookkeeping (open_incident, notify_owner, resolve_incident) that baseline has no tools for; "calls to root cause" counts only the calls before the first fix.*

Tool sequences:

- **bad-deploy / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → rollback_service → rollback_service → verify_recovery → notify_owner → resolve_incident
- **bad-deploy / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → rollback_service → rollback_service → verify_recovery → notify_owner → resolve_incident
- **bad-deploy / baseline**: get_service_health → list_changes_flat → get_logs → rollback_service → rollback_service → verify_recovery
- **bad-deploy / baseline**: get_service_health → list_changes_flat → get_logs → rollback_service → rollback_service → verify_recovery
- **flag / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → set_feature_flag → set_feature_flag → verify_recovery → notify_owner → resolve_incident
- **flag / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → set_feature_flag → set_feature_flag → verify_recovery → notify_owner → resolve_incident
- **flag / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → set_feature_flag → set_feature_flag → verify_recovery
- **flag / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → set_feature_flag → set_feature_flag → verify_recovery
- **pool / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → restart_service → restart_service → verify_recovery → notify_owner → resolve_incident
- **pool / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → restart_service → restart_service → verify_recovery → notify_owner → resolve_incident
- **pool / baseline**: get_service_health → list_changes_flat → get_logs → restart_service → restart_service → verify_recovery
- **pool / baseline**: get_service_health → list_changes_flat → get_logs → restart_service → restart_service → verify_recovery
- **deploy-noise / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → rollback_service → rollback_service → verify_recovery → notify_owner → resolve_incident
- **deploy-noise / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → rollback_service → rollback_service → verify_recovery → notify_owner → resolve_incident
- **deploy-noise / baseline**: get_service_health → list_changes_flat → get_logs → rollback_service → rollback_service → verify_recovery
- **deploy-noise / baseline**: get_service_health → list_changes_flat → get_logs → rollback_service → rollback_service → verify_recovery → get_service_health → verify_recovery
- **flag-collision / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → set_feature_flag → set_feature_flag → verify_recovery → notify_owner → resolve_incident
- **flag-collision / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → set_feature_flag → set_feature_flag → verify_recovery → notify_owner → resolve_incident
- **flag-collision / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → set_feature_flag → set_feature_flag → verify_recovery
- **flag-collision / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → get_logs → get_logs → list_changes_flat → rollback_service → rollback_service → verify_recovery → get_service_health → get_logs → set_feature_flag → set_feature_flag → verify_recovery
- **pool-after-deploy / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → restart_service → restart_service → verify_recovery → notify_owner → resolve_incident
- **pool-after-deploy / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → restart_service → restart_service → verify_recovery → notify_owner → resolve_incident
- **pool-after-deploy / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → restart_service → restart_service → verify_recovery
- **pool-after-deploy / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → get_logs → restart_service → restart_service → verify_recovery → get_service_health → get_logs
