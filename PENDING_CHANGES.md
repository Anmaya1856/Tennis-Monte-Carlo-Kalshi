# Pending & Discussed Changes — Trade Bot

Status as of 2026-07-04. Each entry: what it is, why (with evidence from logged sessions), design detail, and effort.
Sessions referenced: Jul 1 (GLIWIN/WATTUN/BROKEN), Jul 2 (BERFAR/DIASON/FERVIR/SVAMAJ), Jul 3 (JODMOC/RINDJO),
Jul 3–4 overnight (LEGBAR/WINMAN), Jul 4 Wimbledon (DESVA/KHACOB/BERFER/GIRZVE/LEHMUN).

---

> **Update 2026-07-04 (later):** A1, A2, and B2 are now IMPLEMENTED (see sections below for design; config knobs:
> `EDGE_MIN_UNDERDOG`, `UNDERDOG_PRICE`, `TRAIL_SCALE_FRAC`, `STOP_MAX_FRAC`, `DIVERGENCE_PAUSE/RESUME/EMA_ALPHA`).
> Combined replay on the Jul 4 folder: −$4.42 → −$1.39. New pending item A5 added.

## A. Parameter / shape tuning (small, config-level)

### A1. Underdog edge floor — ✅ IMPLEMENTED 2026-07-04
- **What:** minimum entry edge of 6¢ for any market priced below 0.30: `threshold = max(sin² curve, 0.06) if price < 0.30`.
- **Why:** the sin² curve opened the low extreme (2–4¢ thresholds), and cheap entries have been net losers:
  Jul 4 session's <10¢ band lost −$0.71 on 4 entries (GIRZVE 0.05 → 0.01, DESVA 0.06 → 0.01).
  Also favorite–longshot bias: longshots in prediction markets tend to be overpriced.
- **Design:** one `max()` in `edge_threshold`; config `EDGE_MIN_UNDERDOG = 0.06`, `UNDERDOG_PRICE = 0.30`.
  No discontinuity (curve is already >6¢ above ~0.22). High side untouched.
- **Evidence check:** replay on Jul 4 alone was noise (−0.27); combined with A2 it was the best variant (+0.20 vs baseline).
- **Effort:** ~10 lines + tests.

### A2. Scaled trail/stop for cheap entries — ✅ IMPLEMENTED 2026-07-04
- **What:** cap exit distances as a fraction of entry price so penny positions have working exits:
  - trail arm & giveback: `min(0.05, 0.35 × entry)`
  - stop distance: `min(max(0.15 × entry, 0.04), 0.50 × entry)`
- **Why:** DESVA position (entry 0.06): trail arm required +83% of entry (never armed at HWM 0.09 = +50%!),
  stop floor allowed −67%; position rode from 0.06 to 0.01. Fixed-cent constants don't scale at price extremes.
- **Design:** two `min()` caps in `decision.py`; entries above ~15¢ completely unaffected.
- **Effort:** ~10 lines + tests. Do together with A1.

### A3. Gap-scaled PRIOR_N (absolute points difference)  ← most recent discussion
- **What:** scale the career-prior weight by ranking-points gap:
  `PRIOR_N = clip(25 + |pts1 − pts2| / 60, 25, 50)`; fallback flat 40 if either player's points missing.
- **Why:** evenly-matched pairs should trust the match more (upset philosophy), class-gap matches should trust identity more.
  Absolute difference beats log-ratio for this job: it ranks RINDJO (Djokovic, diff 2077) first and FERVIR (peers, diff 115)
  last — both exactly matching what the PRIOR_N sweep said those matches wanted. Log-ratio got both wrong
  (Djokovic's injury-year schedule makes his *ratio* look modest while his absolute total stays elite).
- **Evidence:** scored ≈ tied with flat-40 on overall Brier (0.210 vs 0.208, 6 matches) but with better-shaped errors:
  FERVIR 0.019→0.014, RINDJO 0.047→0.041. Earlier log-ratio version scored 0.224 (rejected).
- **Design:** points from `player_rankings` (latest `roll_points` per player) at match init, alongside career lookup.
  Constants (floor 25, cap 50, slope 1/60) are v1 eyeballed to 6 matches — revisit with more data.
- **Known blind spot:** a returning-from-injury elite with gutted points reads as weak.
- **Effort:** ~30 lines + tests.

### A5. Global prior decay with match progress — ❌ REFUTED 2026-07-05
> Swept `PRIOR_N_eff = 40·max(f, 1−total_pts/T)` for T∈{300,400,600}, f∈{0.15,0.3} on 2,926 snapshots / 18 matches:
> flat 40 wins overall (0.261 vs 0.264–0.268) AND in the late-match slice ≥150 pts (0.266 vs 0.268–0.270) —
> the exact regime the decay targeted. Helps raw-stats-were-right matches (HURSTR, LEGWIN), hurts more
> mirage-stats matches (KHACOB, AUGDAV, BERFER). Prior question now closed by three sweeps:
> level (flat 40), gap-scaling (tie; A3 optional), time-decay (worse). Do not reopen without new data regime.
> Original design below kept for the record.

### ~~A5. Global prior decay with match progress~~  (original design)
- **What:** on top of per-stat pseudo-count math, decay the prior with total match evidence:
  `PRIOR_N_eff = PRIOR_N × max(0.3, 1 − total_points/400)`.
- **Why:** the five stats aren't independent evidence of today's form — a player dominating on 80 first-serve points
  is also evidence about his second-serve level today, but per-stat shrinkage ignores that. Observed: prior still
  ~35% average weight at end of set 4 of a Bo5 (TIABUB), with `win_second` at ~53% because its own den was only ~35.
- **Design:** one multiplier in `career.blend` (needs total points passed in). Early match unchanged
  (that's where the prior earns its keep per the sweep); late match the whole stat vector defers to the day.
- **Status:** run the sweep machinery on logged matches (late-match slices specifically) before building —
  same discipline that killed the rolling-window idea.

### A4. Empirical edge-threshold curve  (data-gated — needs ~15–20 finished matches)
- **What:** replace the sin² guess with a measured curve: bucket snapshots by market price, measure the distribution of
  (model − market) error per bucket against outcomes, set threshold = ~75th percentile of |error| + fees + spread.
- **Why:** every closed-form shape (sin, sin², sin³, raised cosine, Gaussian…) is an aesthetic guess at this measurement.
- **Status:** waiting on data volume. Snapshot logs already contain everything needed.
- Also data-gated: momentum calibration (does Kalshi's `points_won_from_last_10` predict anything beyond the stats —
  we log it every snapshot; test before wiring into the sim).

---

## B. Trading-logic changes (medium)

### B1. Serve-aware stop-loss deferral
- **What:** don't execute a stop loss while the position's player is serving and likely to hold
  (current-game win prob > ~0.55) — wait for the game to resolve, then stop at a (likely) better price.
  **Disaster override:** exit immediately regardless if price has fallen > 1.5× the normal stop distance.
- **Why:** stop postmortems repeatedly show bottom-ticked exits; a hold is the most likely next event and
  bounces the price. Mirror image of the existing entry game-prob gate.
- **Design:** check in `_check_exit` using `ms.last_game_prob`; applies to the stop only (not trail/PT).
  Config: `STOP_DEFER_GAME_PROB = 0.55`, override multiplier 1.5.
- **Effort:** ~15 lines + tests.

### B2. Divergence stand-down — ✅ IMPLEMENTED 2026-07-04  ← targets the #1 recurring loss
> Final evidence before implementation: FRISON (Fritz/Sonego, −$1.33 at 15.8¢ divergence) made it 5-of-5
> divergence matches lost to the market. EMA + stand-down state logged per snapshot (`divergence_ema`, `standdown`).
- **What:** when the model has been in sustained large disagreement with the market — EMA of |model − market|
  over ~20 min above 15¢ — pause **entries** on that match. Exits/trails/stops stay live. Resume when EMA < 10¢.
- **Why:** every losing session's biggest loss is one match where the model argued with the market for hours and lost:
  GLIWIN (−32¢ mean divergence, −$1.26), SVAMAJ (+16¢, −$2.41), RINDJO (−17¢), KHACOB (−19¢, −$1.94, 17 exits).
  Market beat the model on Brier in **4 of 4** such matches. Meanwhile every profitable match tracked within ~7¢ —
  the rule would not have touched them. Unlike a trade-count circuit breaker, it triggers on the diagnostic
  (persistent divergence), not on losses; you can take several stops in a well-tracked match and keep trading.
- **Explicit tradeoff:** encodes "when we disagree this much this long, we're wrong." If the model ever earns the
  right to argue, the threshold is one config number.
- **Status:** design agreed in principle; precise quantification across all 13 matches pending, then implement.
- **Effort:** ~30 lines (EMA in state, gate in `_tick`) + tests.

### B3. Relax `stats_ready` to fields-present
- **What:** gate sims only on Hawkeye fields being populated (not None); drop the per-stat num/den minimums.
- **Why:** career prior handles tiny samples (den=0 blends to pure career rate). The old gate cost an entire match:
  WINMAN never simmed until 3-6 1-3 because Winter had played only 8 second-serve points in 1.5 sets.
- **Note:** user has already partially relaxed it (num ≥ 1); this completes it. Optionally keep a minimum-points
  requirement for *entries* only if early trading feels risky.
- **Effort:** ~5 lines.

---

## C. Reliability / infrastructure

### C1. ATP feed-glitch resilience
- **What:** (a) cache last-good stats per match; (b) validate every fetch — all fields present AND every denominator
  ≥ last good (point counts never decrease mid-match); (c) on glitch, sim with **cached stats + fresh Kalshi score**
  instead of blocking; (d) if glitched continuously > 2–3 min, pause entries (exits stay live).
- **Why:** observed mid-match: stats transiently became "not ready"/regressed, freezing the model. Score drives the
  probability point-to-point; stats-from-60s-ago is a near-perfect sim. Also closes a hole where corrupted data could
  currently be simmed via the heartbeat / accept-stale paths.
- **Synergy:** the monotonicity validator is the same mechanism the (rejected) rolling window needed — build once.
- **Effort:** ~40 lines in `_run_sim` + tests.

### C2. State persistence + startup reconciliation
- **What:** write positions/budgets/cooldowns to a JSON on every change; load at startup; (live mode) cross-check
  against Kalshi portfolio API. Optionally a lockfile to prevent two processes trading the same match.
- **Why:** killed/crashed process orphans its positions (observed: a smoke-test process left a 5.85-contract position
  with no owner — live, that's unmanaged real money). Also removes the restart pain that motivated parallel terminals.
- **Effort:** medium (~half day). The root fix behind several workarounds.

### C3. Challenger career coverage from `match_stats`
- **What:** middle fallback in the career lookup chain: `player_stats` → **aggregate the player's rows in our own
  `match_stats` table** → surface neutrals.
- **Why:** challenger players mostly lack ATP stats-centre data: all four LEGBAR/WINMAN players fell back to neutrals
  (Baris/Legout/Winter are in `players` but have no usable `player_stats`; Manning absent entirely).
  `match_stats` covers everyone who appeared in any scraped match.
- **Effort:** medium (~50 lines + query design: which matches/surfaces to aggregate, minimum sample).

### C4. Sim consistency + speed refactor — ✅ SUPERSEDED 2026-07-04 by the exact engine
> `trade/exact.py` (Barnett/O'Malley DP) replaced Monte Carlo entirely: ~1000× faster (3ms for a
> 500-draw Bo5 vs 1–4s), zero point-level noise, game/set/match internally consistent by construction.
> Validated against the MC oracle: 10-state grid × 3 outputs, all within 4·SE of 20k sims, 0 failures.
> `simulation.py` retained as the reference oracle. Spec: docs/superpowers/specs/2026-07-04-exact-probability-engine-design.md
> Unlocked follow-ons: per-tick recomputation (interacts with fresh-sim entry gate — needs design), fast backtest sweeps (A5 etc. now run in seconds).
- **What:** simulate ONE full match trajectory per iteration and record game/set/match outcomes from it, instead of
  three independent sims per iteration.
- **Why:** (a) removes the cosmetic inconsistency (set prob ≠ match prob in a deciding set — pure MC noise between
  independent estimators); (b) ~2× faster sims → lower entry latency, headroom for more concurrent matches.
- **Effort:** moderate refactor of `estimate_win_prob`; behavior-neutral in expectation.

---

## D. Investigated and REJECTED (kept here so we don't re-litigate)

| Idea | Verdict | Evidence |
|---|---|---|
| Rolling last-X-points stats (recency window) | **Refuted by backtest** | Sweep on Jul 4 matches: overall Brier 0.390–0.415 vs 0.374 baseline; KHACOB unimproved (its recent-window stats still favored Khachanov — the divergence was clutch/scoreboard, not stale data) |
| Per-set stat decay | Superseded/refuted | Slams don't expose per-set stats; replaced by rolling window → refuted above |
| Break-point (clutch) modeling | Rejected by user; **experimentally confirmed 2026-07-05**: career-differential BP adjustment (shrunk toward tour means −1.5/+1.8, K∈{100,300,∞}) backtested on 2,493 snapshots / 14 matches → overall Brier 0.249→0.250 (nothing); KHACOB unimproved (0.705→0.707); only TIABUB helped (0.103→0.089 — Bublik is the tour's biggest clutch outlier at +3.0 on 333 BPs). Divergence matches are not explained by clutch. | bp_experiment sweep |
| Model-aware stop loss | **Dangerous** while the model can diverge | Every KHACOB stop had model valuing the position 0.64–0.95 vs final price 0.02 — would have held everything to zero |
| PRIOR_N gap-scaling via log-ratio | Worse than flat 40 | Brier 0.224 vs 0.208; misranks Djokovic (ratio understates elite class). Superseded by A3 (absolute difference) |
| Trailing-player (comeback) bias hypothesis | **Tested: inverted** | 1,507 set-imbalanced snapshots: model rates the trailing player *lower* than market (−3.4¢ mean). Real bias = over-trusting the leader's aggregates |
| Trading on set-win probability | Category error | Contract settles on the match; set prob is a timing signal at most, and it was equally wrong in KHACOB |
| 60-total-points entry floor | Superseded | Sub-stats (2nd serve) stay small-sample deep into set 2; damping/career prior handles it continuously |
| Trade-count circuit breaker (stop after N stop-losses per match) | Rejected by user (losses are normal in sports trading) | Replaced in spirit by B2, which triggers on divergence instead of losses |
| Market-anchored priors (tilt career anchors so sim(0-0) matches the pre-match Kalshi price) | **Tested 2026-07-05: worse** — full tilt Brier 0.271, half tilt 0.266 vs flat-40's 0.261 (18 matches). Rescues the blow-up class (FRISON 0.153→0.079, AUGDAV) but destroys matches where our anchors beat the pre-match market (TIABUB 0.103→0.214, DIMBER) — the anchors carry real independent signal. Blow-up insurance is already provided more cheaply by the B2 stand-down. Prior architecture now 4-for-4 vs adversarial sweeps (level, gap-scaling, time-decay, market-anchoring). | market_anchor_validation |

---

## Suggested priority

1. **B2 divergence stand-down** — quantify, then implement. Targets the single biggest recurring loss.
2. **A1 + A2** (underdog floor + scaled exits) — small, together they clean up the penny-entry zone.
3. **B1** serve-aware stop deferral + **B3** stats_ready relaxation — small quality wins.
4. **A3** gap-scaled PRIOR_N — validated, philosophical fit.
5. **C1** feed resilience → **C2** persistence → **C3** challenger careers → **C4** sim refactor.
6. **A4** empirical threshold + momentum test once ~15–20 matches of new-schema data exist.
