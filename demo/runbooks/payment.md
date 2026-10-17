---
id: RB-PAY-01
service: payment
title: Payment 5xx after a release
---
1. Check whether payment was deployed in the last hour (`recent_changes`).
2. If the error is a TypeError/NullPointer in card_tokenizer, roll back to the previous version. The tokenizer has no feature flag.
3. Verify checkout error rate drops below 2% within 60s.
4. Page #team-payments with the failing version and the stack trace.
