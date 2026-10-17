# Benchmark: graph tools vs baseline tools

**Telemetry: `full`** (error rate, latency and where errors originate: what distributed tracing gives you)

Same injected failure, same model (headless Claude Code), same remediation actions. **graph** = FalkorDB reasoning tools; **baseline** = health + logs + flat change log only.

| scenario | mode | correct fix | wrong actions | calls to root cause | logs read | total calls | agent time | input tokens | cost |
|---|---|---|---|---|---|---|---|---|---|
| bad-deploy | graph | ✅ | - | 4 | 1 | 9 | 43.4s | 98,850 | $0.130 |
| bad-deploy | graph | ✅ | - | 5 | 1 | 10 | 49.8s | 106,693 | $0.142 |
| bad-deploy | baseline | ✅ | - | 3 | 1 | 6 | 63.5s | 44,717 | $0.080 |
| bad-deploy | baseline | ✅ | - | 3 | 1 | 6 | 35.9s | 44,350 | $0.081 |
| flag | graph | ✅ | - | 5 | 1 | 10 | 47.6s | 94,291 | $0.137 |
| flag | graph | ✅ | - | 4 | 1 | 9 | 41.2s | 88,713 | $0.130 |
| flag | baseline | ✅ | - | 4 | 2 | 7 | 38.4s | 47,778 | $0.097 |
| flag | baseline | ✅ | - | 4 | 2 | 7 | 36.8s | 47,695 | $0.094 |
| pool | graph | ✅ | - | 5 | 1 | 10 | 40.2s | 93,847 | $0.117 |
| pool | graph | ✅ | - | 7 | 1 | 12 | 54.0s | 102,546 | $0.135 |
| pool | baseline | ✅ | - | 4 | 1 | 10 | 50.3s | 72,467 | $0.110 |
| pool | baseline | ✅ | - | 4 | 1 | 7 | 37.2s | 50,535 | $0.082 |
| deploy-noise | graph | ✅ | - | 4 | 1 | 9 | 43.4s | 101,241 | $0.132 |
| deploy-noise | graph | ✅ | - | 4 | 1 | 9 | 44.5s | 77,825 | $0.126 |
| deploy-noise | baseline | ✅ | - | 3 | 1 | 6 | 35.1s | 46,751 | $0.084 |
| deploy-noise | baseline | ✅ | - | 3 | 1 | 6 | 37.3s | 46,660 | $0.085 |
| flag-collision | graph | ✅ | - | 5 | 1 | 10 | 47.3s | 94,021 | $0.154 |
| flag-collision | graph | ✅ | - | 5 | 1 | 10 | 44.4s | 97,628 | $0.148 |
| flag-collision | baseline | ✅ | - | 4 | 2 | 7 | 41.3s | 48,832 | $0.106 |
| flag-collision | baseline | ✅ | - | 4 | 2 | 7 | 36.1s | 49,003 | $0.098 |
| pool-after-deploy | graph | ❌ | rollback inventory | 4 | 1 | 9 | 41.8s | 77,245 | $0.132 |
| pool-after-deploy | graph | ✅ | - | 7 | 1 | 13 | 53.9s | 125,436 | $0.177 |
| pool-after-deploy | baseline | ❌ | rollback inventory | 3 | 1 | 6 | 37.9s | 44,122 | $0.083 |
| pool-after-deploy | baseline | ❌ | rollback inventory | 3 | 1 | 6 | 36.3s | 46,057 | $0.095 |

## Totals

| mode | correct fix | calls to root cause | logs read | total calls | agent time | input tokens | cost |
|---|---|---|---|---|---|---|---|
| graph | 11/12 | 59 | 12 | 120 | 552s | 1,158,336 | $1.66 |
| baseline | 10/12 | 42 | 16 | 81 | 486s | 588,967 | $1.10 |

*Graph-mode totals include incident bookkeeping (open_incident, notify_owner, resolve_incident) that baseline has no tools for; "calls to root cause" counts only the calls before the first fix.*

Tool sequences:

- **bad-deploy / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → rollback_service → rollback_service → verify_recovery → notify_owner → resolve_incident
- **bad-deploy / graph**: rank_suspects → get_service_health → get_logs → find_similar_incidents → open_incident → rollback_service → rollback_service → verify_recovery → notify_owner → resolve_incident
- **bad-deploy / baseline**: get_service_health → list_changes_flat → get_logs → rollback_service → rollback_service → verify_recovery
- **bad-deploy / baseline**: get_service_health → list_changes_flat → get_logs → rollback_service → rollback_service → verify_recovery
- **flag / graph**: rank_suspects → get_service_health → get_logs → find_similar_incidents → open_incident → set_feature_flag → set_feature_flag → verify_recovery → notify_owner → resolve_incident
- **flag / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → set_feature_flag → set_feature_flag → verify_recovery → notify_owner → resolve_incident
- **flag / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → set_feature_flag → set_feature_flag → verify_recovery
- **flag / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → set_feature_flag → set_feature_flag → verify_recovery
- **pool / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → get_owner → restart_service → restart_service → verify_recovery → notify_owner → resolve_incident
- **pool / graph**: get_service_health → rank_suspects → get_logs → find_similar_incidents → recent_changes → open_incident → get_owner → restart_service → restart_service → verify_recovery → notify_owner → resolve_incident
- **pool / baseline**: get_service_health → list_changes_flat → get_logs → list_changes_flat → restart_service → restart_service → verify_recovery → get_service_health → verify_recovery → get_service_health
- **pool / baseline**: get_service_health → list_changes_flat → get_logs → list_changes_flat → restart_service → restart_service → verify_recovery
- **deploy-noise / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → rollback_service → rollback_service → verify_recovery → notify_owner → resolve_incident
- **deploy-noise / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → rollback_service → rollback_service → verify_recovery → notify_owner → resolve_incident
- **deploy-noise / baseline**: get_service_health → list_changes_flat → get_logs → rollback_service → rollback_service → verify_recovery
- **deploy-noise / baseline**: get_service_health → list_changes_flat → get_logs → rollback_service → rollback_service → verify_recovery
- **flag-collision / graph**: get_service_health → rank_suspects → get_logs → find_similar_incidents → open_incident → set_feature_flag → set_feature_flag → verify_recovery → notify_owner → resolve_incident
- **flag-collision / graph**: rank_suspects → get_service_health → get_logs → find_similar_incidents → open_incident → set_feature_flag → set_feature_flag → verify_recovery → notify_owner → resolve_incident
- **flag-collision / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → set_feature_flag → set_feature_flag → verify_recovery
- **flag-collision / baseline**: get_service_health → list_changes_flat → get_logs → get_logs → set_feature_flag → set_feature_flag → verify_recovery
- **pool-after-deploy / graph**: rank_suspects → get_logs → find_similar_incidents → open_incident → rollback_service → rollback_service → verify_recovery → notify_owner → resolve_incident
- **pool-after-deploy / graph**: rank_suspects → get_service_health → get_logs → find_similar_incidents → get_owner → recent_changes → open_incident → restart_service → restart_service → verify_recovery → get_service_health → notify_owner → resolve_incident
- **pool-after-deploy / baseline**: get_service_health → list_changes_flat → get_logs → rollback_service → rollback_service → verify_recovery
- **pool-after-deploy / baseline**: get_service_health → list_changes_flat → get_logs → rollback_service → rollback_service → verify_recovery
