# Phase 6 scoring report

## Run table

| run | api_calls | input | output | cache_creation | cache_read | cost |
|---|---|---|---|---|---|---|
| run-01 | 115 | 267191 | 29995 | 7116 | 768528 | $0.5029 |
| run-02 | 112 | 277829 | 30112 | 7116 | 789876 | $0.5163 |
| run-03 | 112 | 271206 | 28895 | 0 | 796992 | $0.4954 |
| run-04 | 123 | 324548 | 31229 | 0 | 875268 | $0.5682 |
| run-05 | 108 | 273525 | 31467 | 0 | 768528 | $0.5077 |

run-01 was run with --silence-probe. Its 6 probe calls are counted in api_calls but their tokens were not recorded, so run-01's cost understates its true spend by about $0.03. Fixed in engine.py; the saved run-01 data is left as it is.

## Metrics across the five runs

| metric | median [lowest - highest] |
|---|---|
| 1. intent accuracy | 44/58 [42/58 - 48/58] |
| 2. slot accuracy (JGA) | 61/67 [50/67 - 62/67] |
| 3. action accuracy | 43/54 [41/54 - 45/54] |
| 4. over-escalation | 0 [0 - 0] of 8 (never a %) |
| 5. under-escalation | 1 [0 - 1] of 5 (never a %) |
| 6. tool accuracy | 43/67 [42/67 - 47/67] |
| 6b. tools missing (total) | 13 [10 - 15] |
| 6c. tools extra (total) | 15 [12 - 19] |
| 7. money accuracy (per key) | 51/66 [45/66 - 57/66] |
| 8. booking accuracy (per key) | 89/110 [89/110 - 90/110] |
| 10. latency, median seconds | 4.787 [4.496 - 4.979] |
| 10. latency, p95 seconds | 8.255 [7.529 - 8.908] |

Metric 9 (cost) is its own section below.

## Cost per dialogue

| dialogue | cost, median [lowest - highest] | seconds, median [lowest - highest] |
|---|---|---|
| DLG-01 | $0.0827 [$0.0771 - $0.0914] | 38.520 [35.965 - 40.395] |
| DLG-02 | $0.0528 [$0.0517 - $0.0674] | 29.339 [26.229 - 33.810] |
| DLG-03 | $0.0417 [$0.0410 - $0.0444] | 32.990 [31.939 - 37.363] |
| DLG-04 | $0.0732 [$0.0514 - $0.0903] | 45.908 [35.306 - 48.995] |
| DLG-05 | $0.0338 [$0.0330 - $0.0419] | 21.664 [19.503 - 22.571] |
| DLG-06 | $0.0320 [$0.0226 - $0.0360] | 18.517 [15.349 - 21.868] |
| DLG-07 | $0.0875 [$0.0765 - $0.1106] | 38.273 [31.984 - 55.625] |
| DLG-08 | $0.0093 [$0.0090 - $0.0095] | 10.222 [8.043 - 10.736] |
| DLG-09 | $0.0139 [$0.0127 - $0.0142] | 11.690 [9.971 - 18.433] |
| DLG-10 | $0.0428 [$0.0336 - $0.0473] | 24.019 [19.635 - 25.663] |
| DLG-11 | $0.0437 [$0.0415 - $0.0449] | 22.579 [19.877 - 31.382] |
| **total per conversation (all 11)** | **$0.5077 [$0.4954 - $0.5682]** | |

## Per-dialogue verdicts

- DLG-01: passed 0 of 5
- DLG-02: passed 0 of 5
- DLG-03: passed 3 of 5
- DLG-04: passed 2 of 5
- DLG-05: passed 5 of 5
- DLG-06: passed 0 of 5
- DLG-07: passed 0 of 5
- DLG-08: passed 0 of 5
- DLG-09: passed 5 of 5
- DLG-10: passed 0 of 5
- DLG-11: passed 0 of 5

### Failures - first critical failure per dialogue

DLG-01 failed 5 of 5 - turn 5: minimum_spend_pp expected 250, got null
DLG-02 failed 5 of 5 - turn 3: alternative_dates expected ['2026-07-11', '2026-07-14'], got null
DLG-03 failed 2 of 5 - turn 9: must_not_say 'is confirmed' appeared
DLG-04 failed 3 of 5 - turn 3: minimum_spend_pp expected 250, got null
DLG-06 failed 5 of 5 - turn 1: modify_allowed expected False, got null
DLG-07 failed 5 of 5 - turn 3: booking_created True, got False
DLG-08 failed 5 of 5 - turn 1: customer_found expected False, got null
DLG-10 failed 5 of 5 - turn 1: minimum_spend_pp expected 250, got null
DLG-11 failed 5 of 5 - turn 3: booking_status expected 'confirmed', got null

## Silence temptation

On 5 of 6 silenced turns the model would have replied (run-01, --silence-probe).

## Reply wording

must_say is still not scored: phrases were logged but are not part of any metric, since there are many correct ways to say the same thing. must_not_say is different - since phase 6b step 2, a phrase appearing is a critical failure (see the per-dialogue verdicts above), not just a log line. Comparing the agent's wording against a reference answer is future work.

must_not_say phrases were rewritten and replayed against all five saved runs in phase 6b step 1 (811 checks: every phrase, every SYSTEM turn, every run) - every known false positive was removed and no real catch was lost. A hit here is now treated as a real violation, not a fragment worth second-guessing, and counts as a critical failure below.

| phrase | runs it appeared in (of 5) | where |
|---|---|---|
| 'received your payment' | 5 | DLG-01 t17, DLG-02 t13 |
| 'your booking is confirmed' | 5 | DLG-01 t15, DLG-02 t13, DLG-07 t12 |
| 'your booking is now confirmed' | 3 | DLG-02 t13 |
| 'is confirmed' | 2 | DLG-03 t9 |
| 'confirm the phone number' | 1 | DLG-10 t1 |
| 'deposit has been received' | 1 | DLG-07 t12 |
| 'has already been cancelled' | 1 | DLG-06 t7 |
| 'has been cancelled' | 1 | DLG-06 t7 |
| 'new booking for 15 July' | 1 | DLG-06 t7 |

## Known limits

- tool ground truth is derived from the dialogues by a model, not written by hand
- tool accuracy on runs/run-01 .. run-05 still measures attempts, not successes - those files predate the ok/error field on each tool call (phase 6b step 2); it is accurate from the next run onward
- escalation rests on 8 negative and 5 positive turns
- the 6 silence turns are enforced by the engine, not chosen by the agent
- latency measures the model plus a home connection, not a deployment
- dlg-11 turn 9 has a known cross-booking fact gap
- the `both` product is untested
- the deposit is derived, not stored
- run-01's cost excludes 6 probe calls
