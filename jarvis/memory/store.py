"""14-day memory: episodes, activities and facts in SQLite, keyword search, and a purge that really deletes.

Pinned facts never expire. Everything else gets expires_at = created_at + 14 days and is removed by purge(),
which uses secure_delete, FTS secure-delete, a WAL checkpoint and VACUUM so deleted text leaves the file.
"""
import datetime
import json
import math
import re
import sqlite3
import threading
import time

DAY = 86400
RETENTION = 14 * DAY
PURGE_HOUR, PURGE_MINUTE = 3, 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('episode', 'activity', 'fact')),
  text TEXT NOT NULL,              -- episode: Naman's message; activity/fact: the statement
  reply TEXT,                      -- episode: Jarvis's answer
  tainted INTEGER NOT NULL DEFAULT 0,
  pinned INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  expires_at REAL,                 -- NULL only when pinned
  CHECK ((pinned = 1) = (expires_at IS NULL))
);
CREATE INDEX IF NOT EXISTS memories_exp ON memories(expires_at);
CREATE INDEX IF NOT EXISTS memories_created ON memories(created_at);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  text, content='', contentless_delete=1, tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text || ' ' || coalesce(new.reply, ''));
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  DELETE FROM memories_fts WHERE rowid = old.id;
END;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS purge_log (id INTEGER PRIMARY KEY, ran_at REAL, counts_json TEXT,
                                      bytes_before INTEGER, bytes_after INTEGER);
"""

STOPWORDS = set("""a an and are at be did do does for from had has have how i in is it me my of on or
the to was were what when where which who why with you your about this that last week today yesterday
kya kab kaha maine mera meri hai tha the ko ka ki ke se kiya kia aaj kal""".split())


class MemoryStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA secure_delete = ON")  # deleted rows are overwritten with zeros
            self._db.execute("PRAGMA busy_timeout = 5000")
            self._db.executescript(SCHEMA)
            self._db.execute("INSERT INTO memories_fts(memories_fts, rank) VALUES ('secure-delete', 1)")

    # --- write ---
    def add(self, kind: str, text: str, *, reply: str | None = None, tainted: bool = False,
            pinned: bool = False, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO memories (kind, text, reply, tainted, pinned, created_at, expires_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (kind, text, reply, int(tainted), int(pinned), now, None if pinned else now + RETENTION))
            return cur.lastrowid

    def set_pinned(self, mem_id: int, pinned: bool, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            cur = self._db.execute("UPDATE memories SET pinned = ?, expires_at = ? WHERE id = ?",
                                   (int(pinned), None if pinned else now + RETENTION, mem_id))
            return cur.rowcount == 1

    def delete(self, mem_id: int) -> bool:
        with self._lock:
            return self._db.execute("DELETE FROM memories WHERE id = ?", (mem_id,)).rowcount == 1

    # --- read ---
    def pinned(self) -> list[dict]:
        return self._rows("SELECT * FROM memories WHERE pinned = 1 ORDER BY created_at")

    def facts(self) -> list[dict]:
        return self._rows("SELECT * FROM memories WHERE kind = 'fact' ORDER BY pinned DESC, created_at DESC")

    def between(self, start: float, end: float, limit: int = 20) -> list[dict]:
        return self._rows("SELECT * FROM memories WHERE created_at >= ? AND created_at < ? AND pinned = 0"
                          " ORDER BY created_at DESC LIMIT ?", (start, end, limit))

    def search(self, text: str, limit: int = 8, now: float | None = None,
               start: float | None = None, end: float | None = None) -> list[dict]:
        """Keyword search (BM25) with a recency boost: score x (1 + 0.5 e^(-age_days / 7))."""
        # shortcut: keyword-only; vector search waits for M3, when the embedding model is loaded anyway
        words = [w for w in re.findall(r"\w+", text.lower()) if len(w) > 2 and w not in STOPWORDS]
        if not words:
            return []
        query = " OR ".join(f'"{w}"' for w in dict.fromkeys(words))
        now = time.time() if now is None else now
        sql = ("SELECT m.*, -bm25(memories_fts) AS score FROM memories_fts JOIN memories m ON m.id = memories_fts.rowid"
               " WHERE memories_fts MATCH ?")
        args: list = [query]
        if start is not None:
            sql += " AND m.created_at >= ? AND m.created_at < ?"
            args += [start, end]
        rows = self._rows(sql + " LIMIT 50", args)
        for r in rows:
            r["score"] *= 1 + 0.5 * math.exp(-(now - r["created_at"]) / DAY / 7)
        return sorted(rows, key=lambda r: r["score"], reverse=True)[:limit]

    def browse(self, query: str = "", limit: int = 200) -> list[dict]:
        if query.strip():
            return self.search(query, limit=limit)
        return self._rows("SELECT * FROM memories ORDER BY pinned DESC, created_at DESC LIMIT ?", (limit,))

    def _rows(self, sql: str, args=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    # --- forgetting ---
    def purge_due(self, now: float | None = None) -> bool:
        """Due if never run, if the last run is > 24 h old (laptop slept), or if today's 03:30 has passed."""
        now = time.time() if now is None else now
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = 'last_purge_at'").fetchone()
        if row is None:
            return True
        last = float(row["value"])
        today = datetime.datetime.fromtimestamp(now).replace(hour=PURGE_HOUR, minute=PURGE_MINUTE, second=0,
                                                              microsecond=0).timestamp()
        return now - last >= DAY or (now >= today > last)

    def purge(self, now: float | None = None, extra_tables: tuple[tuple[str, str], ...] = ()) -> dict:
        """Delete everything past expiry, then make sure it's gone from the file. Returns counts."""
        now = time.time() if now is None else now
        counts = {}
        with self._lock:
            before = self._size()
            self._db.execute("BEGIN IMMEDIATE")
            try:
                counts["memories"] = self._db.execute(
                    "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,)).rowcount
                for table, ts_col in extra_tables:  # e.g. the activity log, kept 14 days by timestamp
                    counts[table] = self._db.execute(
                        f"DELETE FROM {table} WHERE {ts_col} <= ?", (now - RETENTION,)).rowcount
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._db.execute("VACUUM")
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            left = self._db.execute("SELECT COUNT(*) FROM memories WHERE expires_at IS NOT NULL AND expires_at <= ?",
                                    (now,)).fetchone()[0]
            if left:
                raise RuntimeError(f"purge verification failed: {left} expired memories remain")
            self._db.execute("INSERT INTO purge_log (ran_at, counts_json, bytes_before, bytes_after) VALUES (?,?,?,?)",
                             (now, json.dumps(counts), before, self._size()))
            self._db.execute("DELETE FROM purge_log WHERE ran_at <= ?", (now - RETENTION,))
            self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_purge_at', ?)", (str(now),))
        return counts

    def forget_everything(self, include_pinned: bool = False, extra_tables: tuple[str, ...] = ()) -> int:
        with self._lock:
            n = self._db.execute("DELETE FROM memories" + ("" if include_pinned else " WHERE pinned = 0")).rowcount
            for table in extra_tables:
                self._db.execute(f"DELETE FROM {table}")
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._db.execute("VACUUM")
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return n

    def meta(self, key: str, value: str | None = None) -> str | None:
        """Read (value=None) or write a small setting such as the last daily-brief date."""
        with self._lock:
            if value is not None:
                self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
                return value
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _size(self) -> int:
        page = self._db.execute("PRAGMA page_count").fetchone()[0]
        return page * self._db.execute("PRAGMA page_size").fetchone()[0]
