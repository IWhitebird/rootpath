---
id: RB-INV-01
service: inventory
title: Inventory connection pool exhaustion
---
1. Symptom: `psycopg_pool.PoolTimeout` in inventory logs; checkout and catalog both degrade.
2. Restarting inventory drains the stuck connections (safe, ~3s blip).
3. File a follow-up for the leak; do not roll back unless a deploy happened in the last hour.
