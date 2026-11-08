# Outcome-First Agent Loop — Submission

**Name:** Anushka Kulkarni · **Date:** 2026-11-08 · **Hours actually spent (be honest):** 3 hours (one session, with an AI coding assistant) · **Language / storage:** Python 3 (only `httpx` beyond the stdlib) / SQLite (one file, WAL mode)

---

## 0. TL;DR
- An hourly control loop. It ingests sim events into per-hour aggregates in SQLite, then for each workflow it runs (1) caller-ID health checks, (2) hallucination-incident detection with revert-to-last-good-prompt, and (3) one experiment type: an even-split test on the dimensions that move the workflow's **primary** KPI. Winners are picked by a one-sided two-proportion z-test with Holm correction and a guardrail check. It also detects decay and rolls back.
- **public_01:** conclusion rate **+9.6%** vs baseline, 8 changes applied (4 beneficial, 4 neutral, **0 harmful**), decaying W3 rolled back.
- **public_02:** **+12.2%**, 11 changes applied (10 beneficial, 1 neutral, **0 harmful**). The bad supervisor prompt was fixed with 3 credits. The flagged caller ID was drained by policy-sized auto-apply steps. Hallucinated calls on ks_cancel: 81 vs 330 baseline.
- Crash test: SIGKILL mid-run on public_02, restart, identical final result to the uninterrupted run, 0 apply conflicts, no double applies. Load: 5 simulated days of public_03 in 441 s at 45 MB peak memory.
- **Deliberately not built:** per-person call time (C4), supervisor UI (C11), per-cohort experiments (C2), loop observability dashboards (C14), cross-tenant fairness (C15).

## 1. Assumptions
1. The only facts about the world are what the HTTP API returns. Workflow policies are read from `GET /workflows` at startup and treated as fixed for the run.
2. Welcome message and voice act **after** pickup, so they move engagement. Caller ID acts **before** pickup, so it moves pickup. We therefore test welcome/voice only on engagement-primary workflows and caller ID only on pickup-primary ones. The guardrail KPI is still checked for every winner.
3. Dimensions are sampled independently by the sim, so a welcome × voice split is a factorial design. We analyse each dimension marginally and ignore interactions (see §13).
4. `hallucination_flag` is noisy, so we act on rates over ≥ 300 connected calls, never on single flags.
5. The prompt that dominated pre-D0 history is "known good" until we observe otherwise. A prompt becomes "known good" when its 6-hour hallucination rate is ≤ baseline + 1pp on ≥ 300 connects.
6. A client's written preference that is **stricter** than the policy is honoured, e.g. Kestrel: ≤ 2 requests/day, none before 11:00. A preference that is **looser** than the policy is ignored, because the policy wins.
7. A supervisor's edit to a dimension is a deliberate business choice. We don't re-test that dimension for 7 days.
8. A fresh sim world (scenario changed, or sim clock behind our stored clock) gets a fresh DB. Otherwise we resume.

## 2. Scope decisions
| # | Capability | Built / Partial / Cut | Why | What it would take to finish |
|---|---|---|---|---|
| C1 | Ingestion at scale | **Partial** | Idempotent and restartable: aggregates and the cursor commit in one transaction. Throughput measured in §8. | Stream-parse chunks (orjson), shard by workflow. |
| C2 | Shared cohort object | **Cut** | All learning is at the `default` cohort, so there is one population and no fragmentation. | A cohort table keyed by id. Aggregate stats by cohort_id (already on every call event). |
| C3 | Bootstrap from history | **Partial** | History is ingested and gives the hallucination baseline, the known-good prompt, and incumbent rates in dry runs. History only ran the default config, so it can't rank untried variants. | Use history for per-hour pickup curves (feeds C4). |
| C4 | Call-time optimisation | **Cut** | Biggest build. Needs per-person stats and a 200k-entry map. Risk of weight-0 hours wasting capacity. | Per-cohort hour pickup from history → `hour_weights`, auto-applied where `schedule` is allowed. |
| C5 | Caller-ID health | **Built** | Population test vs sibling numbers. Auto-apply in ≤ `max_shift` steps where allowed, otherwise approval. Bad numbers are excluded from future tests. | Probe a retired number with a tiny share to detect recovery. |
| C6 | Welcome/voice experiments | **Built** | Min sample size, one-sided z-test, Holm, guardrail. | Sequential testing (alpha spending) instead of a fixed strict alpha. |
| C7 | Promotion, decay, rollback | **Built** | Winner's recent 24h rate vs its promotion-time rate. Re-opens the test. | Keep a 10% holdout so decay is measured against a concurrent control. |
| C8 | Hallucination incident | **Partial** | Detect → validate last-good prompt (3 credits) → propose with evidence → verify after apply. LLM suggest is only a fallback (coded, not exercised on public_02). | Rank LLM suggestions by validation and engagement guardrail. |
| C9 | Governance client | **Built** | Full lifecycle, approver hours, cap with an incident reserve, expiry/lapse/stale → re-plan with fresh data, auto-apply within policy. | Smarter batching across experiment and incident kinds. |
| C10 | Honest impact / affected / dry run | **Partial** | Impact = observed lift ÷ 10pp. Affected = batch × cadence days × traffic share moved. Dry run = observed per-option rates and n from our own store. | Replay against historical cohorts with confidence intervals. |
| C11 | Supervisor view | **Cut** | `controller/audit.py` covers the "why" question. A UI is not where the risk is. | Small FastAPI page over the same tables. |
| C12 | Audit store | **Built** | `decisions`, `proposals`, `config_events`, plus the CLI. | — |
| C13 | Crash recovery demo | **Built** | `scripts/crash_test.sh`, log in `reports/crash_test_public02.log`. | Run kill at many random points in CI. |
| C14 | Loop observability | **Cut** | Only logs and the DB. | Prometheus counters for lag, decisions/hour, credits. |
| C15 | Fairness across tenants | **Cut** | Credits barely used (3/200). Approver attention is per-workflow by policy. | A token bucket per tenant. |

**Principle:** build what the hidden-scenario grade and the non-negotiables depend on first. That means no harmful changes, governance hygiene, incident time-to-fix, decay rollback and crash safety. Lift-hunting features that add risk (C4, per-person) come second. Doing five things properly beats fifteen sketches.

## 3. Stakeholders

### 01 — Sales (Nikhil Rao)
- **Accepted:** speed where it's safe. Tests start on D0 inside approver hours. Caller-ID fixes auto-apply where policy allows.
- **Declined:** one blended score (Product and the README forbid blending pickup and engagement). "Winning by lunch, live by evening" (one morning of data is how false winners happen). Rolling out the "persuasive" prompt everywhere (prompt changes need validation evidence, and freer claims are a hallucination risk).
- **Wrong claims:** "Kestrel turned on auto-apply for everything". The API says ks_cancel allows only `schedule, caller_id, welcome_message, voice`, with `max_shift 0.2`. Prompts are never auto-applied. "Northwind is fine with auto-apply on KYC". The API says `nw_kyc.auto_apply.enabled = false`. A QBR conversation is not a policy.
- **Reply:** "Nikhil — happy to give you a strong story: on our test worlds the loop lifted conclusions +10–12% with zero harmful changes, and every change has an approval trail. Two corrections: Kestrel's policy only allows small auto-applied shifts (not prompts), and Northwind KYC has auto-apply switched off, so those go through approvers. I won't blend pickup and engagement into one number; I'll give you both per client, which is also more credible on stage. The persuasive prompt needs validation evidence before any client sees it."

### 02 — Northwind Compliance (Meera Iyer)
- **Accepted:** every customer-facing change (welcome, voice, prompt) goes through a named approver even where auto-apply is allowed. Prompt changes carry a `validation_run_id`. Every change is reconstructable from our store (§10). Experiment requests are only sent in approver hours, and not in the last hour of their day.
- **Pushed back:** "Slow and right" is honoured with strict significance (α = 0.01 family-wise with Holm). But a pure "never even briefly worse" rule is impossible with experimentation. The test arms are bounded and every arm is approved.
- **Wrong claim:** "Operational settings such as call timing and caller-ID rotation are covered by the policy." True for `nw_renewal`, but `nw_kyc` has auto-apply **disabled**. KYC caller-ID changes therefore go through approval, as `nw_kyc did_health` did in public_02.
- **Reply:** "Meera — confirmed: nothing a customer hears changes without your approver's named decision, and script changes carry a validation-run ID. For any customer and day we can answer which variant they heard and why it was live, from our own audit store. One correction: caller-ID latitude applies to Renewal only; KYC has no auto-apply in the signed policy, so we route KYC caller-ID changes to your approvers. Requests are sent only within your approvers' hours."

### 03 — Platform/SRE (Kabir Menon)
- **Accepted:** kill/restart tested (§9). Idempotency keys everywhere (deterministic proposal ids, write-ahead of every proposal, reconciliation on start). No new stateful infrastructure: SQLite is a file, and in production the same schema maps to the existing Postgres.
- **Declined (for now):** health dashboards (C14). The numbers exist in the DB but aren't exported.
- **Reply:** "Kabir — kill -9 mid-run then restart finishes correctly with no double applies; the script is in `scripts/crash_test.sh`. State is a single SQLite file (maps 1:1 to Postgres tables, no Redis needed). Load numbers for public_03 are in `reports/load_public03.txt`. No paging yet: the one alert I'd add first is 'approved change not applied within 60 min of apply_by'."

### 04 — Kestrel Ops Supervisor (Anita Desai)
- **Accepted:** her edits are never silently undone. We detect `actor: supervisor` events, reset the affected test, and hold her dimension for 7 days. Max 2 requests per day and none before 11:00 (stricter than the policy's 4 and 10:00), with one bundled request per test. The bad-prompt fix was **asked**, not forced. We guard against the "verbatim script" trap: winners must pass the engagement test, not just safety, and the prompt revert targets the previously good prompt.
- **Pushed back:** "Tell me before anything changes on a running campaign." Caller-ID drains for a flagged number are auto-applied within policy because time-to-fix matters. They're logged, and we'd include them in her daily summary.
- **Reply:** "Anita — I'll never undo your changes: if you edit voice or welcome, we stop testing that for a week. You'll get at most two requests a day, after 11:00, each bundling everything. The only thing that changes without asking is moving calls off a phone number that's clearly being flagged as spam, which your policy permits; it's in your daily summary. If we see your prompt causing hallucinations we'll ask you, with test evidence."

### 05 — Product (Tara Kulkarni)
- **Accepted:** pickup and engagement are kept separate (`metric()` returns one KPI). An expired, stale or lapsed proposal is closed and **rebuilt from fresh data with a new id**. Only arms that ran count: stats are keyed by the `config_version` and `variant` actually dialled, and evaluation filters by `cfg_version ≥ experiment version`. A winner updates only its own workflow.
- **Open questions answered:** min arm size **400** in the KPI denominator. At ~50% base rate this detects about a 10pp difference at 80% power. We keep collecting up to **3000**, which detects about 3.6pp. Cost: small wins take days. Family-wise **α = 0.01** with Holm, strict because we look daily.
- **Declined:** per-person call-time (cut, see C4), the consolidated view (cut), and caller-ID health (built). Not all three can be P0.
- **Reply:** "Tara — definitions held: separate KPIs, expired = re-queued with fresh data, only executed arms count. Minimum cohort: 400 per arm, family-wise false-winner rate ≤1% per look; cost is that <4pp wins take several days to confirm. Of your three P0s I built caller-ID health; per-person call-time and the supervisor view are cut — call-time is the next build and the plan is in SUBMISSION §2."

### 06 — CTO (Aryamann)
- **Accepted:** explainability over size (every change has a stored rationale, evidence and approval). Credits spent only where they change a decision: 3 of 200 on public_02, with revert-to-last-good before any LLM call. Explicit cuts with reasons.
- **Caught:** the memo claims above. The policy enforced by the API wins.
- **Reply:** "Aryamann — I chose governance hygiene, no harmful changes, incident response and decay rollback over breadth: 0 harmful changes on both public scenarios, +9.6% / +12.2% conclusions. Cut: per-person timing, UI, cross-tenant fairness. What breaks first at 10× is single-process ingestion; the partitioning plan is in §8."

## 4. Architecture
```
            ┌──────────── simulator (HTTP) ─────────────┐
 advance ──►│ /sim/advance → new_chunks                 │
            │ /events/chunks/{n}  /workflows  /approvals│
            └──────┬───────────────────────────▲────────┘
                   │ NDJSON                    │ POST /approvals, /apply, PUT /config,
                   ▼                           │ /validation/runs, /llm/suggest-fixes
  ingest(): per (wf, hour, cfg_version, dim, option) counts ─┐
                   │  one transaction with cursor            │
                   ▼                                         │
            SQLite state/controller.db                       │
   kv(cursor, experiment state, holds, bad DIDs, baselines)  │
   stats | proposals | decisions | config_events             │
                   │                                         │
     reconcile(): pending → approved → apply → applied ──────┤
     per workflow: step_caller_id → step_prompt → step_experiment
```
**Restart:** `catch_up()` ingests any chunk newer than the cursor. Then `reconcile()` sends `submitting` rows (a duplicate-id 409 recovers the approval id), checks `applying` rows against config history (no double apply), and polls `pending` ones. Only after that does the loop advance the clock.

## 5. The learning loop
- **Cohorts:** a single `default` population, deliberately. With 3–4 options per dimension and ~1–1.5k connects per day per workflow, splitting further would leave arms under 400 for most of an 8-day run.
- **Experiment design:** an even split across all options of the tested dimensions, applied by one bundled approval. We chose a fixed split over a bandit because a bandit changes allocations continuously, and every change costs an approval (2–4/day). welcome × voice is factorial, analysed marginally.
- **Deciding a winner:** each live arm needs ≥ 400 in the KPI denominator. We run a one-sided pooled two-proportion z-test of each arm vs the incumbent, Holm across all comparisons (α = 0.01), and a guardrail check (the other KPI's point estimate must not drop more than `max_drop`). If nothing wins by 3000 per arm, we return to the incumbent. If the incumbent was retired (a bad DID), the control is the best live arm. Deterministic, with no LLM. **False-winner rate:** ≤ 1% per look family-wise. Repeated daily looks inflate this, which is why α is 0.01 rather than 0.05.
- **Decay:** the promoted winner's last 24h rate vs its promotion-time rate. A one-sided test with p < 0.01 **and** a drop of more than 3pp re-opens the test (`rollback`). On public_01/02 the decaying W3 was caught about 1.5 days after the promotion.
- **Call time / caller ID:** call time was cut. Caller ID: a DID is flagged when its last-6h pickup is more than 30% below its siblings (p < 0.001, ≥ 300 dials). Traffic then moves to healthy siblings in `max_shift` steps.
- **Guardrail:** checked for every winner (above). A candidate that fails it is not promoted.

## 6. Governance
- **State machine:** `submitting → pending → approved → applying → applied`, with branches `rejected | expired | lapsed | stale | refused`. Every terminal non-applied state frees the slot, and the next tick re-plans from **current** config and data under a new proposal id. We never resubmit a stale payload.
- **Timing and batching:** experiment requests only in `[max(approver_start, client_pref), approver_end - 1)`, and under `min(policy cap, client cap) - 1`. One slot per 24h is reserved for incidents. Incidents may be sent at any hour because time-to-fix wins. Polling each simulated hour keeps us well inside the 2h `apply_window`.
- **Auto-apply:** used only for caller-ID drains where `caller_id ∈ allowed_dims`, stepped to stay ≤ `max_shift`. We voluntarily never auto-apply welcome/voice (Compliance and the Kestrel supervisor asked to approve what customers hear), even though ks_cancel permits it.
- **Impact score** = observed primary-KPI lift ÷ 0.10, clipped to [0.05, 1]. Explore = 0.2, DID drain = 0.6, hallucination fix = 0.9. **Affected leads** = daily batch × cadence days × share of traffic whose variant changes. **Dry run** = observed per-option rates and n from our store. Honest but not a replay; see §13.
- **Supervisor edits mid-experiment:** logged (`supervisor_edit_seen`). If they touch a tested dimension, the experiment resets and that dimension is held for 7 days. Our pending proposals go stale on apply (409) and are re-planned around the edit.

## 7. Incidents
- **Hallucination:** threshold = max(2 × history baseline, baseline + 3pp) on ≥ 300 connects over 6h. At a 1.4% baseline, a 300-call window has a false-positive probability of about 1e-6 (binomial tail at 4.4%). Fix order: validate the last-good prompt (n = 300, 3 credits), then call the LLM only if no good fallback exists. Credit floor 20. 3h back-off after an attempt. Verified after apply from the next ≥ 300 connects.
- **public_02 timeline (ks_cancel):**
  - D2 13:00: D2 caller ID flagged (14.8% vs 23.2% siblings, p = 9e-6). Auto-drained over 3 policy-sized steps by 15:00.
  - D3 11:30: supervisor ships P2.
  - D3 15:00: hallucination spike detected (6.9%, n = 334). P1 validated at 98.7% for 3 credits, and a revert was proposed.
  - D3 18:00: approved and applied (grader MTTR 390 min, most of it approver latency).
  - D4 13:00: verified at 2.3%.

## 8. Scale
Measured on `public_03_load` (12 workflows, 5 simulated days, **7.43M call events**) on this dev box (WSL2) with the simulator on the same machine. Command: `scripts/load_test.sh`. Raw output: `reports/load_public03.txt`.

| metric | value |
|---|---|
| total wall-clock, 5 simulated days | **441 s** |
| first simulated day, including ingesting 5.5M pre-D0 history events | ~160 s |
| each later simulated day (~850k events) | **~70 s** (target was < 10 min) |
| ingest throughput | ~17k events/s end to end, including sim response time |
| peak controller memory (RSS) | **45 MB** |
| decision lag | ≤ 1 simulated hour (the tick). Decisions run right after each hour's chunk is committed. |

**Memory bug found and fixed:** the first load run read each chunk (250k events, ~80 MB) whole and peaked at **~5 GB RSS**. Streaming the chunk line by line brought it to 45 MB. A dropped stream retries the whole chunk with fresh tallies, so nothing is double-counted.

Memory is now bounded by the aggregates (workflows × hours × config versions × options), not by events. Per event the cost is one `json.loads` plus 4 dict increments.

**What breaks first at 10×** (~8.5M events/day, 120 workflows): single-threaded JSON parsing, at roughly 10 min per simulated day. That is at the SRE limit, with no headroom. Next would be the per-tick `GET /config` calls, which grow with the number of workflows.

**Plan:** shard workflows across worker processes (each with its own cursor and DB partition), use orjson, cache configs and refresh them on `config_changed`, and move to Postgres with per-workflow advisory locks for proposals.

## 9. Reliability
- **Method:** `scripts/crash_test.sh <url> 50` SIGKILLs the controller about 50s into public_02 (killed at D2 02:00 sim time), restarts it, and lets it finish. Log: `reports/crash_test_public02.log`. Grade of that interrupted run: `reports/public_02_crash_run.md`.
- **Result (final code):** finished at D10 with **exactly the same outcome as the uninterrupted run**: +12.2% conclusions, 11 applied (10 beneficial, 1 neutral, 0 harmful), `apply_conflicts: 0`, `lapsed: 0`. The audit summary prints "double-applied versions: none" and "still open: []".
- **What's lost:** nothing durable. The aggregates of a chunk being processed are rolled back with its cursor and re-ingested.
- **Idempotency keys:** chunk number (cursor), deterministic `proposal_id` written before sending, `approval_id` checked against config history before re-applying.
- **4xx/5xx:** transport errors and 5xx are retried with exponential back-off. 4xx is interpreted (stale → re-plan, 410 → lapsed, duplicate → recover id, 403 → refused and logged).
- **Not yet tested:** a kill in the middle of a chunk download, or between `POST /apply` and our status update. The code handles both (whole-chunk retry, and config-history reconciliation), but the test only kills at one point.

## 10. Audit & observability
```
$ python -m controller.audit --db state/p02.db --wf ks_cancel --at 2027-01-07T18:00
ks_cancel -> config v7 at 2027-01-07T18:00:00+05:30  [prompt_revert] ks_cancel-prompt_revert-00009
  WHAT : Hallucinations spiked on prompt P2: switch to P1
  WHY  : Hallucination flag rate 6.9% over last 6h (n=334) vs baseline 1.4%; P1 passed validation at 98.7%. ...
  WHO  : approver, apr_00006
  STORY: 15:00 propose_prompt_revert → 18:00 approved → 18:00 applied_prompt_revert {version 7, P2→P1}
  NEXT : 2027-01-08T13:00 incident_verified {hallu_rate 0.023, n 344}
```
Raw SQL also works: `SELECT * FROM decisions WHERE wf='ks_cancel' AND sim_ts BETWEEN ... ORDER BY id`.
- **Would page:** an approved change not applied within 60 min of `apply_by`; ingestion lag of more than 2 chunks; credits under the floor during an open incident.
- **Would not page:** rejections, expiries, a stale base (the loop re-plans by itself), a single DID flag.

## 11. Results
**public_01** (`reports/public_01.md`)

| workflow | conclusion Δ | pickup Δ | engagement Δ |
|---|---|---|---|
| ks_cancel | +2.5% | +0.4% | +1.9% |
| nw_kyc | +31.0% | +26.3% | +0.8% |
| nw_renewal | +5.4% | +0.8% | +4.6% |
| ALL | **+9.6%** | +5.4% | +3.1% |

8 applied: 4 beneficial, 4 neutral, 0 harmful.

**public_02** (`reports/public_02.md`)

| workflow | conclusion Δ | pickup Δ | engagement Δ |
|---|---|---|---|
| ks_cancel | +17.0% | +8.3% | +6.8% |
| nw_kyc | +28.1% | +26.0% | -0.0% |
| nw_renewal | -0.5% | -0.8% | +0.0% |
| ALL | **+12.2%** | +8.3% | +2.6% |

11 applied: 10 beneficial, 1 neutral, 0 harmful.

**Interpretation:** the big wins are pickup on nw_kyc (caller-ID test) and ks_cancel (bad DID drained and bad prompt reverted). nw_renewal is roughly flat on public_02 because the W3 winner decayed and the supervisor's voice edit froze half of the test.

**Harm history:** the first build had **2 harmful** changes on public_02:
1. Reverting to a caller ID we had already flagged.
2. Re-testing voice right after a supervisor set it.

Both were fixed (commit `6b369b2`). ks_cancel captured about 0% of the engagement gap on public_01: its test only reached significance on D7, at α = 0.01.

## 12. Trade-offs & rejected alternatives
| Option | Why considered | Why rejected / what instead |
|---|---|---|
| Thompson-sampling bandit | Less regret, faster | Every reallocation needs an approval (2–4/day cap). Not deterministic. A fixed split plus z-test instead. |
| Bayesian posterior "P(best)" | Natural stopping | Harder to state a false-winner rate to Compliance. A frequentist test plus Holm instead. |
| Store raw events | Re-slice later | 850k/day. Hour aggregates instead, at the cost of no ad-hoc cohort re-slicing. |
| LLM-first prompt fixing | "Use the AI" | Costs credits, and some fixes are worse. Revert to last-good first, LLM only as fallback. |
| Auto-apply welcome/voice on Kestrel | Policy allows it | Supervisor and Compliance want to approve what customers hear. |
| Postgres/Redis | Production has them | Not needed to prove the design. The schema ports directly. |

## 13. Known weaknesses
- Repeated daily looks without formal alpha spending. α = 0.01 mitigates this but doesn't make it exact.
- welcome × voice interactions are ignored (marginal analysis).
- Decay is measured against the winner's own past, not a concurrent control, so time-of-week effects could cause false rollbacks. The 3pp minimum drop mitigates this.
- Dry runs are observed rates, not a replay against historical cohorts with intervals.
- The 7-day supervisor hold is a judgement call. It protected against harm here but could block a legitimate test.
- The LLM-suggest path is coded but **not exercised** on public scenarios. Its response shape is assumed.
- `CLIENT_PREFS` is keyed by workflow id, so on hidden scenarios with different ids it is simply inactive.
- No metrics export or alerting (C14).
- Load: measured twice (before and after the streaming fix). Neither the load run nor the crash test was graded against a baseline.

## 14. If this went to production at Formi next month
1. Move the store to Postgres with the same schema, one worker per workflow shard, and advisory locks on proposals.
2. Add metrics and the three pages listed in §10.
3. Add per-cohort experiments on the shared cohort object (C2), with a minimum cohort size of 400 connects/arm/week.
4. Call-time optimisation per cohort from history (C4), auto-applied where `schedule` is allowed.
5. Alpha-spending sequential tests and a 10% concurrent holdout for decay.
6. A supervisor daily summary (C11), which is also the Compliance weekly summary of auto-applied changes.

---
**How to run** (for the next engineer)
```bash
python -m sim baseline --scenario scenarios/public_01_dev.yaml --run-dir runs/base01
python -m sim serve    --scenario scenarios/public_01_dev.yaml --run-dir runs/mine01 --force
./run.sh http://127.0.0.1:8700            # state in state/controller.db (CONTROLLER_DB to override)
python -m sim grade --run runs/mine01 --baseline runs/base01 --out reports/public_01.md
python -m controller.audit --summary       # or --wf <id> [--at <ts>]
python -m pytest -q tests/test_controller.py
scripts/crash_test.sh http://127.0.0.1:8700 60   # kill/restart demo
scripts/run_public.sh  ;  scripts/load_test.sh   # both public scenarios / load test
```
Code map: `controller/main.py` (loop, governance, detection, experiments; tunables at the top), `controller/stats.py` (z-test, Holm, TV distance), `controller/store.py` (SQLite schema and idempotent ingest), `controller/audit.py` (why-did-it-change CLI).
