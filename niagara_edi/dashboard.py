"""Read-only admin dashboard over the local ingestion audit database."""

import hmac
import json
import os
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, abort, render_template, request

from .audit import connect


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


def create_app(db_path: Path, *, username=None, password=None):
    user = username or os.getenv("DASHBOARD_USER")
    secret = password or os.getenv("DASHBOARD_PASSWORD")
    if not user or not secret:
        raise ValueError("Set DASHBOARD_USER and DASHBOARD_PASSWORD before starting the dashboard")
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["DB_PATH"] = Path(db_path)

    @app.before_request
    def require_auth():
        auth = request.authorization
        if not auth or not (hmac.compare_digest(auth.username or "", user)
                            and hmac.compare_digest(auth.password or "", secret)):
            return Response("Administrator credentials required", 401,
                            {"WWW-Authenticate": 'Basic realm="Niagara EDI Admin", charset="UTF-8"'})

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
                where.append("(filename LIKE ? OR po_refs LIKE ? OR error LIKE ?)")
                params.extend([f"%{query}%"] * 3)
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
        return render_template("file_detail.html", title="File details", selected="files",
                               file=_format_row(row), events=events)

    return app
