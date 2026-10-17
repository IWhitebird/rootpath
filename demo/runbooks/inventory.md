---
id: RB-INV-01
service: inventory
title: Inventory connection pool exhaustion
---
1. Symptom: `psycopg_pool.PoolTimeout` in inventory logs; checkout and catalog both degrade.
2. Cause: a known connection leak in the stock-reservation path (INC-019). It exists in every
   current release, so it is NOT caused by a recent deploy.
3. Fix: restart inventory (drains the stuck connections, ~3s blip). Do not roll back inventory
   for PoolTimeout: the leak is in all versions, and a rollback only reverts a healthy release.
4. Page #team-platform so the leak fix (PLAT-212) gets prioritised.
