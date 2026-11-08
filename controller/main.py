"""Outcome-first controller: observe -> detect -> propose -> apply -> watch -> roll back.

One loop iteration = one simulated hour:
  1. advance the sim 60 minutes and ingest new event chunks (idempotent, see store.ingest_chunk)
  2. reconcile our proposals with the approver (apply approved ones, re-plan rejected/expired/stale)
  3. per workflow: incident checks (bad caller ID, hallucination spike), then the experiment state machine

All state lives in SQLite (controller/store.py), so killing and restarting resumes safely.
"""
import argparse
import copy
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta

import httpx

from controller.stats import holm, tv_distance, two_prop_z
from controller.store import Store

log = logging.getLogger("controller")

# ---- tunables (all explained in SUBMISSION.md §5) ----------------------------------------------
TICK_MINUTES = 60
MIN_N = 400            # min denominator per arm before we test at all
MAX_N = 3000           # per-arm cap: if nothing has won by then, keep the incumbent
ALPHA = 0.01           # family-wise alpha per look (strict because we look once a day, repeatedly)
DECAY_P, DECAY_MIN_DROP = 0.01, 0.03
DID_MIN_DIALS, DID_P, DID_REL_DROP = 300, 0.001, 0.30
HALLU_MIN_CONNECTED = 300
VALIDATION_N = 300
CREDIT_FLOOR = 20      # never spend below this; keeps money for the next incident

# Which knobs move which KPI: what a caller hears after pickup moves engagement; the number
# they see before pickup moves pickup.
DIMS_FOR = {"engagement_rate": ["welcome_message", "voice"], "pickup_rate": ["caller_id"]}
# Stricter-than-policy preferences a client asked for in writing (stakeholders/04).
CLIENT_PREFS = {"ks_cancel": {"max_requests_per_24h": 2, "not_before_hour": 11}}
EXPERIMENT_KINDS = ("explore", "promote", "rollback")
OPEN = ("submitting", "pending", "approved", "applying")


def metric(kpi, s):
    """(successes, denominator) for a KPI from an arm's counts. The two KPIs are never blended."""
    if kpi == "engagement_rate":
        return s["engaged"], s["connected"]
    return s["connected"], s["dials"]


def rate(kpi, s):
    k, n = metric(kpi, s)
    return k / n if n else 0.0


class Controller:
    def __init__(self, url, db_path):
        self.http = httpx.Client(base_url=url, timeout=120)
        self.db = Store(db_path)
        self.state = self.call("GET", "/sim/state")
        self.wfs = {w["workflow_id"]: w for w in self.call("GET", "/workflows")}
        d0 = datetime.fromisoformat(self.state["ts"]) - timedelta(minutes=self.state["sim_minute"])
        self.d0 = d0

    # ---- HTTP with retries on transport errors / 5xx; 4xx is returned to the caller ----------
    def call(self, method, path, raw=False, ok=(200,), **kw):
        for attempt in range(6):
            try:
                r = self.http.request(method, path, **kw)
            except httpx.TransportError as e:
                log.warning("transport error %s %s: %s", method, path, e)
                time.sleep(min(2 ** attempt, 30))
                continue
            if r.status_code >= 500:
                time.sleep(min(2 ** attempt, 30))
                continue
            if raw:
                return r
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"{method} {path} failed after retries")

    @property
    def ts(self):
        return self.state["ts"]

    @property
    def hour(self):
        return self.state["sim_minute"] // 60

    def local_hour(self):
        return int(self.ts[11:13])

    # ---- 1. ingestion ---------------------------------------------------------------------
    def catch_up(self):
        last = self.db.get("last_chunk", -1)
        for kind in ("history", "events"):
            for ch in self.call("GET", "/events/chunks", params={"kind": kind, "after": last}):
                if ch["chunk"] > self.db.get("last_chunk", -1):
                    self.ingest(ch["chunk"])

    def ingest(self, n):
        agg = defaultdict(lambda: [0, 0, 0, 0])
        cfg_events = []
        text = self.call("GET", f"/events/chunks/{n}", raw=True).text
        for line in text.splitlines():
            ev = json.loads(line)
            t = ev["type"]
            if t == "call":
                h = int((datetime.fromisoformat(ev["ts"]) - self.d0).total_seconds() // 3600)
                conn, eng = int(ev["connected"]), int(ev["engaged"])
                hal = int(bool(ev.get("qa") and ev["qa"].get("hallucination_flag")))
                for dim, opt in ev["variant"].items():
                    a = agg[(ev["workflow_id"], h, ev["config_version"], dim, opt)]
                    a[0] += 1; a[1] += conn; a[2] += eng; a[3] += hal
            elif t == "config_changed":
                cfg_events.append((ev["event_id"], ev["ts"], ev["workflow_id"], ev["version"], ev["actor"],
                                   json.dumps(ev.get("changed_dims")), ev.get("proposal_id"), ev.get("approval_id")))
        rows = [(*k, *v) for k, v in agg.items()]
        if self.db.ingest_chunk(n, rows, cfg_events):
            for e in cfg_events:
                if e[4] == "supervisor":
                    self.on_supervisor_edit(e[2], e[3], json.loads(e[5]), e[1])

    def on_supervisor_edit(self, wf, version, dims, ts):
        """Supervisor edits are facts. We never revert them silently; we re-plan around them."""
        exp = self.exp(wf)
        touched = set(dims) & set(DIMS_FOR[self.wfs[wf]["objective"]["primary"]] + ["cohorts"])
        self.db.log(ts, wf, "supervisor_edit_seen",
                    {"version": version, "changed_dims": dims, "experiment_phase": exp["phase"],
                     "experiment_reset": bool(touched)})
        if touched and exp["phase"] != "idle":
            self.set_exp(wf, {"phase": "idle"})

    # ---- experiment state (kv) -------------------------------------------------------------
    def exp(self, wf):
        return self.db.get(f"exp:{wf}", {"phase": "idle"})

    def set_exp(self, wf, e):
        self.db.put(f"exp:{wf}", e)

    # ---- 2. governance: submit / reconcile / apply ------------------------------------------
    def requests_last_24h(self, wf):
        cutoff = datetime.fromisoformat(self.ts) - timedelta(hours=24)
        return sum(1 for p in self.db.proposals(wf) if p["path"] == "approval" and p["approval_id"]
                   and datetime.fromisoformat(p["created_ts"]) > cutoff)

    def can_request(self, wf, incident=False):
        g = self.wfs[wf]["governance"]
        pref = CLIENT_PREFS.get(wf, {})
        cap = min(g["max_requests_per_24h"], pref.get("max_requests_per_24h", 99))
        used = self.requests_last_24h(wf)
        if incident:  # incidents may queue outside hours: time-to-fix beats approver convenience
            return used < cap
        start = max(g["approver_hours"][0], pref.get("not_before_hour", 0))
        in_hours = start <= self.local_hour() < g["approver_hours"][1] - 1
        return in_hours and used < cap - 1  # keep one slot free for incidents

    def next_pid(self, wf, kind):
        n = self.db.get("pid_seq", 0) + 1
        self.db.put("pid_seq", n)
        return f"{wf}-{kind}-{n:05d}"

    def open_proposals(self, wf, kinds):
        return [p for p in self.db.proposals(wf, OPEN) if p["kind"] in kinds]

    def submit(self, wf, kind, cfg, summary, rationale, diff, dry_run, impact, affected,
               meta=None, evidence=None, auto=False):
        cur = self.call("GET", f"/workflows/{wf}/config")
        pid = self.next_pid(wf, kind)
        p = dict(proposal_id=pid, wf=wf, kind=kind, base_version=cur["version"], config=json.dumps(cfg),
                 summary=summary, rationale=rationale, path="auto" if auto else "approval", approval_id=None,
                 status="submitting", reasons=None, created_ts=self.ts, updated_ts=self.ts, applied_version=None)
        # write-ahead: the proposal exists in our store BEFORE the sim hears about it
        self.db.save_proposal(p)
        self.db.put(f"meta:{pid}", {"meta": meta or {}, "diff": diff, "dry_run": dry_run, "impact": impact,
                                    "affected": affected, "evidence": evidence})
        self.db.log(self.ts, wf, f"propose_{kind}", {"summary": summary, "rationale": rationale, "diff": diff,
                    "dry_run": dry_run, "impact_score": impact, "affected_leads": affected,
                    "path": p["path"]}, pid)
        self.send(p)

    def send(self, p):
        m = self.db.get(f"meta:{p['proposal_id']}")
        cfg = json.loads(p["config"])
        if p["path"] == "auto":
            body = {"proposal_id": p["proposal_id"], "base_version": p["base_version"], "proposed_config": cfg,
                    "rationale": p["rationale"], "evidence": m["evidence"] or {}}
            r = self.call("PUT", f"/workflows/{p['wf']}/config", raw=True, json=body)
            if r.status_code == 200:
                self.mark_applied(p, self.call("GET", f"/workflows/{p['wf']}/config")["version"])
            else:
                self.close(p, "stale" if "stale" in r.text else "refused", r.text)
            return
        body = {"proposal_id": p["proposal_id"], "workflow_id": p["wf"], "base_version": p["base_version"],
                "proposed_config": cfg, "change_summary": p["summary"], "diff": m["diff"],
                "affected_leads": int(m["affected"]), "dry_run": m["dry_run"],
                "impact_score": round(max(0.0, min(1.0, m["impact"])), 3), "rationale": p["rationale"]}
        if m["evidence"]:
            body["evidence"] = m["evidence"]
        r = self.call("POST", "/approvals", raw=True, json=body)
        if r.status_code == 200:
            self.db.update_proposal(p["proposal_id"], status="pending", approval_id=r.json()["approval_id"],
                                    updated_ts=self.ts)
        elif "duplicate" in r.text:  # we sent it before a crash; recover its approval id
            self.recover_approval_id(p)
        else:
            self.close(p, "stale" if "stale" in r.text else "refused", r.text)

    def recover_approval_id(self, p):
        for a in self.call("GET", "/approvals", params={"workflow_id": p["wf"]}):
            if a["proposal_id"] == p["proposal_id"]:
                self.db.update_proposal(p["proposal_id"], status="pending", approval_id=a["approval_id"],
                                        updated_ts=self.ts)
                return

    def close(self, p, status, reasons=None):
        self.db.update_proposal(p["proposal_id"], status=status, reasons=json.dumps(reasons), updated_ts=self.ts)
        self.db.log(self.ts, p["wf"], f"proposal_{status}", {"reasons": reasons, "kind": p["kind"]},
                    p["proposal_id"])

    def reconcile(self):
        """Drive every open proposal one step. Safe to call any number of times (and after a crash)."""
        for p in self.db.proposals(statuses=OPEN):
            if p["status"] == "submitting":
                if p["path"] == "auto":
                    hist = self.call("GET", f"/workflows/{p['wf']}/config/history")
                    hit = [h for h in hist if h.get("proposal_id") == p["proposal_id"]]
                    self.mark_applied(p, hit[0]["version"]) if hit else self.send(p)
                else:
                    self.send(p)
                continue
            if p["status"] == "applying":
                hist = self.call("GET", f"/workflows/{p['wf']}/config/history")
                hit = [h for h in hist if h.get("approval_id") == p["approval_id"]]
                if hit:
                    self.mark_applied(p, hit[0]["version"])
                    continue
            a = self.call("GET", f"/approvals/{p['approval_id']}")
            st = a["status"]
            if st == "approved" or p["status"] == "applying":
                if p["status"] != "applying":
                    self.db.log(self.ts, p["wf"], "approved", {"approval": a}, p["proposal_id"])
                self.db.update_proposal(p["proposal_id"], status="applying", updated_ts=self.ts)
                r = self.call("POST", f"/workflows/{p['wf']}/apply", raw=True, json={"approval_id": p["approval_id"]})
                if r.status_code == 200:
                    self.mark_applied(p, self.call("GET", f"/workflows/{p['wf']}/config")["version"])
                elif r.status_code == 410:
                    self.close(p, "lapsed", r.text)
                elif "stale" in r.text:
                    self.close(p, "stale", r.text)  # config moved (e.g. supervisor); re-plan from fresh data
                elif "applied" in r.text:
                    self.mark_applied(p, self.call("GET", f"/workflows/{p['wf']}/config")["version"])
                else:
                    self.close(p, "apply_failed", r.text)
            elif st in ("rejected", "expired", "lapsed"):
                self.close(p, st, a.get("reasons"))

    def mark_applied(self, p, version):
        self.db.update_proposal(p["proposal_id"], status="applied", applied_version=version, updated_ts=self.ts)
        meta = self.db.get(f"meta:{p['proposal_id']}")["meta"]
        self.db.log(self.ts, p["wf"], f"applied_{p['kind']}", {"version": version, "meta": meta}, p["proposal_id"])
        wf, kind = p["wf"], p["kind"]
        if kind in ("explore", "rollback"):
            self.set_exp(wf, {"phase": "exploring", "version": version, "start_hour": self.hour,
                              "incumbent": meta["incumbent"]})
        elif kind == "promote":
            self.set_exp(wf, {"phase": "promoted", "version": version, "start_hour": self.hour,
                              "winners": meta["winners"]})
        elif kind == "prompt_revert":
            self.db.put(f"verify_prompt:{wf}", {"prompt": meta["to"], "since_hour": self.hour})

    # ---- helpers for proposal content ---------------------------------------------------------
    def current(self, wf):
        return self.call("GET", f"/workflows/{wf}/config")

    def affected(self, wf, old_cfg, new_cfg, dims):
        """Leads whose experience changes: ~3 days of batches x share of traffic that moves."""
        w = self.wfs[wf]
        shift = max([tv_distance(old_cfg["allocations"]["default"].get(d, {}),
                                 new_cfg["allocations"]["default"].get(d, {})) for d in dims] or [1.0])
        return int(w["daily_batch"]["size"] * len(w["cadence"]) * shift)

    def observed(self, wf, dim, since_hour=-10_000, min_version=0):
        kpi = self.wfs[wf]["objective"]["primary"]
        return {o: {"n": metric(kpi, s)[1], "rate": round(rate(kpi, s), 4)}
                for o, s in self.db.arm_stats(wf, dim, since_hour, min_version).items()}

    # ---- 3a. experiment state machine -----------------------------------------------------------
    def step_experiment(self, wf):
        if self.open_proposals(wf, EXPERIMENT_KINDS):
            return
        exp = self.exp(wf)
        if exp["phase"] == "idle":
            self.propose_explore(wf, "explore")
        elif exp["phase"] == "exploring":
            self.evaluate_exploration(wf, exp)
        elif exp["phase"] == "promoted":
            self.check_decay(wf, exp)

    def propose_explore(self, wf, kind, reason="start experiment"):
        if not self.can_request(wf):
            return
        w, cur = self.wfs[wf], self.current(wf)
        cfg = copy.deepcopy(cur["config"])
        dims = DIMS_FOR[w["objective"]["primary"]]
        bad = set(self.db.get(f"bad_did:{wf}", []))
        incumbent, diff = {}, {}
        for d in dims:
            old = cfg["allocations"]["default"].get(d, {})
            incumbent[d] = max(old, key=old.get) if old else w["options"][d][0]
            opts = [o for o in w["options"][d] if not (d == "caller_id" and o in bad)]
            cfg["allocations"]["default"][d] = {o: round(1 / len(opts), 6) for o in opts}
            diff[f"allocations.default.{d}"] = {"from": old, "to": cfg["allocations"]["default"][d]}
        if cfg == cur["config"]:
            self.set_exp(wf, {"phase": "exploring", "version": cur["version"], "start_hour": self.hour,
                              "incumbent": incumbent})
            return
        dry = {"method": "observed rates of each option in our own store (history + live); options never run show n=0",
               "primary_kpi": w["objective"]["primary"], "per_dim": {d: self.observed(wf, d) for d in dims}}
        self.submit(wf, kind, cfg,
                    summary=f"Fair test: split traffic evenly across {', '.join(dims)} options to learn which works best",
                    rationale=(f"{reason}. Even split so every option reaches {MIN_N}+ samples quickly; a winner is "
                               f"promoted only after a one-sided two-proportion z-test with Holm correction "
                               f"(alpha={ALPHA}) and a guardrail check."),
                    diff=diff, dry_run=dry, impact=0.2, affected=self.affected(wf, cur["config"], cfg, dims),
                    meta={"incumbent": incumbent})

    def evaluate_exploration(self, wf, exp):
        w = self.wfs[wf]
        kpi = w["objective"]["primary"]
        guard_kpi, max_drop = next(iter(w["objective"]["guardrail"].items()))
        guard_kpi = guard_kpi.replace("_max_drop", "")
        dims = DIMS_FOR[kpi]
        pvals, arms, ready, capped = {}, {}, True, True
        for d in dims:
            st = self.db.arm_stats(wf, d, exp["start_hour"], exp["version"])
            arms[d] = st
            inc = exp["incumbent"][d]
            alloc = self.current(wf)["config"]["allocations"]["default"].get(d, {})
            live = [o for o, wgt in alloc.items() if wgt > 0]
            ns = [metric(kpi, st[o])[1] if o in st else 0 for o in live]
            if not ns or min(ns) < MIN_N or inc not in st:
                ready = False
            if not ns or min(ns) < MAX_N:
                capped = False
            if inc not in st:
                continue
            for o in live:
                if o != inc and o in st and metric(kpi, st[o])[1] >= MIN_N and metric(kpi, st[inc])[1] >= MIN_N:
                    pvals[(d, o)] = two_prop_z(*metric(kpi, st[inc]), *metric(kpi, st[o]))
        if not ready:
            return
        sig = holm({f"{d}:{o}": p for (d, o), p in pvals.items()}, ALPHA)
        winners, evidence = {}, {}
        for d in dims:
            inc = exp["incumbent"][d]
            cands = [o for o in arms[d] if f"{d}:{o}" in sig]
            # guardrail: the winner must not hurt the other KPI by more than the allowed drop
            ok = [o for o in cands if rate(guard_kpi, arms[d][inc]) - rate(guard_kpi, arms[d][o]) <= max_drop]
            pick = max(ok, key=lambda o: (rate(kpi, arms[d][o]), o)) if ok else inc
            k, n = metric(kpi, arms[d][pick])
            winners[d] = {"opt": pick, "succ": k, "n": n, "rate": k / n if n else 0}
            evidence[d] = {o: {"n": metric(kpi, s)[1], "rate": round(rate(kpi, s), 4),
                               "guard_rate": round(rate(guard_kpi, s), 4),
                               "p_vs_incumbent": round(pvals.get((d, o), 1.0), 6),
                               "significant_after_holm": f"{d}:{o}" in sig,
                               "guardrail_ok": o in ok or o == inc} for o, s in arms[d].items()}
        found = any(winners[d]["opt"] != exp["incumbent"][d] for d in dims)
        self.db.log(self.ts, wf, "evaluate_experiment", {"winners": winners, "evidence": evidence,
                                                         "decided": found or capped})
        if not (found or capped) or not self.can_request(wf):
            return
        cur = self.current(wf)
        cfg = copy.deepcopy(cur["config"])
        diff = {}
        for d in dims:
            old = cfg["allocations"]["default"].get(d, {})
            cfg["allocations"]["default"][d] = {winners[d]["opt"]: 1.0}
            diff[f"allocations.default.{d}"] = {"from": old, "to": {winners[d]["opt"]: 1.0}}
        lift = max(winners[d]["rate"] - rate(kpi, arms[d][exp["incumbent"][d]]) for d in dims)
        summ = ", ".join(f"{d}={winners[d]['opt']}" for d in dims)
        self.submit(wf, "promote", cfg,
                    summary=f"Promote test result to all traffic: {summ}" if found else
                    f"Test inconclusive after {MAX_N}+ samples per option: return to {summ}",
                    rationale=("Winner beat the incumbent with Holm-corrected one-sided z-test and passed the "
                               "guardrail." if found else "No option beat the incumbent; stop spending traffic."),
                    diff=diff, dry_run={"method": "observed rates during the test (only arms that actually ran)",
                                        "evidence": evidence},
                    impact=min(1.0, max(0.05, lift / 0.10)),
                    affected=self.affected(wf, cur["config"], cfg, dims), meta={"winners": winners})

    def check_decay(self, wf, exp):
        kpi = self.wfs[wf]["objective"]["primary"]
        for d, win in exp["winners"].items():
            st = self.db.arm_stats(wf, d, max(exp["start_hour"], self.hour - 24), exp["version"]).get(win["opt"])
            if not st or metric(kpi, st)[1] < MIN_N or win["n"] == 0:
                continue
            k, n = metric(kpi, st)
            p = two_prop_z(k, n, win["succ"], win["n"])  # H1: promoted-time rate > recent rate
            drop = win["rate"] - k / n
            if p < DECAY_P and drop > DECAY_MIN_DROP:
                self.db.log(self.ts, wf, "decay_detected", {"dim": d, "opt": win["opt"], "rate_at_promotion":
                            win["rate"], "recent_rate": k / n, "recent_n": n, "p": p})
                self.propose_explore(wf, "rollback",
                                     reason=f"{d}={win['opt']} decayed from {win['rate']:.3f} to {k / n:.3f} "
                                            f"(n={n}, p={p:.4f}); re-open the test")
                return

    # ---- 3b. caller-ID health ---------------------------------------------------------------
    def step_caller_id(self, wf):
        if self.open_proposals(wf, ("did_health",)):
            return
        w, cur = self.wfs[wf], self.current(wf)
        alloc = cur["config"]["allocations"]["default"].get("caller_id", {})
        recent = self.db.arm_stats(wf, "caller_id", self.hour - 6)
        bad = set(self.db.get(f"bad_did:{wf}", []))
        newly = []
        for o, s in recent.items():
            if alloc.get(o, 0) <= 0 or s["dials"] < DID_MIN_DIALS or o in bad:
                continue
            rest = [r for k, r in recent.items() if k != o and k not in bad]
            rk, rn = sum(r["connected"] for r in rest), sum(r["dials"] for r in rest)
            if rn < DID_MIN_DIALS:
                continue
            p = two_prop_z(s["connected"], s["dials"], rk, rn)  # H1: siblings pick up more
            if p < DID_P and s["connected"] / s["dials"] < (1 - DID_REL_DROP) * rk / rn:
                newly.append(o)
                self.db.log(self.ts, wf, "caller_id_degraded", {"did": o, "pickup": s["connected"] / s["dials"],
                            "dials": s["dials"], "siblings_pickup": rk / rn, "p": p})
        bad |= set(newly)
        self.db.put(f"bad_did:{wf}", sorted(bad))
        if not bad & {o for o, v in alloc.items() if v > 0}:
            return
        healthy = [o for o, v in alloc.items() if v > 0 and o not in bad] or \
                  [o for o in w["options"]["caller_id"] if o not in bad]
        if not healthy:
            return
        tot = sum(alloc.get(o, 0) for o in healthy) or len(healthy)
        target = {o: (alloc.get(o, 0) or 1) / tot for o in healthy}
        s = sum(target.values())
        target = {o: v / s for o, v in target.items()}
        g = w["governance"]
        auto = g["auto_apply"]["enabled"] and "caller_id" in g["auto_apply"]["allowed_dims"]
        new = target
        if auto:  # step toward the target without exceeding the policy's max_shift per change
            tv = tv_distance(alloc, target)
            f = min(1.0, (g["auto_apply"]["max_shift"] - 1e-6) / tv) if tv else 1.0
            keys = set(alloc) | set(target)
            new = {o: round(alloc.get(o, 0) * (1 - f) + target.get(o, 0) * f, 6) for o in keys}
            new = {o: v for o, v in new.items() if v > 1e-9}
        elif not self.can_request(wf, incident=True):
            return
        cfg = copy.deepcopy(cur["config"])
        cfg["allocations"]["default"]["caller_id"] = new
        self.submit(wf, "did_health", cfg,
                    summary=f"Move calls away from degraded caller ID(s) {sorted(bad)}",
                    rationale=(f"Pickup on {sorted(bad)} is >{int(DID_REL_DROP * 100)}% below sibling numbers over the "
                               f"last 6h (p<{DID_P}, >= {DID_MIN_DIALS} dials)."),
                    diff={"allocations.default.caller_id": {"from": alloc, "to": new}},
                    dry_run={"method": "last 6h pickup per caller ID", "per_did":
                             {o: round(s["connected"] / s["dials"], 4) for o, s in recent.items() if s["dials"]}},
                    impact=0.6, affected=self.affected(wf, cur["config"], cfg, ["caller_id"]),
                    meta={"bad": sorted(bad)}, auto=auto)

    # ---- 3c. hallucination incident ---------------------------------------------------------
    def hallu_rate(self, wf, prompt, since_hour, until_hour=None):
        st = self.db.arm_stats(wf, "prompt_version", since_hour).get(prompt) if until_hour is None else \
            self.db.arm_stats_range(wf, "prompt_version", since_hour, until_hour).get(prompt)
        return (st["halluc"] / st["connected"], st["connected"]) if st and st["connected"] else (0.0, 0)

    def step_prompt(self, wf):
        cur = self.current(wf)
        prompt = cur["config"]["prompt_version"]
        base = self.db.get(f"hallu_base:{wf}")
        if base is None:  # baseline from pre-D0 history under the business default
            st = self.db.arm_stats_range(wf, "prompt_version", -100_000, 0)
            c = sum(s["connected"] for s in st.values())
            base = sum(s["halluc"] for s in st.values()) / c if c else 0.02
            self.db.put(f"hallu_base:{wf}", base)
            self.db.put(f"good_prompt:{wf}", max(st, key=lambda o: st[o]["connected"]) if st else prompt)
        r, n = self.hallu_rate(wf, prompt, self.hour - 6)
        threshold = max(2 * base, base + 0.03)
        ver = self.db.get(f"verify_prompt:{wf}")
        if ver and ver["prompt"] == prompt and n >= HALLU_MIN_CONNECTED:
            self.db.log(self.ts, wf, "incident_verified" if r < threshold else "incident_fix_not_working",
                        {"prompt": prompt, "hallu_rate": r, "n": n, "threshold": threshold})
            self.db.put(f"verify_prompt:{wf}", None)
        if n >= HALLU_MIN_CONNECTED and r <= base + 0.01:
            self.db.put(f"good_prompt:{wf}", prompt)
        if n < HALLU_MIN_CONNECTED or r < threshold or self.open_proposals(wf, ("prompt_revert",)):
            return
        if self.db.get(f"prompt_incident_hour:{wf}", -999) > self.hour - 3:
            return  # back-off so a rejection doesn't make us burn credits every hour
        self.db.put(f"prompt_incident_hour:{wf}", self.hour)
        self.db.log(self.ts, wf, "hallucination_spike", {"prompt": prompt, "rate": r, "n": n, "baseline": base,
                                                         "threshold": threshold})
        g = self.wfs[wf]["governance"]
        candidates = [self.db.get(f"good_prompt:{wf}")]
        if candidates[0] == prompt:  # no known-good fallback: only now ask the LLM (3 credits)
            if self.call("GET", "/sim/state")["credits_remaining"] < CREDIT_FLOOR + 3:
                return
            r_ = self.call("POST", "/llm/suggest-fixes", raw=True,
                           json={"workflow_id": wf, "problem": f"hallucination rate {r:.1%} on {prompt}"})
            sugg = r_.json() if r_.status_code == 200 else {}
            candidates = [s.get("prompt_version") for s in (sugg.get("suggestions") or sugg.get("fixes") or [])]
            self.db.log(self.ts, wf, "llm_suggest", {"response": sugg})
        best = None
        for cand in [c for c in candidates if c and c != prompt]:
            if self.call("GET", "/sim/state")["credits_remaining"] < CREDIT_FLOOR + VALIDATION_N / 100:
                self.db.log(self.ts, wf, "credit_floor_reached", {"candidate": cand})
                break
            v = self.call("POST", "/validation/runs", raw=True,
                          json={"workflow_id": wf, "prompt_version": cand, "n": VALIDATION_N})
            if v.status_code != 200:
                continue
            v = v.json()
            self.db.log(self.ts, wf, "validation_run", v)
            if v["pass_rate"] >= g["min_validation_pass_rate"] and (best is None or v["pass_rate"] > best["pass_rate"]):
                best = v
        if not best or not self.can_request(wf, incident=True):
            return
        cfg = copy.deepcopy(cur["config"])
        cfg["prompt_version"] = best["prompt_version"]
        self.submit(wf, "prompt_revert", cfg,
                    summary=f"Hallucinations spiked on prompt {prompt}: switch to {best['prompt_version']}",
                    rationale=(f"Hallucination flag rate {r:.1%} over last 6h (n={n}) vs baseline {base:.1%}; "
                               f"{best['prompt_version']} passed validation at {best['pass_rate']:.1%}. This changes a "
                               f"prompt a supervisor may have set; we are asking, not overriding."),
                    diff={"prompt_version": {"from": prompt, "to": best["prompt_version"]}},
                    dry_run={"validation": best, "current_hallu_rate": r, "n": n},
                    impact=0.9, affected=self.affected(wf, cur["config"], cfg, []),
                    meta={"from": prompt, "to": best["prompt_version"]},
                    evidence={"validation_run_id": best["validation_run_id"]})

    # ---- main loop ------------------------------------------------------------------------
    def run(self):
        self.catch_up()
        self.reconcile()
        while not self.state["finished"]:
            r = self.call("POST", "/sim/advance", raw=True, json={"minutes": TICK_MINUTES})
            if r.status_code == 409:
                break
            self.state = r.json()
            for ch in self.state["new_chunks"]:
                if ch["chunk"] > self.db.get("last_chunk", -1):
                    self.ingest(ch["chunk"])
            self.reconcile()
            for wf in self.wfs:
                self.step_caller_id(wf)
                self.step_prompt(wf)
                self.step_experiment(wf)
            self.db.put("last_sim_minute", self.state["sim_minute"])
            if self.state["sim_minute"] % 1440 == 0:
                log.info("%s day done, credits=%s", self.ts, self.call("GET", "/sim/state")["credits_remaining"])
        self.reconcile()
        log.info("scenario finished at %s", self.ts)


def open_store_for_world(db_path, url):
    """Reuse the DB only if it belongs to this same simulated world; a fresh sim means a fresh DB."""
    st = httpx.get(f"{url}/sim/state", timeout=60).json()
    if os.path.exists(db_path):
        s = Store(db_path)
        same = s.get("scenario") == st["scenario"] and s.get("last_sim_minute", 0) <= st["sim_minute"] \
            and (s.get("last_sim_minute", 0) > 0 or st["sim_minute"] == 0)
        s.db.close()
        if not same:
            os.rename(db_path, f"{db_path}.{int(time.time())}.old")
    s = Store(db_path)
    s.put("scenario", st["scenario"])
    s.db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--db", default="state/controller.db")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.makedirs(os.path.dirname(a.db) or ".", exist_ok=True)
    open_store_for_world(a.db, a.url)
    Controller(a.url, a.db).run()


if __name__ == "__main__":
    main()
