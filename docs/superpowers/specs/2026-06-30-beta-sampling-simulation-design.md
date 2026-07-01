# Beta Sampling Simulation Design

**Date:** 2026-06-30  
**Status:** Approved for implementation

---

## Problem

The current trade simulation (`trade/simulation.py`) uses fixed point estimates for player stats (e.g. 63% first serve in). These are single numbers derived from in-match observations and treated as exact. Early in a match, these estimates are based on very few data points and are highly uncertain — but the simulation has no way to express that uncertainty. This caused ROYWEN-style flip-flopping: a single point shifts the observed percentage, which shifts the MC win probability sharply, which triggers an entry or exit.

---

## Solution

For each simulation run, sample player stats from Beta distributions rather than using fixed point estimates. A player with 8/12 first serves in gets modelled as `Beta(8.5, 4.5)` rather than a fixed 0.667. Each of the 10,000 runs draws a fresh set of stats, simulates game/set/match with those stats, and discards them. The win probability is the average across all runs.

**Effect:** Early in a match (few observations), Beta distributions are wide — the win probability output naturally has more variance, reflecting genuine uncertainty. Late in a match (many observations), Beta distributions tighten around the observed values and the output converges to what the point-estimate model would give. No configuration needed — the behaviour self-adjusts as the match progresses.

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Sampling granularity | Once per simulation run | Each run is one coherent "possible world"; within-run variance from point-level randomness is sufficient |
| Prior | Jeffreys (`+0.5` to both alpha and beta) | Handles edge cases (all successes, all failures, None counts) without arbitrary clipping; principled non-informative prior |
| Stat coherence constraints | None | Inversions (win_first < win_second) add noise but no systematic bias; not worth the complexity |
| Hybrid with point estimates | No | Beta naturally degrades to point estimate with many observations; mixing is not statistically principled |
| Loop structure | Single combined loop | Game/set/match sims use the same sampled stats per iteration — they represent the same scenario |

---

## Implementation

### `_sample_stats(stats)` — new function in `trade/simulation.py`

Inputs a stats dict (must carry `_num`/`_den` keys populated by the ATP client).  
Returns a new stats dict with sampled values for all five stats.

For each stat:
```
a = num + 0.5
b = (den - num) + 0.5
sampled_value = np.random.beta(a, b)
```

Fallback: if `num` or `den` is `None`, use the existing point estimate (`stats[key]`).

Stats sampled: `first_in`, `win_first`, `win_second`, `return_first`, `return_second`.

### `estimate_win_prob` — modified in `trade/simulation.py`

The three separate simulation loops (game, set, match) collapse into one loop of `n_sims` iterations:

```
for each iteration:
    sample p1_stats_s from _sample_stats(p1_stats)
    sample p2_stats_s from _sample_stats(p2_stats)
    run game sim  → accumulate game_wins
    run set sim   → accumulate set_wins
    run match sim → accumulate match_wins
```

Return dict unchanged: `{"match": ..., "set": ..., "game": ...}`.

---

## Files Changed

| File | Change |
|---|---|
| `trade/simulation.py` | Add `_sample_stats`; merge three loops into one in `estimate_win_prob` |

No other files touched. The ATP client already provides `_num`/`_den` keys; the rest of the pipeline is unaffected.

---

## Known Limitations

- Stat coherence: independent Beta sampling can produce inversions (e.g. `win_first < win_second`) especially early in a match when distributions are wide. These add noise but not systematic bias.
- Stats not yet available (None counts): falls back to point estimate silently. This can happen in the first few points of a match before the ATP client has accumulated observations.

---

## Out of Scope

- Career stats as prior (only in-match observations used)
- Correlation modelling across stats (independent sampling per stat)
- Swapping/clamping sampled values to enforce ordering constraints
