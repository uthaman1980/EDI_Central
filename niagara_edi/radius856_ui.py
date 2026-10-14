"""Authenticated 856 export history across customers, sourced from Radius SQL."""

import ntpath
import os
import sqlite3
from datetime import date

from flask import abort, redirect, render_template, request, url_for

from .central import now_utc, session
from .radius856_monitor import STATUS_LABELS, connection_settings, sync_source


def register_radius856_routes(app, checked, csrf_token):
    @app.get("/central/856-monitor")
    def radius856_monitor():
        query = request.args.get("q", "").strip()[:100]
        customer = request.args.get("customer", "").strip()[:32]
        status = request.args.get("status", "")
        source_id = request.args.get("source", "")
        start, end = request.args.get("start", ""), request.args.get("end", "")
        try:
            page = max(1, int(request.args.get("page", "1")))
            if status and status not in {"0", "1", "9"}:
                raise ValueError()
            if source_id:
                int(source_id)
            for value in (start, end):
                if value:
                    date.fromisoformat(value)
        except ValueError:
            abort(400, "Invalid status, source, date or page filter")
        clauses, args = ["1=1"], []
        if query:
            clauses.append("(r.customer_code LIKE ? OR CAST(r.shipreq_number AS TEXT) LIKE ? OR r.file_name LIKE ? OR CAST(r.table_rec_id AS TEXT) LIKE ?)")
            args.extend([f"%{query}%"] * 4)
        for value, clause in ((customer, "r.customer_code=?"), (status, "r.record_status=?"),
                              (source_id, "r.source_id=?"), (start, "r.creation_date>=?"), (end, "r.creation_date<=?")):
            if value:
                clauses.append(clause)
                args.append(value)
        where = " WHERE " + " AND ".join(clauses)
        with session(app.config["DB_PATH"]) as conn:
            sources = [dict(row) for row in conn.execute("SELECT * FROM central_radius_sources ORDER BY environment,company_number")]
            customers = [row[0] for row in conn.execute("SELECT DISTINCT customer_code FROM central_radius_856_records ORDER BY customer_code")]
            counts = {row[0]: row[1] for row in conn.execute("SELECT r.record_status,COUNT(*) FROM central_radius_856_records r" + where + " GROUP BY r.record_status", args)}
            total = sum(counts.values())
            rows = [dict(row) for row in conn.execute("""SELECT r.*,s.environment FROM central_radius_856_records r
                JOIN central_radius_sources s ON s.id=r.source_id""" + where +
                " ORDER BY r.creation_date DESC,r.table_rec_id DESC LIMIT 100 OFFSET ?", (*args, (page - 1) * 100))]
        return render_template("radius856_monitor.html", sources=sources, customers=customers, rows=rows,
            counts=counts, total=total, labels=STATUS_LABELS, query=query, customer=customer, status=status,
            source_id=source_id, start=start, end=end, page=page, pages=max(1, (total + 99) // 100),
            filter_args={"q": query, "customer": customer, "status": status, "source": source_id,
                         "start": start, "end": end}, csrf_token=csrf_token)

    @app.post("/central/856-monitor/sources")
    def radius856_source_create():
        checked()
        environment = request.form.get("environment", "TEST")
        try:
            company = int(request.form.get("company", ""))
            if environment not in {"TEST", "LIVE"} or company <= 0:
                raise ValueError()
            with session(app.config["DB_PATH"]) as conn:
                conn.execute("INSERT INTO central_radius_sources(environment,company_number) VALUES(?,?)", (environment, company))
        except (ValueError, sqlite3.IntegrityError):
            abort(400, "Choose TEST/LIVE and a positive company; duplicate sources are not allowed")
        return redirect(url_for("radius856_monitor"))

    @app.post("/central/856-monitor/sources/<int:source_id>/<action>")
    def radius856_source_action(source_id, action):
        checked()
        with session(app.config["DB_PATH"]) as conn:
            source = conn.execute("SELECT * FROM central_radius_sources WHERE id=?", (source_id,)).fetchone()
            if not source:
                abort(404)
        if action not in {"enable", "pause", "sync"}:
            abort(404)
        try:
            if action != "pause":
                if source["environment"] == "LIVE" and os.getenv("EDI_CENTRAL_LIVE_OBSERVATION") != "1":
                    raise ValueError("LIVE read-only observation requires explicit authorization")
                connection_settings(source["environment"])
            if action == "sync":
                if not source["enabled"]:
                    raise ValueError("Enable the database source before syncing")
                sync_source(app.config["DB_PATH"], source_id)
            else:
                with session(app.config["DB_PATH"]) as conn:
                    conn.execute("UPDATE central_radius_sources SET enabled=? WHERE id=?", (int(action == "enable"), source_id))
                    conn.execute("INSERT INTO central_events(created_at,level,stage,message) VALUES(?,?,?,?)",
                        (now_utc(), "info", "856 database monitor", f"Source {source_id} {action}"))
        except (ValueError, RuntimeError) as exc:
            abort(409, str(exc))
        return redirect(url_for("radius856_monitor", source=source_id))

    @app.get("/central/856-monitor/records/<int:record_id>")
    def radius856_record(record_id):
        with session(app.config["DB_PATH"]) as conn:
            record = conn.execute("""SELECT r.*,s.environment FROM central_radius_856_records r
                JOIN central_radius_sources s ON s.id=r.source_id WHERE r.id=?""", (record_id,)).fetchone()
            if not record:
                abort(404)
            events = [dict(row) for row in conn.execute("SELECT * FROM central_radius_856_events WHERE record_id=? ORDER BY id", (record_id,))]
            matches = [dict(row) for row in conn.execute("""SELECT DISTINCT a.id,a.filename,d.radius_bol_number,l.radius_so_number
                FROM central_documents d JOIN central_artifacts a ON a.id=d.artifact_id
                JOIN central_capabilities c ON c.id=a.capability_id
                LEFT JOIN central_document_lines l ON l.document_id=d.id
                WHERE c.environment=? AND d.company_number=? AND d.plant_code=?
                AND d.radius_customer_code=? AND d.radius_shipreq_number=?""",
                (record["environment"], record["company_number"], record["plant_code"], record["customer_code"], record["shipreq_number"]))]
        basename = ntpath.basename(record["file_name"]).lower()
        matches = [row for row in matches if not basename or row["filename"].lower() == basename]
        return render_template("radius856_record.html", record=dict(record), events=events, matches=matches, labels=STATUS_LABELS)
