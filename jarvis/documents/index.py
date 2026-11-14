"""Document index: chunks of Naman's files with keyword (FTS5) and meaning (embeddings) search.

The index mirrors files on disk, so it doesn't follow the 14-day rule: a file deleted or moved out of a
watched folder is removed from the index on the next scan.
"""
import hashlib
import os
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from ..policy.paths import PolicyError, resolve
from . import extract

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL,
  mtime REAL NOT NULL,
  indexed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page INTEGER,
  text TEXT NOT NULL,
  embedding BLOB
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(document_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text, content='', contentless_delete=1, tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  DELETE FROM chunks_fts WHERE rowid = old.id;
END;
"""

SKIP_DIRS = {"node_modules", "venv", ".venv", "__pycache__", "build", "dist", "target", "Pods", "DerivedData"}
BATCH, PAUSE = 20, 2.0
Embed = Callable[[list[str]], list[list[float]]]


def on_battery_low(threshold: int = 30) -> bool:
    out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+)%;\s*(discharging)", out)
    return bool(m and int(m.group(1)) < threshold)


class DocIndex:
    def __init__(self, path: str, embed: Embed | None = None) -> None:
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self.embed = embed  # None -> keyword search only
        with self._lock:
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA foreign_keys = ON")
            self._db.execute("PRAGMA busy_timeout = 5000")
            self._db.executescript(SCHEMA)

    # --- indexing ---
    def scan(self, folders: list[str], cfg: dict, *, force: bool = False, stop=None) -> dict:
        """Index new/changed files, drop deleted ones. Skips heavy work on low battery unless forced."""
        if not force and on_battery_low():
            return {"skipped": "battery below 30% and not charging"}
        seen, stats, todo = set(), {"indexed": 0, "unchanged": 0, "removed": 0, "failed": 0}, []
        kinds = extract.DOC_EXT if cfg["documents"].get("include_code") else extract.DOC_EXT - extract.CODE_EXT
        for folder in folders:
            try:
                root = resolve(folder, cfg, must_exist=True)
            except PolicyError:
                continue  # missing or outside the allowed folders
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
                for name in filenames:
                    p = Path(dirpath) / name
                    if name.startswith(".") or p.suffix.lower() not in kinds:
                        continue
                    try:
                        resolve(str(p), cfg)  # deny rules (secrets etc.) apply to indexing too
                    except PolicyError:
                        continue
                    seen.add(str(p))
                    todo.append(p)
        for i, p in enumerate(todo):
            if stop is not None and stop.is_set():
                break
            result = self.index_file(p)
            stats[result] += 1
            if result == "indexed" and stats["indexed"] % BATCH == 0:
                time.sleep(PAUSE)  # fanless Mac: let it cool between batches
        with self._lock:
            known = [r["path"] for r in self._db.execute("SELECT path FROM documents")]
        for path in known:
            if path not in seen:
                self.remove(path)
                stats["removed"] += 1
        return stats

    def index_file(self, path: Path) -> str:
        try:
            st = path.stat()
            with self._lock:
                row = self._db.execute("SELECT id, mtime, sha256 FROM documents WHERE path = ?", (str(path),)).fetchone()
            if row and row["mtime"] == st.st_mtime:
                return "unchanged"
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if row and row["sha256"] == digest:
                with self._lock:
                    self._db.execute("UPDATE documents SET mtime = ? WHERE id = ?", (st.st_mtime, row["id"]))
                return "unchanged"
            pieces = [c for c in extract.chunks(extract.pages(path)) if c[1].strip()]
            vectors = self.embed([t for _, t in pieces]) if self.embed and pieces else [None] * len(pieces)
        except Exception:
            return "failed"
        with self._lock:
            self._db.execute("BEGIN")
            self._db.execute("DELETE FROM documents WHERE path = ?", (str(path),))
            doc_id = self._db.execute("INSERT INTO documents (path, sha256, mtime, indexed_at) VALUES (?,?,?,?)",
                                      (str(path), digest, st.st_mtime, time.time())).lastrowid
            self._db.executemany(
                "INSERT INTO chunks (document_id, page, text, embedding) VALUES (?,?,?,?)",
                [(doc_id, page, text, None if v is None else np.asarray(v, np.float32).tobytes())
                 for (page, text), v in zip(pieces, vectors)])
            self._db.execute("COMMIT")
        return "indexed"

    def remove(self, path: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM documents WHERE path = ?", (path,))

    # --- search ---
    def search(self, query: str, limit: int = 6) -> list[dict]:
        """Hybrid: BM25 top 20 + vector top 20, merged with reciprocal rank fusion (k = 60)."""
        ranked: dict[int, float] = {}
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
        if words:
            with self._lock:
                rows = self._db.execute(
                    "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT 20",
                    (" OR ".join(f'"{w}"' for w in dict.fromkeys(words)),)).fetchall()
            for rank, r in enumerate(rows):
                ranked[r[0]] = ranked.get(r[0], 0) + 1 / (60 + rank)
        if self.embed:
            # shortcut: brute-force cosine in numpy, fine to ~50k chunks; move to sqlite-vec beyond that
            with self._lock:
                rows = self._db.execute("SELECT id, embedding FROM chunks WHERE embedding IS NOT NULL").fetchall()
            if rows:
                q = np.asarray(self.embed([query])[0], np.float32)
                m = np.frombuffer(b"".join(r["embedding"] for r in rows), np.float32).reshape(len(rows), -1)
                sims = m @ q / (np.linalg.norm(m, axis=1) * np.linalg.norm(q) + 1e-9)
                for rank, i in enumerate(np.argsort(-sims)[:20]):
                    cid = rows[i]["id"]
                    ranked[cid] = ranked.get(cid, 0) + 1 / (60 + rank)
        best = sorted(ranked, key=ranked.get, reverse=True)[:limit]
        if not best:
            return []
        with self._lock:
            rows = {r["id"]: dict(r) for r in self._db.execute(
                f"SELECT c.id, c.page, c.text, d.path FROM chunks c JOIN documents d ON d.id = c.document_id"
                f" WHERE c.id IN ({','.join('?' * len(best))})", best)}
        return [rows[i] for i in best if i in rows]

    def stats(self) -> dict:
        with self._lock:
            return {"documents": self._db.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                    "chunks": self._db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]}
