import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

DB_PATH = os.path.join(
    os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__))),
    "subscriptions.db",
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                account_email       TEXT NOT NULL DEFAULT '',
                sender_email        TEXT NOT NULL,
                sender_name         TEXT,
                latest_subject      TEXT,
                unsubscribe_method  TEXT NOT NULL,
                unsubscribe_target  TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'pending',
                error_message       TEXT,
                found_at            TEXT NOT NULL DEFAULT (datetime('now')),
                unsubscribed_at     TEXT,
                UNIQUE(account_email, sender_email)
            )
        """)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn):
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(subscriptions)").fetchall()
    }
    if "account_email" not in cols:
        conn.execute("""
            CREATE TABLE subscriptions_new (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                account_email       TEXT NOT NULL DEFAULT '',
                sender_email        TEXT NOT NULL,
                sender_name         TEXT,
                latest_subject      TEXT,
                unsubscribe_method  TEXT NOT NULL,
                unsubscribe_target  TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'pending',
                error_message       TEXT,
                found_at            TEXT NOT NULL DEFAULT (datetime('now')),
                unsubscribed_at     TEXT,
                UNIQUE(account_email, sender_email)
            )
        """)
        conn.execute("""
            INSERT INTO subscriptions_new
                (id, account_email, sender_email, sender_name, latest_subject,
                 unsubscribe_method, unsubscribe_target, status, error_message,
                 found_at, unsubscribed_at)
            SELECT id, '', sender_email, sender_name, latest_subject,
                   unsubscribe_method, unsubscribe_target,
                   status, error_message, found_at, unsubscribed_at
            FROM subscriptions
        """)
        conn.execute("DROP TABLE subscriptions")
        conn.execute("ALTER TABLE subscriptions_new RENAME TO subscriptions")


def add_subscription(
    account_email: str,
    sender_email: str,
    sender_name: str,
    latest_subject: str,
    unsubscribe_method: str,
    unsubscribe_target: str,
) -> bool:
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO subscriptions
               (account_email, sender_email, sender_name, latest_subject,
                unsubscribe_method, unsubscribe_target)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (account_email, sender_email, sender_name, latest_subject,
             unsubscribe_method, unsubscribe_target),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_subscriptions(
    account_email: str, status: Optional[str] = None
) -> List[Dict]:
    conn = _conn()
    try:
        if status:
            rows = conn.execute(
                """SELECT * FROM subscriptions
                   WHERE account_email = ? AND status = ?
                   ORDER BY found_at DESC""",
                (account_email, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM subscriptions"
                " WHERE account_email = ? ORDER BY found_at DESC",
                (account_email,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_subscription(sub_id: int) -> Optional[Dict]:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE id = ?", (sub_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_status(
    sub_id: int, status: str, error_message: Optional[str] = None
):
    conn = _conn()
    try:
        if status == "success":
            conn.execute(
                """UPDATE subscriptions
                   SET status = ?, unsubscribed_at = ?, error_message = NULL
                   WHERE id = ?""",
                (status, datetime.now().isoformat(), sub_id),
            )
        else:
            conn.execute(
                "UPDATE subscriptions"
                " SET status = ?, error_message = ? WHERE id = ?",
                (status, error_message, sub_id),
            )
        conn.commit()
    finally:
        conn.close()


def find_mailto_by_address(
    account_email: str, address: str
) -> List[Dict]:
    """Find mailto subscriptions whose target address matches `address`.

    Handles both bare addresses and `addr@host?subject=...` form.
    """
    addr = address.lower()
    conn = _conn()
    try:
        rows = conn.execute(
            """SELECT * FROM subscriptions
               WHERE account_email = ?
                 AND unsubscribe_method = 'mailto'
                 AND (
                     lower(unsubscribe_target) = ?
                     OR lower(unsubscribe_target) LIKE ?
                 )""",
            (account_email, addr, addr + "?%"),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_subscriptions(account_email: str) -> int:
    conn = _conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE account_email = ?",
            (account_email,),
        ).fetchone()[0]
    finally:
        conn.close()


def get_stats(account_email: str) -> Dict:
    conn = _conn()
    try:
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success,
                SUM(CASE WHEN status = 'failed'  THEN 1 ELSE 0 END) as failed
            FROM subscriptions
            WHERE account_email = ?
        """, (account_email,)).fetchone()
        return dict(row)
    finally:
        conn.close()
