"""SQLite audit + state store. One file, no extra infrastructure.

Everything the controller knows lives here, so a restart resumes from it:
  kv          cursors (last chunk processed) and small flags
  stats       aggregated call counts per (workflow, sim hour, config version, dimension, option)
  proposals   every proposal we built, with status lifecycle
  decisions   append-only log: why we did what we did (the audit trail)
"""
import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS stats (
  wf TEXT, hour INTEGER, cfg_version INTEGER, dim TEXT, opt TEXT,
  dials INTEGER, connected INTEGER, engaged INTEGER, halluc INTEGER,
  PRIMARY KEY (wf, hour, cfg_version, dim, opt));
CREATE TABLE IF NOT EXISTS proposals (
  proposal_id TEXT PRIMARY KEY, wf TEXT, kind TEXT, base_version INTEGER,
  config TEXT, summary TEXT, rationale TEXT, path TEXT,      -- path: approval | auto
  approval_id TEXT, status TEXT, reasons TEXT,
  created_ts TEXT, updated_ts TEXT, applied_version INTEGER);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, sim_ts TEXT, wf TEXT, kind TEXT,
  detail TEXT, proposal_id TEXT, wall_ts REAL);
CREATE TABLE IF NOT EXISTS config_events (
  event_id INTEGER PRIMARY KEY, sim_ts TEXT, wf TEXT, version INTEGER, actor TEXT,
  changed_dims TEXT, proposal_id TEXT, approval_id TEXT);
"""


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    # --- kv -----------------------------------------------------------------
    def get(self, k, default=None):
        r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default

    def put(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, json.dumps(v)))

    # --- ingestion: counts + cursor written in ONE transaction (idempotent) ---
    def ingest_chunk(self, chunk_no, rows, cfg_events):
        cur = self.db.cursor()
        cur.execute("BEGIN")
        if (self.get("last_chunk", -1)) >= chunk_no:
            cur.execute("ROLLBACK")
            return False
        cur.executemany(
            """INSERT INTO stats VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(wf,hour,cfg_version,dim,opt) DO UPDATE SET
               dials=dials+excluded.dials, connected=connected+excluded.connected,
               engaged=engaged+excluded.engaged, halluc=halluc+excluded.halluc""",
            rows)
        cur.executemany("INSERT OR IGNORE INTO config_events VALUES (?,?,?,?,?,?,?,?)", cfg_events)
        cur.execute("INSERT OR REPLACE INTO kv VALUES ('last_chunk', ?)", (json.dumps(chunk_no),))
        cur.execute("COMMIT")
        return True

    def arm_stats(self, wf, dim, since_hour, min_version=0):
        """Totals per option for one dimension since `since_hour` (only what actually ran)."""
        q = """SELECT opt, SUM(dials), SUM(connected), SUM(engaged), SUM(halluc) FROM stats
               WHERE wf=? AND dim=? AND hour>=? AND cfg_version>=? GROUP BY opt"""
        return {r[0]: dict(dials=r[1], connected=r[2], engaged=r[3], halluc=r[4])
                for r in self.db.execute(q, (wf, dim, since_hour, min_version))}

    def arm_stats_range(self, wf, dim, h0, h1):
        """Like arm_stats but for hours in [h0, h1), e.g. pre-D0 history."""
        q = """SELECT opt, SUM(dials), SUM(connected), SUM(engaged), SUM(halluc) FROM stats
               WHERE wf=? AND dim=? AND hour>=? AND hour<? GROUP BY opt"""
        return {r[0]: dict(dials=r[1], connected=r[2], engaged=r[3], halluc=r[4])
                for r in self.db.execute(q, (wf, dim, h0, h1))}

    # --- decisions / proposals ---------------------------------------------
    def log(self, sim_ts, wf, kind, detail, proposal_id=None):
        self.db.execute("INSERT INTO decisions (sim_ts,wf,kind,detail,proposal_id,wall_ts) VALUES (?,?,?,?,?,?)",
                        (sim_ts, wf, kind, json.dumps(detail, default=str), proposal_id, time.time()))

    def save_proposal(self, p):
        self.db.execute("""INSERT OR REPLACE INTO proposals VALUES
            (:proposal_id,:wf,:kind,:base_version,:config,:summary,:rationale,:path,
             :approval_id,:status,:reasons,:created_ts,:updated_ts,:applied_version)""", p)

    def proposals(self, wf=None, statuses=None):
        q, a = "SELECT * FROM proposals WHERE 1=1", []
        if wf:
            q += " AND wf=?"; a.append(wf)
        if statuses:
            q += f" AND status IN ({','.join('?' * len(statuses))})"; a += list(statuses)
        cols = [d[0] for d in self.db.execute("SELECT * FROM proposals LIMIT 0").description]
        return [dict(zip(cols, r)) for r in self.db.execute(q + " ORDER BY created_ts", a)]

    def update_proposal(self, pid, **kw):
        sets = ",".join(f"{k}=?" for k in kw)
        self.db.execute(f"UPDATE proposals SET {sets} WHERE proposal_id=?", (*kw.values(), pid))
