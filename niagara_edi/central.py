"""Versioned platform configuration and read-only Radius/VAN file observation."""

import fnmatch
import hashlib
import os
import re
import sqlite3
import time
import logging
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


CATALOG = {
    ("INBOUND", "850"): "LEGACY_ADAPTER_SEPARATE",
    ("INBOUND", "830"): "PLANNED",
    ("INBOUND", "856"): "PLANNED",
    ("OUTBOUND", "810"): "PLANNED",
    ("OUTBOUND", "850"): "PLANNED",
    ("OUTBOUND", "855"): "PLANNED",
    ("OUTBOUND", "856"): "OBSERVATION_ONLY",
    ("OUTBOUND", "858"): "PLANNED",
}
CODE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def session(db_path: Path):
    with closing(connect(db_path)) as conn, conn:
        yield conn


def migrate(db_path: Path):
    """Apply ordered migrations in the same SQLite database as the legacy audit."""
    directory = Path(__file__).parent / "migrations"
    with session(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS central_schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        applied = {row[0] for row in conn.execute("SELECT version FROM central_schema_migrations")}
        for path in sorted(directory.glob("[0-9][0-9][0-9]_*.sql")):
            version = int(path.name[:3])
            if version in applied:
                continue
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO central_schema_migrations(version,applied_at) VALUES(?,?)", (version, now_utc()))


def create_partner(conn, *, partner_code, display_name, radius_customer_code, company_number, plant_code,
                   edi_identity=""):
    if not CODE.fullmatch(partner_code) or not display_name.strip() or not radius_customer_code.strip() or not plant_code.strip():
        raise ValueError("Partner code, name, Radius customer and plant are required")
    try:
        company_number = int(company_number)
    except (ValueError, TypeError) as exc:
        raise ValueError("Company number must be positive") from exc
    if company_number <= 0:
        raise ValueError("Company number must be positive")
    stamp = now_utc()
    cursor = conn.execute("""INSERT INTO central_partners
        (partner_code,display_name,radius_customer_code,company_number,plant_code,edi_identity,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?)""", (partner_code, display_name.strip(), radius_customer_code.strip(),
        company_number, plant_code.strip(), edi_identity.strip(), stamp, stamp))
    return cursor.lastrowid


def set_partner_enabled(conn, partner_id: int, enabled: bool):
    if not conn.execute("SELECT 1 FROM central_partners WHERE id=?", (partner_id,)).fetchone():
        raise ValueError("Unknown partner")
    conn.execute("UPDATE central_partners SET enabled=?,updated_at=? WHERE id=?",
                 (int(enabled), now_utc(), partner_id))
    if not enabled:
        conn.execute("UPDATE central_capabilities SET enabled=0,updated_at=? WHERE partner_id=?",
                     (now_utc(), partner_id))
    conn.execute("INSERT INTO central_events(created_at,level,stage,message) VALUES(?,?,?,?)",
                 (now_utc(), "info", "Configuration", f"Partner {partner_id} {'enabled' if enabled else 'paused'}"))


def expected_strategy(direction, route_class):
    if direction not in {"INBOUND", "OUTBOUND"} or route_class not in {"VAN", "DIRECT"}:
        raise ValueError("Direction and route class must be INBOUND/OUTBOUND and VAN/DIRECT")
    return ("PASSTHROUGH_TRACK" if route_class == "VAN" else
            "TRANSLATE_TO_RADIUS" if direction == "INBOUND" else "TRANSLATE_FROM_RADIUS")


def create_capability(conn, *, partner_id, environment, direction, message_type, route_class,
                      operating_mode="OBSERVE_ONLY", radius_message_active=None,
                      radius_contract_verified=False, radius_format="", source_format="",
                      destination_format="", format_version="", mapping_version="", transport="FOLDER",
                      radius_inbound_folder="", radius_outbound_folder="", partner_inbound_folder="",
                      partner_outbound_folder="", working_folder="", archive_folder="",
                      quarantine_folder="", filename_pattern="*", poll_seconds=60):
    if environment not in {"TEST", "LIVE"} or (direction, message_type) not in CATALOG:
        raise ValueError("Unsupported environment or EDI capability")
    if operating_mode not in {"OBSERVE_ONLY", "DRY_RUN", "ACTIVE"}:
        raise ValueError("Invalid operating mode")
    if not filename_pattern or any(c in filename_pattern for c in "/\\") or filename_pattern in {".", ".."}:
        raise ValueError("Filename pattern must not contain a directory")
    strategy = expected_strategy(direction, route_class)
    if radius_message_active not in (None, True, False):
        raise ValueError("Radius message active status must be yes, no, or unverified")
    if int(poll_seconds) < 5:
        raise ValueError("Poll interval must be at least five seconds")
    if not conn.execute("SELECT 1 FROM central_partners WHERE id=?", (partner_id,)).fetchone():
        raise ValueError("Unknown partner")
    stamp = now_utc()
    cursor = conn.execute("""INSERT INTO central_capabilities
        (partner_id,environment,direction,message_type,route_class,strategy,operating_mode,
         radius_message_active,radius_contract_verified,radius_format,source_format,destination_format,
         format_version,mapping_version,transport,radius_inbound_folder,radius_outbound_folder,
         partner_inbound_folder,partner_outbound_folder,working_folder,archive_folder,quarantine_folder,
         filename_pattern,poll_seconds,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (partner_id, environment, direction, message_type, route_class, strategy, operating_mode,
         radius_message_active, int(radius_contract_verified), radius_format, source_format,
         destination_format, format_version, mapping_version, transport, radius_inbound_folder,
         radius_outbound_folder, partner_inbound_folder, partner_outbound_folder, working_folder,
         archive_folder, quarantine_folder, filename_pattern, int(poll_seconds), stamp, stamp))
    return cursor.lastrowid


def observation_folder(capability):
    if capability["strategy"] != "PASSTHROUGH_TRACK":
        raise ValueError("No verified translator is installed for this capability")
    return capability["radius_inbound_folder"] if capability["direction"] == "INBOUND" else capability["radius_outbound_folder"]


def update_capability(conn, capability_id: int, **changes):
    cap = conn.execute("SELECT * FROM central_capabilities WHERE id=?", (capability_id,)).fetchone()
    if not cap:
        raise ValueError("Unknown capability")
    if cap["enabled"]:
        raise ValueError("Pause observation before editing a capability")
    allowed = {"operating_mode", "radius_message_active", "radius_contract_verified", "radius_format",
               "source_format", "destination_format", "format_version", "mapping_version", "transport",
               "radius_inbound_folder", "radius_outbound_folder", "partner_inbound_folder",
               "partner_outbound_folder", "working_folder", "archive_folder", "quarantine_folder",
               "filename_pattern", "poll_seconds"}
    if not changes or changes.keys() - allowed:
        raise ValueError("Unexpected capability field")
    if changes.get("operating_mode", cap["operating_mode"]) not in {"OBSERVE_ONLY", "DRY_RUN", "ACTIVE"}:
        raise ValueError("Invalid operating mode")
    pattern = changes.get("filename_pattern", cap["filename_pattern"])
    if not pattern or any(c in pattern for c in "/\\") or pattern in {".", ".."}:
        raise ValueError("Filename pattern must not contain a directory")
    if int(changes.get("poll_seconds", cap["poll_seconds"])) < 5:
        raise ValueError("Poll interval must be at least five seconds")
    if changes.get("radius_message_active", cap["radius_message_active"]) not in (None, 0, 1):
        raise ValueError("Radius message active status must be yes, no, or unverified")
    changes["updated_at"] = now_utc()
    conn.execute("UPDATE central_capabilities SET " + ",".join(f"{key}=?" for key in changes) + " WHERE id=?",
                 (*changes.values(), capability_id))
    conn.execute("INSERT INTO central_events(capability_id,created_at,level,stage,message) VALUES(?,?,?,?,?)",
                 (capability_id, now_utc(), "info", "Configuration", "Capability draft updated"))


def set_enabled(conn, capability_id: int, enabled: bool, *, allowed_roots=()):
    cap = conn.execute("SELECT * FROM central_capabilities WHERE id=?", (capability_id,)).fetchone()
    if not cap:
        raise ValueError("Unknown capability")
    if enabled:
        partner = conn.execute("SELECT enabled FROM central_partners WHERE id=?", (cap["partner_id"],)).fetchone()
        if not partner or not partner[0]:
            raise ValueError("Enable the partner before enabling observation")
        if cap["environment"] == "LIVE" and os.getenv("EDI_CENTRAL_LIVE_OBSERVATION") != "1":
            raise ValueError("LIVE capability activation requires explicit read-only observation authorization")
        if cap["operating_mode"] != "OBSERVE_ONLY":
            raise ValueError("Only read-only observation can be enabled in this release")
        folder = observation_folder(cap)
        if not cap["radius_contract_verified"] or cap["radius_message_active"] != 1:
            raise ValueError("Radius folder contract and message activation must be verified first")
        if not folder:
            raise ValueError("Radius observation folder is required")
        path = Path(folder).resolve()
        roots = [Path(root).resolve() for root in allowed_roots]
        if not roots or not any(path == root or root in path.parents for root in roots):
            raise ValueError("Observation folder is outside configured allowed roots")
        if not path.is_dir():
            raise ValueError("Observation folder is not accessible")
    conn.execute("UPDATE central_capabilities SET enabled=?,updated_at=? WHERE id=?",
                 (int(enabled), now_utc(), capability_id))
    conn.execute("INSERT INTO central_events(capability_id,created_at,level,stage,message) VALUES(?,?,?,?,?)",
                 (capability_id, now_utc(), "info", "Configuration", "Observation enabled" if enabled else "Observation paused"))


def scan_observer_once(db_path: Path, capability_id: int, *, owner=None, stable_seconds=2,
                       allowed_roots=None):
    """Record stable Radius/VAN-owned files without renaming, moving or writing to them."""
    owner = owner or uuid4().hex
    with session(db_path) as conn:
        cap = conn.execute("""SELECT c.*,p.radius_customer_code,p.company_number,p.plant_code FROM central_capabilities c JOIN central_partners p ON p.id=c.partner_id
            WHERE c.id=? AND p.enabled=1""", (capability_id,)).fetchone()
        if not cap or not cap["enabled"] or cap["operating_mode"] != "OBSERVE_ONLY":
            return 0
        if cap["environment"] == "LIVE" and os.getenv("EDI_CENTRAL_LIVE_OBSERVATION") != "1":
            return 0
        folder = Path(observation_folder(cap))
        if allowed_roots is None:
            allowed_roots = tuple(part for part in os.getenv("EDI_CENTRAL_ALLOWED_ROOTS", "").split(";") if part.strip())
        resolved = folder.resolve()
        roots = [Path(root).resolve() for root in allowed_roots]
        if not roots or not any(resolved == root or root in resolved.parents for root in roots):
            raise ValueError("Observation folder is outside configured allowed roots")
        instant = datetime.now(timezone.utc)
        lease_until = (instant + timedelta(seconds=max(30, cap["poll_seconds"] * 2))).isoformat(timespec="seconds")
        conn.execute("DELETE FROM central_worker_leases WHERE capability_id=? AND expires_at<=?",
                     (capability_id, instant.isoformat(timespec="seconds")))
        try:
            conn.execute("INSERT INTO central_worker_leases(capability_id,owner,expires_at) VALUES(?,?,?)",
                         (capability_id, owner, lease_until))
            conn.commit()
        except sqlite3.IntegrityError:
            return 0
    recorded = 0
    try:
        for path in folder.iterdir():
            if path.is_symlink() or not path.is_file() or not fnmatch.fnmatch(path.name, cap["filename_pattern"]):
                continue
            stat = path.stat()
            if instant.timestamp() - stat.st_mtime < stable_seconds:
                continue
            with session(db_path) as conn:
                candidate = conn.execute("SELECT size_bytes,mtime_ns,first_seen_at FROM central_observation_candidates WHERE capability_id=? AND path=?",
                                         (capability_id, str(path))).fetchone()
                if not candidate or candidate[0] != stat.st_size or candidate[1] != stat.st_mtime_ns:
                    conn.execute("""INSERT INTO central_observation_candidates
                        (capability_id,path,size_bytes,mtime_ns,first_seen_at) VALUES(?,?,?,?,?)
                        ON CONFLICT(capability_id,path) DO UPDATE SET size_bytes=excluded.size_bytes,
                        mtime_ns=excluded.mtime_ns,first_seen_at=excluded.first_seen_at""",
                        (capability_id, str(path), stat.st_size, stat.st_mtime_ns, now_utc()))
                    continue
                if (instant - datetime.fromisoformat(candidate[2])).total_seconds() < stable_seconds:
                    continue
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            after = path.stat()
            if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                continue
            state = "RADIUS_FILE_OBSERVED" if cap["direction"] == "INBOUND" else "PICKUP_UNVERIFIED"
            with session(db_path) as conn:
                cursor = conn.execute("""INSERT OR IGNORE INTO central_artifacts
                    (capability_id,source_path,filename,digest,size_bytes,mtime_ns,observed_at,state)
                    VALUES(?,?,?,?,?,?,?,?)""", (capability_id, str(path), path.name, digest,
                    stat.st_size, stat.st_mtime_ns, now_utc(), state))
                if cursor.rowcount:
                    artifact_id = cursor.lastrowid
                    conn.execute("INSERT INTO central_events(capability_id,artifact_id,created_at,level,stage,message) VALUES(?,?,?,?,?,?)",
                                 (capability_id, artifact_id, now_utc(), "info", "Observation", state))
                    if cap["direction"] == "OUTBOUND" and cap["message_type"] == "856":
                        from .outbound856 import PROFILE
                        if cap["source_format"] == PROFILE:
                            _index_856(conn, cap, artifact_id, data)
                    recorded += 1
        with session(db_path) as conn:
            conn.execute("UPDATE central_capabilities SET last_scan_at=?,last_error=NULL WHERE id=?",
                         (now_utc(), capability_id))
    except OSError as exc:
        with session(db_path) as conn:
            conn.execute("UPDATE central_capabilities SET last_error=? WHERE id=?", (str(exc)[:500], capability_id))
            conn.execute("INSERT INTO central_events(capability_id,created_at,level,stage,message) VALUES(?,?,?,?,?)",
                         (capability_id, now_utc(), "error", "Observation", str(exc)[:500]))
        raise
    finally:
        with session(db_path) as conn:
            conn.execute("DELETE FROM central_worker_leases WHERE capability_id=? AND owner=?", (capability_id, owner))
    return recorded


def _index_856(conn, cap, artifact_id, data):
    from .outbound856 import PARSER_VERSION, parse_outbound_856
    try:
        documents = parse_outbound_856(data, customer=cap["radius_customer_code"],
                                       company=cap["company_number"], plant=cap["plant_code"])
    except (ValueError, UnicodeError) as exc:
        conn.execute("UPDATE central_artifacts SET parse_status='REJECTED',parse_error=?,parser_version=? WHERE id=?",
                     (str(exc), PARSER_VERSION, artifact_id))
        conn.execute("INSERT INTO central_events(capability_id,artifact_id,created_at,level,stage,message) VALUES(?,?,?,?,?,?)",
                     (cap["id"], artifact_id, now_utc(), "error", "856 parsing", str(exc)))
        return
    for doc in documents:
        sales = {line["so"] for line in doc["lines"]}
        cursor = conn.execute("""INSERT INTO central_documents
            (artifact_id,partner_document_id,radius_so_number,radius_shipreq_number,status,created_at,
             radius_bol_number,radius_customer_code,company_number,plant_code)
            VALUES(?,?,?,?,?,?,?,?,?,?)""", (artifact_id, str(doc["shipreq"]),
            next(iter(sales)) if len(sales) == 1 else None, doc["shipreq"], "PARSED", now_utc(),
            doc["bol"], doc["customer"], doc["company"], doc["plant"]))
        for line in doc["lines"]:
            conn.execute("""INSERT INTO central_document_lines
                (document_id,radius_shipreq_line,radius_so_number,radius_so_line,customer_po)
                VALUES(?,?,?,?,?)""", (cursor.lastrowid, line["shipreq_line"], line["so"], line["so_line"], line["po"]))
    conn.execute("UPDATE central_artifacts SET parse_status='PARSED',parser_version=? WHERE id=?",
                 (PARSER_VERSION, artifact_id))
    conn.execute("INSERT INTO central_events(capability_id,artifact_id,created_at,level,stage,message) VALUES(?,?,?,?,?,?)",
                 (cap["id"], artifact_id, now_utc(), "info", "856 parsing", f"Indexed {len(documents)} shipping notes; pickup remains unverified"))


def run_observer_forever(db_path, interval=10):
    """Shared read-only worker for the standalone CLI and web launcher."""
    while True:
        with session(db_path) as conn:
            caps = list(conn.execute("SELECT id,poll_seconds,last_scan_at FROM central_capabilities WHERE enabled=1"))
        for cap in caps:
            if cap["last_scan_at"] and time.time() - datetime.fromisoformat(cap["last_scan_at"]).timestamp() < cap["poll_seconds"]:
                continue
            try:
                scan_observer_once(db_path, cap["id"])
            except (OSError, ValueError, sqlite3.Error):
                logging.exception("Capability %s observation failed", cap["id"])
        from .radius856_monitor import sync_source
        with session(db_path) as conn:
            sources = list(conn.execute("SELECT id,poll_seconds,last_scan_at FROM central_radius_sources WHERE enabled=1"))
        for source in sources:
            if source["last_scan_at"] and time.time() - datetime.fromisoformat(source["last_scan_at"]).timestamp() < source["poll_seconds"]:
                continue
            try:
                sync_source(db_path, source["id"])
            except (OSError, ValueError, RuntimeError, sqlite3.Error):
                logging.exception("Radius 856 source %s sync failed", source["id"])
        time.sleep(interval)
