"""Activity log: every tool call with its tier, taint, decision, result and undo journal. Kept 14 days."""
import json
import sqlite3
import threading
import time

RETENTION_DAYS = 14

SCHEMA = """
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  request_id TEXT,
  tool TEXT NOT NULL,
  args TEXT NOT NULL,
  tier INTEGER,
  tainted INTEGER,
  decision TEXT NOT NULL,
  result TEXT,
  undo TEXT,
  undone INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS activity_ts ON activity(ts);
"""


class Activity:
    def __init__(self, path: str, clock=time.time) -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._clock = clock
        with self._lock:
            self._db.execute("PRAGMA secure_delete = ON")
            self._db.executescript(SCHEMA)
        self.prune()

    def log(self, *, request_id: str, tool: str, args: dict, tier: int, tainted: bool,
            decision: str, result: str = "", undo: list | None = None) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO activity (ts, request_id, tool, args, tier, tainted, decision, result, undo)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (self._clock(), request_id, tool, json.dumps(args), tier, int(tainted), decision, result[:2000],
                 json.dumps(undo) if undo else None))
            return cur.lastrowid

    def last_undoable(self) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM activity WHERE undo IS NOT NULL AND undone = 0 ORDER BY id DESC LIMIT 1").fetchone()

    def mark_undone(self, row_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE activity SET undone = 1 WHERE id = ?", (row_id,))

    def recent(self, n: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    def prune(self) -> None:
        # shortcut: plain DELETE; M2's purge job adds secure_delete + VACUUM
        with self._lock, self._db:
            self._db.execute("DELETE FROM activity WHERE ts < ?", (self._clock() - RETENTION_DAYS * 86400,))
