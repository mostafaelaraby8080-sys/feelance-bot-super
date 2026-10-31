"""قاعدة البيانات (SQLite): المشاريع اللي اتشافت + العروض اللي اتبعتت.

جدولين:
    seen_jobs    — كل مشروع اتشاف، عشان ما يتبعتش مرتين.
    applications — كل عرض بدأته (سعر/مدة/نص) وحالته: draft / sent / failed.

كل الدوال متزامنة (blocking) وبتفتح اتصال خاص بيها، فآمنة تتندّه من
asyncio.to_thread في أكثر من task في نفس الوقت.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("DATABASE_PATH", "jobs.db"))

logger = logging.getLogger("freelance-bot.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    job_id    TEXT PRIMARY KEY,
    platform  TEXT NOT NULL DEFAULT '',
    title     TEXT NOT NULL DEFAULT '',
    seen_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_seen_jobs_platform ON seen_jobs (platform);

CREATE TABLE IF NOT EXISTS applications (
    job_id     TEXT PRIMARY KEY,
    platform   TEXT NOT NULL DEFAULT '',
    title      TEXT NOT NULL DEFAULT '',
    link       TEXT NOT NULL DEFAULT '',
    price      TEXT NOT NULL DEFAULT '',
    duration   TEXT NOT NULL DEFAULT '',
    proposal   TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'draft',
    error      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_applications_status ON applications (status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _platform_of(job_id: str) -> str:
    """job.id بالشكل 'platform:raw_id'، فلو البلاتفورم مش متمرر نستخرجه."""
    return job_id.split(":", 1)[0] if ":" in job_id else ""


@contextmanager
def _connect():
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    """إنشاء الجداول لو مش موجودة."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)
    logger.info("قاعدة البيانات جاهزة: %s", DB_PATH)


# --------------------------------------------------------------------------
# المشاريع اللي اتشافت
# --------------------------------------------------------------------------
def is_job_seen(job_id: str) -> bool:
    with _connect() as connection:
        row = connection.execute(
            "SELECT 1 FROM seen_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    return row is not None


def mark_job_seen(job_id: str, title: str = "", platform: str = "") -> None:
    """تسجيل مشروع كـ 'اتشاف'. آمن لو اتندّه أكتر من مرة."""
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO seen_jobs (job_id, platform, title, seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                title = excluded.title
            """,
            (job_id, platform or _platform_of(job_id), title or "", _now()),
        )


def count_seen_jobs(platform: str = "") -> int:
    """عدد المشاريع المسجّلة، أو عدد مشاريع منصة معينة."""
    with _connect() as connection:
        if platform:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM seen_jobs WHERE platform = ?",
                (platform,),
            ).fetchone()
        else:
            row = connection.execute("SELECT COUNT(*) AS total FROM seen_jobs").fetchone()
    return int(row["total"])


# --------------------------------------------------------------------------
# العروض
# --------------------------------------------------------------------------
def save_application(
    job_id: str,
    *,
    platform: str = "",
    title: str = "",
    link: str = "",
    price: str = "",
    duration: str = "",
    proposal: str = "",
    status: str = "draft",
) -> None:
    """حفظ/تحديث عرض. بيحافظ على created_at الأصلي."""
    now = _now()
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO applications
                (job_id, platform, title, link, price, duration, proposal,
                 status, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                platform   = excluded.platform,
                title      = excluded.title,
                link       = excluded.link,
                price      = excluded.price,
                duration   = excluded.duration,
                proposal   = excluded.proposal,
                status     = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                job_id,
                platform or _platform_of(job_id),
                title or "",
                link or "",
                price or "",
                duration or "",
                proposal or "",
                status,
                now,
                now,
            ),
        )


def set_application_status(job_id: str, status: str, error: str = "") -> None:
    """تغيير حالة عرض: draft / sent / failed."""
    with _connect() as connection:
        connection.execute(
            """
            UPDATE applications
               SET status = ?, error = ?, updated_at = ?
             WHERE job_id = ?
            """,
            (status, (error or "")[:1000], _now(), job_id),
        )


def has_sent_application(job_id: str) -> bool:
    """هل قدّمت فعلاً على المشروع ده؟ (يمنع التقديم المكرر)."""
    with _connect() as connection:
        row = connection.execute(
            "SELECT 1 FROM applications WHERE job_id = ? AND status = 'sent'",
            (job_id,),
        ).fetchone()
    return row is not None


def get_application(job_id: str) -> dict | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
    return dict(row) if row else None


def stats() -> dict:
    """إحصائية سريعة للأمر /status."""
    with _connect() as connection:
        seen = connection.execute("SELECT COUNT(*) AS total FROM seen_jobs").fetchone()["total"]
        rows = connection.execute(
            "SELECT status, COUNT(*) AS total FROM applications GROUP BY status"
        ).fetchall()

    applications = {row["status"]: int(row["total"]) for row in rows}
    return {"seen": int(seen), "applications": applications}
