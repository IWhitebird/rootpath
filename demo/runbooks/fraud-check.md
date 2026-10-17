---
id: RB-FRAUD-01
service: fraud-check
title: Fraud scoring latency
---
1. fraud-check latency above 1s causes payment timeouts (payment's client timeout is 1.5s).
2. Most latency regressions come from model flags. Check `fraud_model_v2`.
3. Turning the flag off is safe: scoring falls back to the v1 rules engine.
4. Do NOT roll back fraud-check for a flag-driven regression; the binary is unchanged.
