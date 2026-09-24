"""SQLite persistence: who is subscribed, what we've already announced, and the digest queue.

Deliberately synchronous. SQLite writes here are sub-millisecond and the bot
polls on a timer, so an async driver would add dependencies for no gain.

Schema migrations run automatically on startup: ``_migrate`` compares the
current ``PRAGMA user_version`` against the latest known version and applies
each pending migration in order.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path

#: Highest schema version this code understands. Bump when you add a migration.
LATEST_SCHEMA_VERSION = 4

# Version 1 → 2: add frequency column for notification preferences.
_MIGRATION_1_TO_2 = """
ALTER TABLE subscribers ADD COLUMN frequency TEXT NOT NULL DEFAULT 'instant';
"""

# Version 2 → 3: digest queue for batching daily/weekly notifications.
_MIGRATION_2_TO_3 = """
CREATE TABLE IF NOT EXISTS digest_queue (
    course_id TEXT PRIMARY KEY,
    title     TEXT NOT NULL,
    url       TEXT NOT NULL,
    source    TEXT NOT NULL,
    queued_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS digest_queue_queued_at ON digest_queue (queued_at);
"""

# Version 3 → 4: per-subscriber language for i18n.
_MIGRATION_3_TO_4 = """
ALTER TABLE subscribers ADD COLUMN language TEXT NOT NULL DEFAULT '';
"""

#: Ordered list of migrations. Index N upgrades from version N to N+1.
MIGRATIONS: list[str] = [
    _MIGRATION_1_TO_2,
    _MIGRATION_2_TO_3,
    _MIGRATION_3_TO_4,
]


class Store:
    def __init__(self, path: str | Path = "bot.db") -> None:
        self.path = str(path)
        with self._conn() as conn:
            existed = self._has_tables(conn)
            conn.executescript(_base_schema())
            if not existed:
                # Brand-new database: stamp to latest so migrations are skipped.
                conn.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION}")
            else:
                self._migrate(conn)

    @staticmethod
    def _has_tables(conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='subscribers'"
        ).fetchone()
        return row is not None

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Apply any pending migrations in order, then stamp to latest."""
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for target, sql in enumerate(MIGRATIONS, start=1):
            if target <= current:
                continue
            if target > LATEST_SCHEMA_VERSION:
                break
            conn.executescript(sql)
        conn.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION}")

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

    def set_frequency(self, chat_id: int, frequency: str) -> None:
        """Set notification frequency: instant, daily, weekly, or paused."""
        if frequency not in {"instant", "daily", "weekly", "paused"}:
            raise ValueError(f"invalid frequency: {frequency}")
        with self._conn() as conn:
            conn.execute(
                "UPDATE subscribers SET frequency = ? WHERE chat_id = ?",
                (frequency, chat_id),
            )

    def set_language(self, chat_id: int, language: str) -> None:
        """Set per-subscriber UI language (empty string = use bot default)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE subscribers SET language = ? WHERE chat_id = ?",
                (language.strip().lower(), chat_id),
            )

    def get_subscriber(self, chat_id: int) -> sqlite3.Row | None:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM subscribers WHERE chat_id = ?", (chat_id,)
            ).fetchone()

    def all_subscribers(self) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute("SELECT * FROM subscribers").fetchall()

    def subscribers_by_frequency(self, frequency: str) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM subscribers WHERE frequency = ?", (frequency,)
            ).fetchall()

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

    def mark_seen(self, courses: Iterable) -> None:
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

    # ---------- digest queue ----------

    def queue_for_digest(self, courses: Iterable) -> None:
        """Add courses to the digest queue (deduped by course_id)."""
        now = int(time.time())
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO digest_queue (course_id, title, url, source, queued_at)"
                " VALUES (?, ?, ?, ?, ?)",
                [(c.id, c.title, c.url, c.source, now) for c in courses],
            )

    def drain_digest(self, limit: int = 50) -> list[sqlite3.Row]:
        """Remove and return up to *limit* queued digest entries (oldest first)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM digest_queue ORDER BY queued_at ASC LIMIT ?", (limit,)
            ).fetchall()
            if rows:
                ids = [r["course_id"] for r in rows]
                placeholders = ",".join("?" * len(ids))
                conn.execute(
                    f"DELETE FROM digest_queue WHERE course_id IN ({placeholders})", ids
                )
            return rows

    def digest_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM digest_queue").fetchone()[0]

    # ---------- metrics ----------

    def bump_metric(self, key: str, amount: int = 1) -> None:
        """Increment a named counter (creates it on first use)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO metrics (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = value + excluded.value,"
                " updated_at = excluded.updated_at",
                (key, amount, int(time.time())),
            )

    def get_metric(self, key: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM metrics WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else 0

    def all_metrics(self) -> dict[str, int]:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM metrics").fetchall()
            return {r["key"]: r["value"] for r in rows}


def _base_schema() -> str:
    return """
    CREATE TABLE IF NOT EXISTS subscribers (
        chat_id   INTEGER PRIMARY KEY,
        keywords  TEXT    NOT NULL DEFAULT '',
        added_at  INTEGER NOT NULL,
        frequency TEXT    NOT NULL DEFAULT 'instant',
        language  TEXT    NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS seen (
        course_id  TEXT PRIMARY KEY,
        title      TEXT NOT NULL,
        url        TEXT NOT NULL,
        source     TEXT NOT NULL,
        first_seen INTEGER NOT NULL
    );

    CREATE INDEX IF NOT EXISTS seen_first_seen ON seen (first_seen DESC);

    CREATE TABLE IF NOT EXISTS digest_queue (
        course_id TEXT PRIMARY KEY,
        title     TEXT NOT NULL,
        url       TEXT NOT NULL,
        source    TEXT NOT NULL,
        queued_at INTEGER NOT NULL
    );

    CREATE INDEX IF NOT EXISTS digest_queue_queued_at ON digest_queue (queued_at);

    CREATE TABLE IF NOT EXISTS metrics (
        key        TEXT PRIMARY KEY,
        value      INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL
    );
    """
