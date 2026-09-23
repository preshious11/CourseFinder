"""SQLite persistence: who is subscribed, and what we've already announced.

Deliberately synchronous. SQLite writes here are sub-millisecond and the bot
polls on a timer, so an async driver would add dependencies for no gain.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (
    chat_id   INTEGER PRIMARY KEY,
    keywords  TEXT    NOT NULL DEFAULT '',
    added_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS seen (
    course_id  TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    url        TEXT NOT NULL,
    source     TEXT NOT NULL,
    first_seen INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS seen_first_seen ON seen (first_seen DESC);
"""


class Store:
    def __init__(self, path: str | Path = "bot.db") -> None:
        self.path = str(path)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------- subscribers ----------

    def subscribe(self, chat_id: int) -> bool:
        """Returns True if this was a new subscription."""
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO subscribers (chat_id, added_at) VALUES (?, ?)",
                (chat_id, int(time.time())),
            )
            return cur.rowcount > 0

    def unsubscribe(self, chat_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM subscribers WHERE chat_id = ?", (chat_id,)
            )
            return cur.rowcount > 0

    def set_keywords(self, chat_id: int, keywords: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE subscribers SET keywords = ? WHERE chat_id = ?",
                (keywords.lower().strip(), chat_id),
            )

    def get_subscriber(self, chat_id: int) -> sqlite3.Row | None:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM subscribers WHERE chat_id = ?", (chat_id,)
            ).fetchone()

    def all_subscribers(self) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute("SELECT * FROM subscribers").fetchall()

    def subscriber_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]

    # ---------- seen courses ----------

    def filter_new(self, courses: list) -> list:
        """Return only the courses we have never announced before."""
        if not courses:
            return []
        ids = [c.id for c in courses]
        placeholders = ",".join("?" * len(ids))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT course_id FROM seen WHERE course_id IN ({placeholders})",
                ids,
            ).fetchall()
        known = {r["course_id"] for r in rows}

        fresh, batch_ids = [], set()
        for course in courses:
            # guard against the same course appearing twice in one poll
            if course.id in known or course.id in batch_ids:
                continue
            batch_ids.add(course.id)
            fresh.append(course)
        return fresh

    def mark_seen(self, courses: list) -> None:
        now = int(time.time())
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO seen (course_id, title, url, source, first_seen)"
                " VALUES (?, ?, ?, ?, ?)",
                [(c.id, c.title, c.url, c.source, now) for c in courses],
            )

    def recent(self, limit: int = 5) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM seen ORDER BY first_seen DESC LIMIT ?", (limit,)
            ).fetchall()

    def seen_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]

    def prune(self, older_than_days: int = 90) -> int:
        """Drop ancient rows so the dedupe table doesn't grow forever."""
        cutoff = int(time.time()) - older_than_days * 86400
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM seen WHERE first_seen < ?", (cutoff,))
            return cur.rowcount
