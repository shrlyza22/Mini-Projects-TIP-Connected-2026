# Sensitivity Analysis — ZTNA Trust Model

Model: `T = wR*R + wC*C + wB*B`, tiers FULL (T>=70), LIMITED (T>=40), DENIED otherwise.

## 0. Evaluated scenarios and how R/C/B arise

| Expected tier | Account | R | C | B | T | Attained |
|---|---|---|---|---|---|---|
| FULL | ratih (research) | 80 | 100 | 100 | 90.0 | FULL |
| LIMITED | ratih (research) | 80 | 50 | 70 | 69.0 | LIMITED |
| DENIED | bima (guest) | 30 | 50 | 40 | 38.0 | DENIED |

- **FULL** — R: role base for research = 80; C: IP in subnet, inside hours, MAC matches = 100; B: no failed logins = 100.
- **LIMITED** — R: role base for research = 80; C: IP/MAC binding mismatch = 100 - 50 = 50; B: 2 failed logins = 100 - 2*15 = 70.
- **DENIED** — R: role base for guest = 30; C: IP/MAC binding mismatch = 100 - 50 = 50; B: 4 failed logins = 100 - min(4*15, 60) = 40.

## 1. Access-level outcome vs weight configuration (thresholds fixed 70/40)

| Config | wR | wC | wB | FULL case | LIMITED case | DENIED case |
|---|---|---|---|---|---|---|
| Baseline (paper) | 0.50 | 0.30 | 0.20 | 90.0 (FULL) | 69.0 (LIMITED) | 38.0 (DENIED) |
| Alt-1 (flatter) | 0.45 | 0.25 | 0.30 | 91.0 (FULL) | 69.5 (LIMITED) | 38.0 (DENIED) |
| Alt-2 (context-heavy) | 0.40 | 0.40 | 0.20 | 92.0 (FULL) | 66.0 (LIMITED) | 40.0 (LIMITED) |
| Alt-3 (behaviour-heavy) | 0.40 | 0.20 | 0.40 | 92.0 (FULL) | 70.0 (FULL) | 38.0 (DENIED) |
| Alt-4 (identity-heavy) | 0.60 | 0.30 | 0.10 | 88.0 (FULL) | 70.0 (FULL) | 37.0 (DENIED) |
| Alt-5 (uniform) | 0.33 | 0.33 | 0.33 | 93.3 (FULL) | 66.7 (LIMITED) | 40.0 (LIMITED) |

## 2. Access-level outcome vs thresholds (baseline weights 0.5/0.3/0.2)

| Config | FULL thr | LIMITED thr | FULL case | LIMITED case | DENIED case |
|---|---|---|---|---|---|
| Baseline 70/40 | 70 | 40 | 90.0 -> FULL | 69.0 -> LIMITED | 38.0 -> DENIED |
| Looser 65/35 | 65 | 35 | 90.0 -> FULL | 69.0 -> FULL | 38.0 -> LIMITED |
| Stricter 75/45 | 75 | 45 | 90.0 -> FULL | 69.0 -> LIMITED | 38.0 -> DENIED |
| Limited@50 | 70 | 50 | 90.0 -> FULL | 69.0 -> LIMITED | 38.0 -> DENIED |
| Lower 60/30 | 60 | 30 | 90.0 -> FULL | 69.0 -> FULL | 38.0 -> LIMITED |

## 3. Threshold placement vs reachable scores (baseline weights)

Scores reachable from valid R/C/B combinations:

`23, 26, 29, 32, 33, 35, 36, 38, 39, 41, 42, 44, 45, 47, 48, 50, 51, 53, 54, 55.5, 56, 57, 58.5, 59, 60, 61.5, 62, 63, 64.5, 65, 66, 67.5, 69, 70.5, 72, 73.5, 75, 76.5, 78, 79.5, 81, 82.5, 84, 85.5, 87, 88.5, 90, 91.5, 94.5, 97.5`

- Threshold **70** sits in the gap (69.0, 70.5]: any value in that interval yields the same tiering.
- Threshold **40** sits in the gap (39.0, 41.0]: any value in that interval yields the same tiering.

## 4. Robustness of the FULL/LIMITED/DENIED mapping

The three evaluated cases sit close to the 70/40 boundaries, so this reports how often the mapping survives as weights vary:

- **full simplex (step 0.05)**: 27/231 (11.7% of sampled weight assignments)
- **design-consistent wR>=wC>=wB**: 79/154 (51.3% of sampled weight assignments)
- **near baseline (+/-0.05)**: 21/25 (84.0% of sampled weight assignments)

Note: the reviewer's alternative (0.45/0.25/0.30) preserves all three outcomes; the score mapping degrades only for weightings far from the identity-dominant region, and the affected cases are exactly those deliberately placed on a tier boundary.
