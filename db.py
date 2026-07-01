"""SQLite persistence for decisions, appeals, and the append-only audit log."""
import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "provenance_guard.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decisions (
            content_id TEXT PRIMARY KEY,
            creator_id TEXT,
            text TEXT,
            created_at TEXT,
            llm_p_ai REAL,
            llm_reasoning TEXT,
            stylometry_p_ai REAL,
            stylometry_features TEXT,
            p_ai REAL,
            confidence REAL,
            label_variant TEXT,
            label_text TEXT,
            status TEXT,
            model TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS appeals (
            appeal_id TEXT PRIMARY KEY,
            content_id TEXT,
            creator_id TEXT,
            reasoning TEXT,
            status TEXT,
            filed_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event TEXT,
            content_id TEXT,
            timestamp TEXT,
            payload TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def insert_decision(decision):
    conn = get_conn()
    features = decision.get("stylometry_features")
    conn.execute(
        """
        INSERT INTO decisions (
            content_id, creator_id, text, created_at, llm_p_ai, llm_reasoning,
            stylometry_p_ai, stylometry_features, p_ai, confidence,
            label_variant, label_text, status, model
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            decision["content_id"],
            decision.get("creator_id"),
            decision["text"],
            decision["created_at"],
            decision.get("llm_p_ai"),
            decision.get("llm_reasoning"),
            decision.get("stylometry_p_ai"),
            json.dumps(features) if features is not None else None,
            decision.get("p_ai"),
            decision.get("confidence"),
            decision.get("label_variant"),
            decision.get("label_text"),
            decision.get("status"),
            decision.get("model"),
        ),
    )
    conn.commit()
    conn.close()


def get_decision(content_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM decisions WHERE content_id = ?", (content_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    result = dict(row)
    if result.get("stylometry_features"):
        result["stylometry_features"] = json.loads(result["stylometry_features"])
    return result


def update_status(content_id, status):
    conn = get_conn()
    conn.execute(
        "UPDATE decisions SET status = ? WHERE content_id = ?", (status, content_id)
    )
    conn.commit()
    conn.close()


def insert_appeal(appeal):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO appeals (appeal_id, content_id, creator_id, reasoning, status, filed_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            appeal["appeal_id"],
            appeal["content_id"],
            appeal.get("creator_id"),
            appeal["reasoning"],
            appeal["status"],
            appeal["filed_at"],
        ),
    )
    conn.commit()
    conn.close()


def get_appeals():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM appeals ORDER BY filed_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def append_audit(event, content_id, timestamp, payload):
    conn = get_conn()
    conn.execute(
        "INSERT INTO audit_log (event, content_id, timestamp, payload) VALUES (?,?,?,?)",
        (event, content_id, timestamp, json.dumps(payload)),
    )
    conn.commit()
    conn.close()


def get_log(limit=50):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    entries = []
    for r in rows:
        entry = dict(r)
        entry["payload"] = json.loads(entry["payload"]) if entry["payload"] else {}
        entries.append(entry)
    return entries
