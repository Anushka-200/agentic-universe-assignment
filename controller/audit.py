"""Audit CLI: answer "why did workflow X change at time T, who approved it, and what happened next?"

    python -m controller.audit --db state/controller.db --summary
    python -m controller.audit --db state/controller.db --wf nw_renewal                 # every change, with story
    python -m controller.audit --db state/controller.db --wf nw_renewal --at 2027-01-06T12:00
"""
import argparse
import json
import sqlite3

NOISY = ("evaluate_experiment",)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="state/controller.db")
    ap.add_argument("--wf")
    ap.add_argument("--at", help="sim timestamp prefix; shows the change applied at/just before it")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    db = sqlite3.connect(a.db)
    db.row_factory = sqlite3.Row

    if a.summary:
        print("proposals by workflow/kind/status:")
        for r in db.execute("SELECT wf, kind, status, COUNT(*) n FROM proposals GROUP BY 1,2,3 ORDER BY 1,2,3"):
            print(f"  {r['wf']:12} {r['kind']:14} {r['status']:10} {r['n']}")
        dup = db.execute("""SELECT wf, applied_version, COUNT(*) n FROM proposals WHERE status='applied'
                            GROUP BY 1,2 HAVING n>1""").fetchall()
        print("double-applied versions:", [tuple(d) for d in dup] or "none")
        print("still open:", [r[0] for r in db.execute(
            "SELECT proposal_id FROM proposals WHERE status IN ('submitting','pending','approved','applying')")])
        return

    q, args = "SELECT * FROM proposals WHERE status='applied'", []
    if a.wf:
        q += " AND wf=?"; args.append(a.wf)
    if a.at:
        q += " AND updated_ts<=? ORDER BY updated_ts DESC LIMIT 1"; args.append(a.at + "￿")
    else:
        q += " ORDER BY updated_ts"
    for p in db.execute(q, args).fetchall():
        print("=" * 100)
        print(f"{p['wf']} -> config v{p['applied_version']} at {p['updated_ts']}  [{p['kind']}] {p['proposal_id']}")
        print(f"  WHAT : {p['summary']}")
        print(f"  WHY  : {p['rationale']}")
        who = "auto-apply within policy (no human needed)" if p["path"] == "auto" else f"approver, {p['approval_id']}"
        print(f"  WHO  : {who}")
        print("  STORY (proposal timeline):")
        for d in db.execute("SELECT sim_ts, kind, detail FROM decisions WHERE proposal_id=? ORDER BY id",
                            (p["proposal_id"],)):
            print(f"    {d['sim_ts']}  {d['kind']}: {d['detail'][:220]}")
        print("  WHAT HAPPENED NEXT (next 48 sim-hours on this workflow):")
        for d in db.execute("""SELECT sim_ts, kind, detail FROM decisions WHERE wf=? AND sim_ts>? AND
                               sim_ts<=datetime(?, '+2 days') AND kind NOT IN (%s) ORDER BY id LIMIT 12"""
                            % ",".join("?" * len(NOISY)),
                            (p["wf"], p["updated_ts"], p["updated_ts"][:19], *NOISY)):
            print(f"    {d['sim_ts']}  {d['kind']}: {d['detail'][:160]}")


if __name__ == "__main__":
    main()
