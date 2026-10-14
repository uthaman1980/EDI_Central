"""Operator pages for central partner, capability, and observer configuration."""

import hmac
import os
import sqlite3

from flask import abort, redirect, render_template, request, url_for

from .central import (CATALOG, create_capability, create_partner, migrate,
                      scan_observer_once, session, set_enabled, set_partner_enabled,
                      update_capability)
from .customer_config import load_config


def _allowed_roots():
    return tuple(part for part in os.getenv("EDI_CENTRAL_ALLOWED_ROOTS", "").split(";") if part.strip())


def register_central_routes(app, csrf_token):
    migrate(app.config["DB_PATH"])

    def checked():
        if not hmac.compare_digest(request.form.get("csrf_token", ""), csrf_token):
            abort(403, "Form token expired; reload the page")

    from .radius856_ui import register_radius856_routes
    register_radius856_routes(app, checked, csrf_token)

    @app.route("/central/850-inspect", methods=["GET", "POST"])
    def central_850_inspect():
        reports, error, filename = [], None, ""
        if request.method == "POST":
            checked()
            upload = request.files.get("file")
            if not upload or not upload.filename:
                abort(400, "Choose a Radius inbound 850 file")
            filename = upload.filename
            from .fixed850 import inspect_fixed_850
            try:
                reports = inspect_fixed_850(upload.read())
                profiles = load_config(app.config["CONFIG_PATH"])["customers"]
                for report in reports:
                    profile = profiles.get(report["customer_code"])
                    report["profile_found"] = profile is not None
                    report["radius_customer"] = profile.get("cust_code", "") if profile else ""
                    report["profile_enabled"] = bool(profile and profile.get("enabled", True))
            except (ValueError, OSError) as exc:
                error = str(exc)
        return render_template("central_850_inspect.html", reports=reports, error=error,
                               filename=filename, csrf_token=csrf_token), 400 if error else 200

    @app.route("/central/856-preview", methods=["GET", "POST"])
    def central_856_preview():
        documents, error, filename = [], None, ""
        if request.method == "POST":
            checked()
            upload = request.files.get("file")
            if not upload or not upload.filename:
                abort(400, "Choose a Radius outbound 856 sample")
            filename = upload.filename
            from .outbound856 import parse_outbound_856
            try:
                documents = parse_outbound_856(upload.read())
            except (ValueError, UnicodeError) as exc:
                error = str(exc)
        return render_template("central_856_preview.html", documents=documents, error=error,
                               filename=filename, csrf_token=csrf_token), 400 if error else 200

    @app.get("/central")
    def central_home():
        with session(app.config["DB_PATH"]) as conn:
            partners = [dict(row) for row in conn.execute("SELECT * FROM central_partners ORDER BY display_name")]
            capabilities = [dict(row) for row in conn.execute("""SELECT c.*,p.partner_code,p.display_name
                FROM central_capabilities c JOIN central_partners p ON p.id=c.partner_id
                ORDER BY p.display_name,c.direction,c.message_type""")]
            artifact_count = conn.execute("SELECT COUNT(*) FROM central_artifacts").fetchone()[0]
            recent = [dict(row) for row in conn.execute("""SELECT a.*,p.partner_code,c.direction,c.message_type
                FROM central_artifacts a JOIN central_capabilities c ON c.id=a.capability_id
                JOIN central_partners p ON p.id=c.partner_id ORDER BY a.id DESC LIMIT 30""")]
        legacy = load_config(app.config["CONFIG_PATH"])
        legacy_profiles = [(kind, code, profile) for kind, bucket in
            (("van", "customers"), ("direct", "x12_partners"))
            for code, profile in legacy.get(bucket, {}).items()]
        return render_template("central_home.html", partners=partners, capabilities=capabilities,
                               artifact_count=artifact_count, recent=recent, csrf_token=csrf_token,
                               catalog=CATALOG, allowed_roots=_allowed_roots(),
                               legacy_profiles=legacy_profiles)

    @app.post("/central/partners")
    def central_partner_create():
        checked()
        try:
            with session(app.config["DB_PATH"]) as conn:
                partner_id = create_partner(conn, partner_code=request.form.get("partner_code", "").strip(),
                    display_name=request.form.get("display_name", ""),
                    radius_customer_code=request.form.get("radius_customer_code", ""),
                    company_number=request.form.get("company_number", ""),
                    plant_code=request.form.get("plant_code", ""),
                    edi_identity=request.form.get("edi_identity", ""))
        except (ValueError, sqlite3.IntegrityError) as exc:
            abort(400, str(exc))
        return redirect(url_for("central_partner", partner_id=partner_id))

    @app.get("/central/partners/<int:partner_id>")
    def central_partner(partner_id):
        with session(app.config["DB_PATH"]) as conn:
            partner = conn.execute("SELECT * FROM central_partners WHERE id=?", (partner_id,)).fetchone()
            if not partner:
                abort(404)
            capabilities = [dict(row) for row in conn.execute(
                "SELECT * FROM central_capabilities WHERE partner_id=? ORDER BY direction,message_type", (partner_id,))]
        return render_template("central_partner.html", partner=dict(partner), capabilities=capabilities,
                               csrf_token=csrf_token, catalog=CATALOG)

    @app.post("/central/partners/<int:partner_id>")
    def central_partner_edit(partner_id):
        checked()
        with session(app.config["DB_PATH"]) as conn:
            if not conn.execute("SELECT 1 FROM central_partners WHERE id=?", (partner_id,)).fetchone():
                abort(404)
            name = request.form.get("display_name", "").strip()
            radius_code = request.form.get("radius_customer_code", "").strip()
            plant = request.form.get("plant_code", "").strip()
            try:
                company = int(request.form.get("company_number", ""))
            except ValueError:
                abort(400, "Company number must be positive")
            if not name or not radius_code or not plant or company <= 0:
                abort(400, "Name, Radius customer, company and plant are required")
            conn.execute("""UPDATE central_partners SET display_name=?,radius_customer_code=?,
                company_number=?,plant_code=?,edi_identity=?,updated_at=datetime('now') WHERE id=?""",
                (name, radius_code, company, plant, request.form.get("edi_identity", "").strip(), partner_id))
        return redirect(url_for("central_partner", partner_id=partner_id))

    @app.post("/central/partners/<int:partner_id>/enabled")
    def central_partner_enabled(partner_id):
        checked()
        with session(app.config["DB_PATH"]) as conn:
            try:
                set_partner_enabled(conn, partner_id, request.form.get("enabled") == "yes")
            except ValueError as exc:
                abort(404, str(exc))
        return redirect(url_for("central_partner", partner_id=partner_id))

    @app.post("/central/partners/<int:partner_id>/capabilities")
    def central_capability_create(partner_id):
        checked()
        try:
            with session(app.config["DB_PATH"]) as conn:
                capability_id = create_capability(conn, partner_id=partner_id,
                    environment=request.form.get("environment", "TEST"),
                    direction=request.form.get("direction", ""),
                    message_type=request.form.get("message_type", ""),
                    route_class=request.form.get("route_class", ""),
                    radius_message_active=None,
                    radius_inbound_folder=request.form.get("radius_inbound_folder", "").strip(),
                    radius_outbound_folder=request.form.get("radius_outbound_folder", "").strip(),
                    partner_inbound_folder=request.form.get("partner_inbound_folder", "").strip(),
                    partner_outbound_folder=request.form.get("partner_outbound_folder", "").strip(),
                    source_format=request.form.get("source_format", "").strip(),
                    destination_format=request.form.get("destination_format", "").strip(),
                    filename_pattern=request.form.get("filename_pattern", "*").strip(),
                    mapping_version=request.form.get("mapping_version", "").strip())
        except (ValueError, sqlite3.IntegrityError) as exc:
            abort(400, str(exc))
        return redirect(url_for("central_capability", capability_id=capability_id))

    @app.get("/central/capabilities/<int:capability_id>")
    def central_capability(capability_id):
        with session(app.config["DB_PATH"]) as conn:
            row = conn.execute("""SELECT c.*,p.partner_code,p.display_name FROM central_capabilities c
                JOIN central_partners p ON p.id=c.partner_id WHERE c.id=?""", (capability_id,)).fetchone()
            if not row:
                abort(404)
            cap = dict(row)
            artifacts = [dict(item) for item in conn.execute(
                "SELECT * FROM central_artifacts WHERE capability_id=? ORDER BY id DESC LIMIT 30", (capability_id,))]
            events = [dict(item) for item in conn.execute(
                "SELECT * FROM central_events WHERE capability_id=? ORDER BY id DESC LIMIT 30", (capability_id,))]
        return render_template("central_capability.html", cap=cap, artifacts=artifacts, events=events,
                               csrf_token=csrf_token, maturity=CATALOG[(cap["direction"], cap["message_type"])],
                               allowed_roots=_allowed_roots())

    @app.post("/central/capabilities/<int:capability_id>/edit")
    def central_capability_edit(capability_id):
        checked()
        active = request.form.get("radius_message_active", "unknown")
        active_value = None if active == "unknown" else 1 if active == "yes" else 0 if active == "no" else "invalid"
        try:
            with session(app.config["DB_PATH"]) as conn:
                update_capability(conn, capability_id,
                    operating_mode=request.form.get("operating_mode", "OBSERVE_ONLY"),
                    radius_message_active=active_value,
                    radius_contract_verified=int(request.form.get("radius_contract_verified") == "on"),
                    radius_format=request.form.get("radius_format", "").strip(),
                    source_format=request.form.get("source_format", "").strip(),
                    destination_format=request.form.get("destination_format", "").strip(),
                    format_version=request.form.get("format_version", "").strip(),
                    mapping_version=request.form.get("mapping_version", "").strip(),
                    transport=request.form.get("transport", "FOLDER").strip(),
                    radius_inbound_folder=request.form.get("radius_inbound_folder", "").strip(),
                    radius_outbound_folder=request.form.get("radius_outbound_folder", "").strip(),
                    partner_inbound_folder=request.form.get("partner_inbound_folder", "").strip(),
                    partner_outbound_folder=request.form.get("partner_outbound_folder", "").strip(),
                    working_folder=request.form.get("working_folder", "").strip(),
                    archive_folder=request.form.get("archive_folder", "").strip(),
                    quarantine_folder=request.form.get("quarantine_folder", "").strip(),
                    filename_pattern=request.form.get("filename_pattern", "*").strip(),
                    poll_seconds=int(request.form.get("poll_seconds", "60")))
        except (ValueError, sqlite3.IntegrityError) as exc:
            abort(400, str(exc))
        return redirect(url_for("central_capability", capability_id=capability_id))

    @app.post("/central/capabilities/<int:capability_id>/enabled")
    def central_capability_enabled(capability_id):
        checked()
        try:
            with session(app.config["DB_PATH"]) as conn:
                set_enabled(conn, capability_id, request.form.get("enabled") == "yes",
                            allowed_roots=_allowed_roots())
        except ValueError as exc:
            abort(409, str(exc))
        return redirect(url_for("central_capability", capability_id=capability_id))

    @app.post("/central/capabilities/<int:capability_id>/run")
    def central_capability_run(capability_id):
        checked()
        try:
            scan_observer_once(app.config["DB_PATH"], capability_id)
        except (ValueError, OSError) as exc:
            abort(409, str(exc))
        return redirect(url_for("central_capability", capability_id=capability_id))

    @app.get("/central/transactions")
    def central_transactions():
        query = request.args.get("q", "").strip()[:80]
        like = f"%{query}%"
        with session(app.config["DB_PATH"]) as conn:
            legacy = [dict(row) for row in conn.execute("""SELECT id,filename,status,stage,po_refs,
                so_numbers,shipreq_numbers,updated_at FROM files
                WHERE filename LIKE ? OR po_refs LIKE ? OR so_numbers LIKE ? OR shipreq_numbers LIKE ?
                ORDER BY id DESC LIMIT 100""", (like, like, like, like))]
            observed = [dict(row) for row in conn.execute("""SELECT a.*,p.partner_code,c.direction,c.message_type,c.environment
                FROM central_artifacts a JOIN central_capabilities c ON c.id=a.capability_id
                JOIN central_partners p ON p.id=c.partner_id
                WHERE a.filename LIKE ? OR a.digest LIKE ? OR p.partner_code LIKE ? OR EXISTS (
                    SELECT 1 FROM central_documents d LEFT JOIN central_document_lines l ON l.document_id=d.id
                    WHERE d.artifact_id=a.id AND (CAST(d.radius_bol_number AS TEXT) LIKE ?
                    OR CAST(d.radius_shipreq_number AS TEXT) LIKE ? OR CAST(l.radius_so_number AS TEXT) LIKE ?
                    OR l.customer_po LIKE ?))
                ORDER BY a.id DESC LIMIT 100""", (like, like, like, like, like, like, like))]
        return render_template("central_transactions.html", legacy=legacy, observed=observed,
                               query=query)

    @app.get("/central/artifacts/<int:artifact_id>")
    def central_artifact(artifact_id):
        with session(app.config["DB_PATH"]) as conn:
            row = conn.execute("""SELECT a.*,p.partner_code,c.direction,c.message_type,c.route_class,c.environment,
                c.radius_inbound_folder,c.radius_outbound_folder FROM central_artifacts a
                JOIN central_capabilities c ON c.id=a.capability_id
                JOIN central_partners p ON p.id=c.partner_id WHERE a.id=?""", (artifact_id,)).fetchone()
            if not row:
                abort(404)
            events = [dict(event) for event in conn.execute(
                "SELECT * FROM central_events WHERE artifact_id=? ORDER BY id", (artifact_id,))]
            documents = [dict(doc) for doc in conn.execute(
                "SELECT * FROM central_documents WHERE artifact_id=? ORDER BY id", (artifact_id,))]
            for doc in documents:
                doc["lines"] = [dict(line) for line in conn.execute(
                    "SELECT * FROM central_document_lines WHERE document_id=? ORDER BY id", (doc["id"],))]
        return render_template("central_artifact.html", artifact=dict(row), events=events, documents=documents)

    @app.get("/central/exceptions")
    def central_exceptions():
        with session(app.config["DB_PATH"]) as conn:
            legacy = [dict(row) for row in conn.execute("""SELECT id,filename,status,stage,error,updated_at
                FROM files WHERE status IN ('rejected','attention') ORDER BY id DESC LIMIT 100""")]
            observers = [dict(row) for row in conn.execute("""SELECT c.id,p.partner_code,c.direction,
                c.message_type,c.last_error FROM central_capabilities c JOIN central_partners p
                ON p.id=c.partner_id WHERE c.last_error IS NOT NULL ORDER BY c.updated_at DESC LIMIT 100""")]
            parse_failures = [dict(row) for row in conn.execute("""SELECT a.id,a.filename,a.parse_error,p.partner_code
                FROM central_artifacts a JOIN central_capabilities c ON c.id=a.capability_id
                JOIN central_partners p ON p.id=c.partner_id WHERE a.parse_status='REJECTED'
                ORDER BY a.id DESC LIMIT 100""")]
        return render_template("central_exceptions.html", legacy=legacy, observers=observers,
                               parse_failures=parse_failures)

    @app.get("/central/jobs")
    def central_jobs():
        with session(app.config["DB_PATH"]) as conn:
            capabilities = [dict(row) for row in conn.execute("""SELECT c.*,p.partner_code FROM central_capabilities c
                JOIN central_partners p ON p.id=c.partner_id ORDER BY c.id DESC""")]
            events = [dict(row) for row in conn.execute(
                "SELECT * FROM central_events ORDER BY id DESC LIMIT 50")]
            database_sources = [dict(row) for row in conn.execute("SELECT * FROM central_radius_sources ORDER BY id")]
        return render_template("central_jobs.html", capabilities=capabilities, events=events,
                               database_sources=database_sources)
