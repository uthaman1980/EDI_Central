"""Local operator console for customer setup and inbound orders."""

import hmac
import hashlib
import json
import os
import secrets
import shutil
import threading
from uuid import uuid4
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, abort, redirect, render_template, request, url_for

from .audit import connect
from .customer_config import KINDS, format_map, load_config, profile_from_form, save_config
from .edi import parse_850
from .fixed850 import parse_fixed_850
from .service import initialize, process_file


STATUS_LABELS = {
    "received": "Received", "awaiting_receipt": "Awaiting AS2 receipt",
    "processing": "Processing", "previewed": "Previewed",
    "complete": "Completed", "attention": "Needs review", "rejected": "Rejected",
    "superseded": "Superseded",
}


def _json_list(value):
    try:
        result = json.loads(value or "[]")
        return result if isinstance(result, list) else []
    except ValueError:
        return []


def _format_row(row):
    item = dict(row)
    for key in ("po_refs", "so_numbers", "shipreq_numbers"):
        item[key] = _json_list(item[key])
    item["status_label"] = STATUS_LABELS.get(item["status"], item["status"].replace("_", " ").title())
    return item


def _saved_source(row, inbox: Path) -> Path | None:
    """Find archived bytes by their recorded digest, including watcher prefixes."""
    original = Path(row["source_path"])
    candidates = [original]
    for folder in ("previews", "failed", "processed", "manual"):
        directory = inbox.parent / folder
        candidates.append(directory / row["filename"])
        candidates.extend(directory.glob("*-" + row["filename"]))
    for path in candidates:
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == row["digest"]:
            return path
    return None


def create_app(db_path: Path, *, username=None, password=None,
               config_path=Path("config.json"), inbox_path=Path("inbox"), radius_client=None,
               resolver=None):
    user = username or os.getenv("DASHBOARD_USER")
    secret = password or os.getenv("DASHBOARD_PASSWORD")
    if not user or not secret:
        raise ValueError("Set DASHBOARD_USER and DASHBOARD_PASSWORD before starting the dashboard")
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["DB_PATH"] = Path(db_path)
    app.config["CONFIG_PATH"] = Path(config_path)
    app.config["INBOX_PATH"] = Path(inbox_path)
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
    initialize(app.config["DB_PATH"]).close()
    connect(app.config["DB_PATH"]).close()
    csrf_token = secrets.token_urlsafe(32)
    config_lock = threading.RLock()
    from .central_ui import register_central_routes
    register_central_routes(app, csrf_token)

    @app.before_request
    def require_auth():
        auth = request.authorization
        if not auth or not (hmac.compare_digest(auth.username or "", user)
                            and hmac.compare_digest(auth.password or "", secret)):
            return Response("Administrator credentials required", 401,
                            {"WWW-Authenticate": 'Basic realm="Radius EDI Central", charset="UTF-8"'})

    @app.after_request
    def secure_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'none'; object-src 'none'; base-uri 'none'"
        return response

    @app.get("/")
    def index():
        status = request.args.get("status", "all")
        if status != "all" and status not in STATUS_LABELS:
            abort(400)
        query = request.args.get("q", "").strip()[:100]
        try:
            page = max(1, int(request.args.get("page", "1")))
        except ValueError:
            abort(400)
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        since = (now - timedelta(days=6)).date().isoformat()
        with closing(connect(app.config["DB_PATH"])) as conn:
            total = conn.execute("SELECT COUNT(*) FROM files WHERE substr(received_at,1,10)=? AND status<>'superseded'", (today,)).fetchone()[0]
            completed = conn.execute("SELECT COUNT(*) FROM files WHERE substr(received_at,1,10)=? AND status='complete'", (today,)).fetchone()[0]
            exceptions = conn.execute("SELECT COUNT(*) FROM files WHERE status IN ('attention','rejected')").fetchone()[0]
            active = conn.execute("SELECT COUNT(*) FROM files WHERE status IN ('received','awaiting_receipt','processing')").fetchone()[0]
            params = []
            where = []
            if status != "all":
                where.append("status=?")
                params.append(status)
            if query:
                where.append("(filename LIKE ? OR po_refs LIKE ? OR so_numbers LIKE ? OR shipreq_numbers LIKE ? OR error LIKE ?)")
                params.extend([f"%{query}%"] * 5)
            filtered = " FROM files" + (" WHERE " + " AND ".join(where) if where else "")
            matching = conn.execute("SELECT COUNT(*)" + filtered, params).fetchone()[0]
            pages = max(1, (matching + 49) // 50)
            page = min(page, pages)
            sql = "SELECT *" + filtered + " ORDER BY id DESC LIMIT 50 OFFSET ?"
            files = [_format_row(row) for row in conn.execute(sql, (*params, (page - 1) * 50)).fetchall()]
            events = [dict(row) for row in conn.execute("""SELECT e.*, f.filename FROM file_events e
                JOIN files f ON f.id=e.file_id ORDER BY e.id DESC LIMIT 12""").fetchall()]
            trend_rows = conn.execute("""SELECT substr(received_at,1,10) day,
                COUNT(*) total, SUM(CASE WHEN status='complete' THEN 1 ELSE 0 END) done
                FROM files WHERE substr(received_at,1,10)>=? AND status<>'superseded' GROUP BY day""", (since,)).fetchall()
        trend = {row["day"]: (row["total"], row["done"]) for row in trend_rows}
        days = [(now.date() - timedelta(days=6 - offset)).isoformat() for offset in range(7)]
        chart = [{"day": day, "label": datetime.fromisoformat(day).strftime("%a"),
                  "total": trend.get(day, (0, 0))[0], "done": trend.get(day, (0, 0))[1]} for day in days]
        peak = max([point["total"] for point in chart] + [1])
        for point in chart:
            point["height"] = max(4, round(point["total"] / peak * 100)) if point["total"] else 4
            point["done_height"] = round(point["done"] / peak * 100)
        return render_template("dashboard.html", title="Overview", selected="overview", files=files,
                               events=events, chart=chart, total=total, completed=completed,
                               exceptions=exceptions, active=active,
                               completion_rate=round(completed / total * 100) if total else 0,
                               status=status, query=query, status_labels=STATUS_LABELS,
                               page=page, pages=pages, matching=matching,
                               updated=now.strftime("%b %d, %Y · %H:%M UTC"))

    @app.get("/files/<int:file_id>")
    def file_detail(file_id):
        with closing(connect(app.config["DB_PATH"])) as conn:
            row = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
            if row is None:
                abort(404)
            events = [dict(item) for item in conn.execute(
                "SELECT * FROM file_events WHERE file_id=? ORDER BY id DESC", (file_id,)).fetchall()]
            radius_records = [dict(item) for item in conn.execute(
                "SELECT * FROM radius_records WHERE file_id=? ORDER BY id", (file_id,)).fetchall()]
            completed_pos = {item[0].split("|")[1] for item in conn.execute("""SELECT business_key FROM orders
                WHERE digest=? AND state='complete' AND instr(business_key,'|resubmit:')=0""",
                (row["digest"],)).fetchall()}
            po_refs = _json_list(row["po_refs"])
            has_completed_po = bool(po_refs) and all(po in completed_pos for po in po_refs)
            completed_so_options = sorted({item[1] for item in conn.execute(
                "SELECT business_key,so_number FROM orders WHERE digest=? AND state='complete' AND so_number IS NOT NULL",
                (row["digest"],)).fetchall() if item[0].split("|")[1] in po_refs}, reverse=True)
        for record in radius_records:
            record["request"] = json.loads(record.pop("request_json"))
            record["response"] = json.loads(record.pop("response_json"))
        return render_template("file_detail.html", title="File details", selected="files",
                               file=_format_row(row), events=events, radius_records=radius_records,
                               csrf_token=csrf_token,
                               has_completed_po=has_completed_po, resubmission_token=uuid4().hex,
                               completed_so_options=completed_so_options,
                               allow_saved_submit=load_config(app.config["CONFIG_PATH"]).get("environment") == "TEST")

    def checked_csrf():
        if not hmac.compare_digest(request.form.get("csrf_token", ""), csrf_token):
            abort(403, description="This form expired when the web application restarted. Open Process a file again, select the file, and resubmit it.")

    @app.post("/files/<int:file_id>/submit")
    def submit_saved_file(file_id):
        checked_csrf()
        cfg = load_config(app.config["CONFIG_PATH"])
        if cfg.get("environment") != "TEST":
            abort(400, "Web submission is restricted to Radius TEST")
        with closing(connect(app.config["DB_PATH"])) as conn:
            row = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        if row is None:
            abort(404)
        if row["environment"] != "TEST" or row["status"] not in {"previewed", "rejected", "complete"}:
            abort(409, "This file cannot be submitted automatically; review its processing history")
        mode = request.form.get("resubmit_mode")
        token = request.form.get("resubmission_token")
        if mode is not None and (mode not in {"new", "update"} or not token or len(token) != 32 or any(c not in "0123456789abcdef" for c in token)):
            abort(400, "Invalid resubmission choice")
        if mode == "update":
            abort(409, "Radius Sales Order and Shipping Request updates are on hold pending Radius investigation")
        target_so_number = None
        if mode == "update":
            po_refs = _json_list(row["po_refs"])
            if len(po_refs) != 1:
                abort(400, "Choose a file containing one PO to update an existing Sales Order")
            try:
                target_so_number = int(request.form.get("target_so_number", ""))
            except ValueError:
                abort(400, "Choose the Sales Order number to update")
            with closing(connect(app.config["DB_PATH"])) as conn:
                valid_target = conn.execute("""SELECT business_key FROM orders WHERE digest=? AND state='complete'
                    AND so_number=? LIMIT 1""", (row["digest"], target_so_number)).fetchone()
            if not valid_target or valid_target[0].split("|")[1] != po_refs[0]:
                abort(400, "The selected Sales Order is not a completed record for this file")
        entries = [(kind, key, profile.get("display_name", key)) for kind, bucket in KINDS.items()
                   for key, profile in cfg[bucket].items() if profile.get("enabled", True)]
        if row["status"] == "complete" and not mode:
            outcome = [{"file_id": file_id, "po_numbers": _json_list(row["po_refs"]),
                        "so_numbers": _json_list(row["so_numbers"]),
                        "shipreq_numbers": _json_list(row["shipreq_numbers"]),
                        "state": "already_complete"}]
            return render_template("inbound.html", entries=entries, csrf_token=csrf_token,
                                   outcome=outcome, error=None, environment="TEST", action="submit",
                                   resubmission_token=uuid4().hex)
        source = _saved_source(row, app.config["INBOX_PATH"])
        if source is None:
            abort(409, "The saved file is missing or its contents changed; upload the original file again")
        meta = Path(str(source) + ".as2.json")
        try:
            result = process_file(source, meta, cfg, app.config["DB_PATH"], submit=True,
                                  client=radius_client, resolver=resolver,
                                  resubmit_mode=mode, resubmission_token=token,
                                  target_so_number=target_so_number)
        except (ValueError, RuntimeError, OSError) as exc:
            if source.parent.name == "previews":
                failed = app.config["INBOX_PATH"].parent / "failed"
                failed.mkdir(exist_ok=True)
                shutil.move(str(source), str(failed / source.name))
                if meta.exists():
                    shutil.move(str(meta), str(failed / meta.name))
            return render_template("inbound.html", entries=entries, csrf_token=csrf_token,
                                   outcome=None, error=str(exc), environment="TEST"), 400
        if source.parent.name in {"previews", "failed"}:
            processed = app.config["INBOX_PATH"].parent / "processed"
            processed.mkdir(exist_ok=True)
            shutil.move(str(source), str(processed / source.name))
            if meta.exists():
                shutil.move(str(meta), str(processed / meta.name))
        return render_template("inbound.html", entries=entries, csrf_token=csrf_token,
                               outcome=result, error=None, environment="TEST", action="submit",
                               resubmit_mode=mode, resubmission_token=uuid4().hex)

    def profile_form(kind, key, profile):
        return {**profile, "kind": kind, "key": key,
                **{field: format_map(profile.get(field, {})) for field in
                   ("item_map", "uom_map", "price_unit_map", "address_map")}}

    @app.get("/customers")
    def customers():
        config = load_config(app.config["CONFIG_PATH"])
        entries = [(kind, key, profile) for kind, bucket in KINDS.items()
                   for key, profile in config[bucket].items()]
        entries.sort(key=lambda item: (item[2].get("display_name", item[1]).lower(), item[0]))
        return render_template("customers.html", entries=entries, csrf_token=csrf_token,
                               test_url=os.getenv("RADIUS_API_URL", "http://P-RAD-FSM-EF.fsm.corp:8081/radadapter/radius/api"),
                               environment=config.get("environment", "TEST"))

    @app.get("/customers/new")
    def new_customer():
        kind = request.args.get("kind", "van")
        if kind not in KINDS:
            abort(400)
        form = {"kind": kind, "key": "", "display_name": "", "enabled": False,
                "comp_num": 2, "plant_code": "2", "cust_code": "", "item_identifier": "CI" if kind == "van" else "VN",
                "item_map": "", "uom_map": "", "price_unit_map": "", "address_map": "",
                "shipping_request_mode": "combined",
                "transport": "as2", "as2_sender": "", "as2_receiver": "", "isa_receiver": ""}
        return render_template("customer_form.html", form=form, csrf_token=csrf_token,
                               action=url_for("new_customer_save"), error=None, editing=False)

    @app.post("/customers/new")
    def new_customer_save():
        checked_csrf()
        try:
            kind, key, profile = profile_from_form(request.form)
            with config_lock:
                config = load_config(app.config["CONFIG_PATH"])
                if key in config[KINDS[kind]]:
                    raise ValueError("That customer routing ID already exists")
                config[KINDS[kind]][key] = profile
                save_config(app.config["CONFIG_PATH"], config)
        except ValueError as exc:
            return render_template("customer_form.html", form=request.form, csrf_token=csrf_token,
                                   action=url_for("new_customer_save"), error=str(exc), editing=False), 400
        return redirect(url_for("customers"))

    @app.get("/customers/<kind>/<key>")
    def edit_customer(kind, key):
        if kind not in KINDS:
            abort(404)
        config = load_config(app.config["CONFIG_PATH"])
        profile = config[KINDS[kind]].get(key)
        if profile is None:
            abort(404)
        return render_template("customer_form.html", form=profile_form(kind, key, profile),
                               csrf_token=csrf_token, action=url_for("edit_customer_save", kind=kind, key=key),
                               error=None, editing=True)

    @app.post("/customers/<kind>/<key>")
    def edit_customer_save(kind, key):
        checked_csrf()
        if kind not in KINDS:
            abort(404)
        try:
            new_kind, new_key, profile = profile_from_form(request.form)
            if (new_kind, new_key) != (kind, key):
                raise ValueError("Routing ID and source type cannot be changed; create a new customer")
            with config_lock:
                config = load_config(app.config["CONFIG_PATH"])
                if key not in config[KINDS[kind]]:
                    abort(404)
                config[KINDS[kind]][key] = profile
                save_config(app.config["CONFIG_PATH"], config)
        except ValueError as exc:
            return render_template("customer_form.html", form=request.form, csrf_token=csrf_token,
                                   action=url_for("edit_customer_save", kind=kind, key=key),
                                   error=str(exc), editing=True), 400
        return redirect(url_for("customers"))

    @app.get("/inbound")
    def inbound():
        config = load_config(app.config["CONFIG_PATH"])
        entries = [(kind, key, profile.get("display_name", key)) for kind, bucket in KINDS.items()
                   for key, profile in config[bucket].items() if profile.get("enabled", True)]
        return render_template("inbound.html", entries=entries, csrf_token=csrf_token,
                               outcome=None, error=None, environment=config.get("environment", "TEST"))

    @app.post("/inbound")
    def inbound_submit():
        checked_csrf()
        config = load_config(app.config["CONFIG_PATH"])
        if config.get("environment") != "TEST":
            abort(400, "Web submission is restricted to Radius TEST")
        entries = [(kind, key, profile.get("display_name", key)) for kind, bucket in KINDS.items()
                   for key, profile in config[bucket].items() if profile.get("enabled", True)]
        route = request.form.get("customer", "")
        kind, _, key = route.partition(":")
        if kind not in KINDS or key not in config[KINDS[kind]] or not config[KINDS[kind]][key].get("enabled", True):
            abort(400, "Choose an enabled customer")
        action = request.form.get("action")
        if action not in {"preview", "submit"}:
            abort(400)
        upload = request.files.get("file")
        if not upload or not upload.filename:
            abort(400, "Select an 850 file")
        data = upload.read()
        expected_suffix = ".txt" if kind == "van" else ".edi"
        if not upload.filename.lower().endswith(expected_suffix):
            abort(400, f"This customer requires a {expected_suffix} file")
        try:
            orders = parse_fixed_850(data) if kind == "van" else parse_850(data)
            if any(order.sender != key for order in orders):
                raise ValueError("File customer identifier does not match selected customer")
            receipt = request.files.get("receipt")
            receipt_data = receipt.read() if receipt and receipt.filename else None
            if kind == "direct" and config[KINDS[kind]][key].get("transport", "as2") == "as2" and not receipt_data:
                raise ValueError("Direct X12 files require the verified AS2 receipt JSON")
            if receipt_data:
                json.loads(receipt_data)
            inbox = app.config["INBOX_PATH"]
            destination_dir = inbox.parent / ("manual" if action == "submit" else "previews")
            destination_dir.mkdir(parents=True, exist_ok=True)
            original = Path(upload.filename).name[:100]
            safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in original)
            file_path = destination_dir / f"{uuid4().hex}-{safe_name}"
            file_path.write_bytes(data)
            meta_path = Path(str(file_path) + ".as2.json")
            if receipt_data:
                meta_path.write_bytes(receipt_data)
            try:
                result = process_file(file_path, meta_path, config, app.config["DB_PATH"],
                                      submit=action == "submit", client=radius_client, resolver=resolver)
            except Exception:
                if action == "submit":
                    failed = inbox.parent / "failed"
                    failed.mkdir(exist_ok=True)
                    shutil.move(str(file_path), str(failed / file_path.name))
                    if meta_path.exists():
                        shutil.move(str(meta_path), str(failed / meta_path.name))
                raise
            if action == "submit":
                processed = inbox.parent / "processed"
                processed.mkdir(exist_ok=True)
                shutil.move(str(file_path), str(processed / file_path.name))
                if meta_path.exists():
                    shutil.move(str(meta_path), str(processed / meta_path.name))
            preview_has_completed = False
            if action == "preview":
                with closing(connect(app.config["DB_PATH"])) as conn:
                    preview_has_completed = all(bool(conn.execute(
                        "SELECT 1 FROM orders WHERE business_key=? AND digest=? AND state='complete'",
                        (item["business_key"], hashlib.sha256(data).hexdigest())).fetchone()) for item in result)
            return render_template("inbound.html", entries=entries, csrf_token=csrf_token,
                                   outcome=result, error=None, environment="TEST", action=action,
                                   preview_has_completed=preview_has_completed,
                                   resubmission_token=uuid4().hex)
        except (ValueError, RuntimeError, OSError) as exc:
            return render_template("inbound.html", entries=entries, csrf_token=csrf_token,
                                   outcome=None, error=str(exc), environment="TEST"), 400

    return app
