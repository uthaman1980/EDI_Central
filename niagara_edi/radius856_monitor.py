"""Read-only Radius XLShippingNotes export history, independent of file retention."""

import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pyodbc

from .central import now_utc, session
from .radius import ENV_DEFAULTS


STATUS_LABELS = {0: "Not processed", 1: "Processed OK", 9: "Rejected"}
FIELDS = ("TableRecId", "Direction", "CreationDate", "CreationTime", "RecordType", "RecordStatus",
          "ErrorMessage", "CompNum", "PlantCode", "CustCode", "ShipAddrNum", "ShipReqNum",
          "ShippingType", "FileName", "ActualShipDate")


def connection_settings(environment):
    prefix = f"RADIUS_{environment}_MONITOR_"
    password = os.getenv(prefix + "PASS")
    if environment == "TEST":
        password = password or os.getenv("RADIUS_DB_PASS")
    if not password:
        raise ValueError(f"Set {prefix}PASS for the read-only 856 database monitor")
    return (os.getenv(prefix + "DSN", ENV_DEFAULTS[environment]["RADIUS_DSN"]),
            os.getenv(prefix + "USER", "vision"), password)


def _odbc_value(value):
    return "{" + value.replace("}", "}}") + "}"


class ShippingNotesReader:
    """Only SELECT statements; a separate connection from the TEST order adapter."""

    def __init__(self, environment):
        self.environment = environment

    def read_pages(self, company, recent_cursor, history_cursor, *, page_size=500):
        dsn, user, password = connection_settings(self.environment)
        if any(char in dsn for char in ";{}\r\n"):
            raise ValueError("Invalid Radius monitor DSN")
        try:
            conn = pyodbc.connect(f"DSN={dsn};UID={_odbc_value(user)};PWD={_odbc_value(password)}",
                                  readonly=True, autocommit=True, timeout=15)
            try:
                cursor = conn.cursor()
                columns = ",".join(FIELDS)
                # Verify every projected field against the installed schema without reading data.
                cursor.execute(f"SELECT {columns} FROM PUB.PV_XLShippingNotes WHERE 1=0")

                def page(after, recent=False):
                    clauses, values = ["CompNum=?", "Direction=0", "ShippingType=0"], [company]
                    if after is not None:
                        clauses.append("TableRecId>?")
                        values.append(after)
                    if recent:
                        clauses.append("CreationDate>=?")
                        values.append(date.today() - timedelta(days=30))
                    cursor.execute(f"SELECT {columns} FROM PUB.PV_XLShippingNotes WHERE " +
                                   " AND ".join(clauses) + " ORDER BY TableRecId", *values)
                    rows = [dict(zip(FIELDS, row)) for row in cursor.fetchmany(page_size)]
                    cursor.cancel()
                    return rows

                return page(recent_cursor, True), page(history_cursor)
            finally:
                conn.close()
        except pyodbc.Error as exc:
            message = str(exc).replace(password, "[REDACTED]")
            message = re.sub(r"(?i)(PWD|PASSWORD)\s*=\s*[^;]*", r"\1=[REDACTED]", message)
            raise RuntimeError(f"Radius {self.environment} 856 read failed: {message[:800]}") from None


def _text(value):
    return "" if value is None else str(value).strip()


def _iso_date(value):
    if value is None:
        return None
    return value.isoformat()[:10] if hasattr(value, "isoformat") else str(value)[:10]


def store_record(conn, source, row):
    # Enforce scope again before storing; neither a mocked reader nor bad data can mix scopes.
    if int(row["Direction"]) != 0 or int(row["ShippingType"]) != 0 or int(row["CompNum"]) != source["company_number"]:
        raise ValueError("Radius 856 record is outside the configured export/EDI/company scope")
    record_id = int(row["TableRecId"])
    values = (_iso_date(row["CreationDate"]), _text(row["CreationTime"]), int(row["RecordType"]),
              int(row["RecordStatus"]), _text(row["ErrorMessage"]), int(row["CompNum"]),
              _text(row["PlantCode"]), _text(row["CustCode"]), row["ShipAddrNum"], row["ShipReqNum"],
              _text(row["FileName"]), _iso_date(row["ActualShipDate"]))
    digest = hashlib.sha256(json.dumps(values, default=str).encode()).hexdigest()
    existing = conn.execute("SELECT id,snapshot_digest FROM central_radius_856_records WHERE source_id=? AND table_rec_id=?",
                            (source["id"], record_id)).fetchone()
    stamp = now_utc()
    if existing and existing["snapshot_digest"] == digest:
        conn.execute("UPDATE central_radius_856_records SET last_seen_at=? WHERE id=?", (stamp, existing["id"]))
        return 0
    fields = ("creation_date", "creation_time", "record_type", "record_status", "error_message", "company_number",
              "plant_code", "customer_code", "ship_address_number", "shipreq_number", "file_name", "actual_ship_date")
    if existing:
        local_id = existing["id"]
        conn.execute("UPDATE central_radius_856_records SET " + ",".join(name + "=?" for name in fields) +
                     ",snapshot_digest=?,last_seen_at=?,changed_at=? WHERE id=?", (*values, digest, stamp, stamp, local_id))
    else:
        cursor = conn.execute("INSERT INTO central_radius_856_records (source_id,table_rec_id," + ",".join(fields) +
            ",snapshot_digest,first_seen_at,last_seen_at,changed_at) VALUES(" + ",".join("?" for _ in range(18)) + ")",
            (source["id"], record_id, *values, digest, stamp, stamp, stamp))
        local_id = cursor.lastrowid
    conn.execute("INSERT INTO central_radius_856_events(record_id,observed_at,record_status,error_message,file_name) VALUES(?,?,?,?,?)",
                 (local_id, stamp, int(row["RecordStatus"]), _text(row["ErrorMessage"]), _text(row["FileName"])))
    return 1


def sync_source(db_path, source_id, *, reader=None, page_size=500):
    owner = uuid4().hex
    with session(db_path) as conn:
        source = conn.execute("SELECT * FROM central_radius_sources WHERE id=?", (source_id,)).fetchone()
        if not source or not source["enabled"]:
            return 0
        if source["environment"] == "LIVE" and os.getenv("EDI_CENTRAL_LIVE_OBSERVATION") != "1":
            raise ValueError("LIVE read-only observation is not authorized in this worker")
        stamp = now_utc()
        expires = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat(timespec="seconds")
        claimed = conn.execute("UPDATE central_radius_sources SET lease_owner=?,lease_until=? WHERE id=? AND (lease_until IS NULL OR lease_until<=?)",
                               (owner, expires, source_id, stamp))
        if not claimed.rowcount:
            return 0
    try:
        reader = reader or ShippingNotesReader(source["environment"])
        recent, history = reader.read_pages(source["company_number"], source["recent_cursor"], source["history_cursor"], page_size=page_size)
        with session(db_path) as conn:
            # Pausing during an ODBC read also prevents new local indexing.
            current = conn.execute("SELECT enabled,lease_owner FROM central_radius_sources WHERE id=?", (source_id,)).fetchone()
            if not current["enabled"] or current["lease_owner"] != owner:
                return 0
            changed = sum(store_record(conn, source, row) for row in recent + history)
            next_recent = int(recent[-1]["TableRecId"]) if len(recent) == page_size else None
            next_history = int(history[-1]["TableRecId"]) if len(history) == page_size else None
            conn.execute("UPDATE central_radius_sources SET recent_cursor=?,history_cursor=?,history_sweeps=history_sweeps+?,last_scan_at=?,last_error=NULL WHERE id=?",
                         (next_recent, next_history, int(len(history) < page_size), now_utc(), source_id))
        return changed
    except Exception as exc:
        with session(db_path) as conn:
            conn.execute("UPDATE central_radius_sources SET last_scan_at=?,last_error=? WHERE id=?",
                         (now_utc(), str(exc)[:1000], source_id))
        raise
    finally:
        with session(db_path) as conn:
            conn.execute("UPDATE central_radius_sources SET lease_owner=NULL,lease_until=NULL WHERE id=? AND lease_owner=?",
                         (source_id, owner))
