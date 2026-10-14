"""Persistent file and event history for operators."""

import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_path TEXT NOT NULL,
        filename TEXT NOT NULL,
        digest TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        received_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        status TEXT NOT NULL,
        stage TEXT NOT NULL,
        environment TEXT NOT NULL,
        as2_sender TEXT,
        as2_receiver TEXT,
        po_count INTEGER DEFAULT 0,
        po_refs TEXT,
        so_numbers TEXT,
        shipreq_numbers TEXT,
        error TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        UNIQUE(source_path, digest)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS file_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_id INTEGER NOT NULL REFERENCES files(id),
        created_at TEXT NOT NULL,
        level TEXT NOT NULL,
        stage TEXT NOT NULL,
        message TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_updated ON files(updated_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_file ON file_events(file_id, id DESC)")
    conn.execute("""CREATE TABLE IF NOT EXISTS radius_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_id INTEGER NOT NULL REFERENCES files(id),
        business_key TEXT NOT NULL,
        entity TEXT NOT NULL,
        po_line TEXT,
        so_number INTEGER,
        shipreq_number INTEGER,
        request_json TEXT NOT NULL,
        response_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_radius_records_file ON radius_records(file_id, id)")
    conn.commit()
    return conn


class Audit:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def start(self, path: Path, environment: str):
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        now = utc_now()
        with closing(connect(self.db_path)) as conn, conn:
            conn.execute("""UPDATE files SET status='superseded',stage='Handoff',updated_at=?
                WHERE source_path=? AND digest<>? AND status IN ('received','awaiting_receipt')""",
                (now, str(path.resolve()), digest))
            row = conn.execute("SELECT id,status FROM files WHERE source_path=? AND digest=?", (str(path.resolve()), digest)).fetchone()
            if row:
                file_id = row["id"]
                conn.execute("UPDATE files SET updated_at=?, attempts=attempts+1 WHERE id=?", (now, file_id))
            else:
                cur = conn.execute("""INSERT INTO files
                    (source_path,filename,digest,size_bytes,received_at,updated_at,status,stage,environment,attempts)
                    VALUES (?,?,?,?,?,?,?,?,?,1)""",
                    (str(path.resolve()), path.name, digest, len(data), now, now, "received", "Handoff", environment))
                file_id = cur.lastrowid
                conn.execute("INSERT INTO file_events(file_id,created_at,level,stage,message) VALUES(?,?,?,?,?)",
                             (file_id, now, "info", "Handoff", "EDI file detected"))
        return file_id, digest

    def update(self, file_id: int, status: str, stage: str, message: str,
               *, level: str = "info", **fields):
        allowed = {"as2_sender", "as2_receiver", "po_count", "po_refs", "so_numbers", "shipreq_numbers", "error"}
        if fields.keys() - allowed:
            raise ValueError("Unexpected audit field")
        now = utc_now()
        with closing(connect(self.db_path)) as conn, conn:
            assignments = ", ".join(f"{key}=?" for key in fields)
            statement = "UPDATE files SET status=?,stage=?,updated_at=?" + ("," + assignments if assignments else "") + " WHERE id=?"
            conn.execute(statement, (status, stage, now, *fields.values(), file_id))
            conn.execute("INSERT INTO file_events(file_id,created_at,level,stage,message) VALUES(?,?,?,?,?)",
                         (file_id, now, level, stage, message[:2000]))

    def status(self, file_id: int):
        with closing(connect(self.db_path)) as conn:
            row = conn.execute("SELECT status,stage FROM files WHERE id=?", (file_id,)).fetchone()
            return dict(row) if row else None
