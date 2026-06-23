"""
SQLite persistence layer for the Agent Referral Tracker bot.
All functions are synchronous (called from async context — fine for SQLite).
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "tracker.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist. Call once at startup."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS agents (
                agent_id    TEXT PRIMARY KEY,
                guild_id    INTEGER NOT NULL,
                name        TEXT NOT NULL,
                invite_code TEXT NOT NULL UNIQUE,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS member_joins (
                member_id   INTEGER PRIMARY KEY,
                member_name TEXT NOT NULL,
                agent_id    TEXT REFERENCES agents(agent_id),
                invite_code TEXT,
                joined_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS premium_purchases (
                member_id    INTEGER PRIMARY KEY REFERENCES member_joins(member_id),
                purchased_at TEXT NOT NULL,
                lapsed_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id         INTEGER PRIMARY KEY,
                premium_role_id  INTEGER,
                tally_message_id INTEGER
            );
        """)


# Initialise on import
init_db()


# ─── Agents ────────────────────────────────────────────────────────────────────

def add_agent(*, name: str, invite_code: str, guild_id: int) -> str:
    agent_id = str(uuid.uuid4())[:8]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO agents (agent_id, guild_id, name, invite_code, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (agent_id, guild_id, name, invite_code, _now()),
        )
    return agent_id


def remove_agent(*, name: str, guild_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM agents WHERE name = ? AND guild_id = ?", (name, guild_id)
        )
        return cur.rowcount > 0


def get_agent(agent_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
    return dict(row) if row else None


def get_agent_by_invite(invite_code: str) -> str | None:
    """Return agent_id for this invite code, or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT agent_id FROM agents WHERE invite_code = ?", (invite_code,)
        ).fetchone()
    return row["agent_id"] if row else None


def get_agent_by_name(name: str, *, guild_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM agents WHERE name = ? AND guild_id = ?", (name, guild_id)
        ).fetchone()
    return dict(row) if row else None


def list_agents(guild_id: int | None = None) -> list[dict]:
    with _connect() as conn:
        if guild_id:
            rows = conn.execute(
                "SELECT * FROM agents WHERE guild_id = ? ORDER BY created_at", (guild_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM agents ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def get_agent_stats(agent_id: str) -> dict:
    with _connect() as conn:
        total_invites = conn.execute(
            "SELECT COUNT(*) FROM member_joins WHERE agent_id = ?", (agent_id,)
        ).fetchone()[0]

        # Conversions = members who bought premium (not just had it lapse only)
        conv_rows = conn.execute(
            """
            SELECT mj.member_name
            FROM premium_purchases pp
            JOIN member_joins mj ON pp.member_id = mj.member_id
            WHERE mj.agent_id = ?
            ORDER BY pp.purchased_at DESC
            """,
            (agent_id,),
        ).fetchall()

    return {
        "total_invites": total_invites,
        "premium_conversions": len(conv_rows),
        "recent_conversions": [r["member_name"] for r in conv_rows],
    }


# ─── Member Joins ──────────────────────────────────────────────────────────────

def record_member_join(
    *,
    member_id: int,
    member_name: str,
    agent_id: str | None,
    invite_code: str | None,
    joined_at: datetime,
):
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO member_joins "
            "(member_id, member_name, agent_id, invite_code, joined_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (member_id, member_name, agent_id, invite_code, joined_at.isoformat()),
        )


def get_member_record(member_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM member_joins WHERE member_id = ?", (member_id,)
        ).fetchone()
    return dict(row) if row else None


def update_member_agent(*, member_id: int, agent_id: str):
    with _connect() as conn:
        conn.execute(
            "UPDATE member_joins SET agent_id = ? WHERE member_id = ?",
            (agent_id, member_id),
        )


# ─── Premium Purchases ─────────────────────────────────────────────────────────

def record_premium_purchase(*, member_id: int, purchased_at: datetime):
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO premium_purchases (member_id, purchased_at) VALUES (?, ?)",
            (member_id, purchased_at.isoformat()),
        )


def mark_premium_lapsed(member_id: int):
    with _connect() as conn:
        conn.execute(
            "UPDATE premium_purchases SET lapsed_at = ? WHERE member_id = ? AND lapsed_at IS NULL",
            (_now(), member_id),
        )


# ─── Guild Settings ────────────────────────────────────────────────────────────

def _ensure_guild(conn: sqlite3.Connection, guild_id: int):
    conn.execute(
        "INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,)
    )


def set_premium_role(*, guild_id: int, role_id: int):
    with _connect() as conn:
        _ensure_guild(conn, guild_id)
        conn.execute(
            "UPDATE guild_settings SET premium_role_id = ? WHERE guild_id = ?",
            (role_id, guild_id),
        )


def get_premium_role(guild_id: int) -> int | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT premium_role_id FROM guild_settings WHERE guild_id = ?", (guild_id,)
        ).fetchone()
    return row["premium_role_id"] if row else None


def set_tally_message_id(guild_id: int, message_id: int):
    with _connect() as conn:
        _ensure_guild(conn, guild_id)
        conn.execute(
            "UPDATE guild_settings SET tally_message_id = ? WHERE guild_id = ?",
            (message_id, guild_id),
        )


def get_tally_message_id(guild_id: int) -> int | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT tally_message_id FROM guild_settings WHERE guild_id = ?", (guild_id,)
        ).fetchone()
    return row["tally_message_id"] if row else None


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
