import argparse
import getpass
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import shutil
import sys
import time
import threading
from uuid import uuid4
from pathlib import Path

from .audit import Audit
from .customer_config import load_config
from .edi import EDIError, parse_850
from .service import process_file


def scan_inbox(inbox: Path, cfg: dict, state: Path, *, submit: bool, client=None, resolver=None):
    inbox.mkdir(parents=True, exist_ok=True)
    processed = inbox.parent / "processed"
    processed.mkdir(exist_ok=True)
    failed = inbox.parent / "failed"
    failed.mkdir(exist_ok=True)
    audit = Audit(state)
    outcomes = []
    for edi in sorted(p for p in inbox.iterdir() if p.is_file() and p.suffix.lower() in {".edi", ".txt"}):
        meta = Path(str(edi) + ".as2.json")
        fixed = edi.read_bytes()[:32].lstrip().startswith(b"EDI850Inbound")
        needs_receipt = not fixed
        if needs_receipt and cfg.get("x12_partners"):
            try:
                orders = parse_850(edi.read_bytes())
                profiles = cfg["x12_partners"]
                needs_receipt = not orders or any(
                    profiles.get(order.sender, {}).get("transport", "as2") != "trusted_drop"
                    for order in orders)
            except (EDIError, UnicodeDecodeError):
                needs_receipt = False  # Invalid X12 should be rejected, not held indefinitely.
        if needs_receipt and not meta.exists():
            file_id, _ = audit.start(edi, cfg["environment"])
            if audit.status(file_id)["status"] != "awaiting_receipt":
                audit.update(file_id, "awaiting_receipt", "AS2", "Waiting for AS2 verification receipt")
            outcomes.append((edi.name, "awaiting_receipt"))
            continue
        try:
            result = process_file(edi, meta, cfg, state, submit=submit, client=client, resolver=resolver)
            logging.info("Processed %s: %s", edi.name, result)
            destination, status = processed, "complete" if submit else "previewed"
        except Exception:
            logging.exception("Failed %s; moving to failed for reconciliation", edi.name)
            destination, status = failed, "failed"
        if submit or status == "failed":
            prefix = uuid4().hex[:12] + "-"
            for source in (edi, meta):
                if source.exists():
                    shutil.move(str(source), str(destination / (prefix + source.name)))
        outcomes.append((edi.name, status))
    return outcomes


def main():
    parser = argparse.ArgumentParser(description="Process X12 or fixed-width EDI850Inbound files")
    parser.add_argument("edi", type=Path, nargs="?")
    parser.add_argument("--meta", type=Path, help="Gateway verification receipt JSON; defaults to <edi>.as2.json")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--state", type=Path, default=Path("state/orders.sqlite3"))
    parser.add_argument("--submit", action="store_true", help="POST to configured Radius environment")
    parser.add_argument("--watch", type=Path, help="Poll inbox for fixed-width *.txt or verified X12 *.edi files")
    parser.add_argument("--dashboard", action="store_true", help="Serve the customer and operations web app")
    parser.add_argument("--central-worker", action="store_true", help="Run read-only per-capability folder observers")
    parser.add_argument("--observe", action="store_true", help="Run read-only observers alongside the dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Dashboard bind address")
    parser.add_argument("--port", type=int, default=8088, help="Dashboard port")
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--debug", action="store_true", help="Write decoded Radius POST and response details to a local log")
    parser.add_argument("--debug-log", type=Path, help="DEBUG log path; defaults to <state directory>/radius-debug.log")
    args = parser.parse_args()
    if sum([bool(args.edi), bool(args.watch), args.dashboard, args.central_worker]) != 1:
        parser.error("Provide exactly one of an EDI path, --watch, --dashboard, or --central-worker")
    if args.interval < 1:
        parser.error("--interval must be positive")
    if args.observe and not args.dashboard:
        parser.error("--observe requires --dashboard")
    if args.debug or args.debug_log:
        log_path = args.debug_log or args.state.parent / "radius-debug.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024,
                                      backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        radius_logger = logging.getLogger("niagara_edi.radius")
        radius_logger.setLevel(logging.DEBUG)
        radius_logger.addHandler(handler)
        radius_logger.propagate = False
        logging.info("Radius DEBUG log: %s", log_path)
    if args.dashboard:
        from waitress import serve
        from .dashboard import create_app
        user = os.getenv("DASHBOARD_USER")
        secret = os.getenv("DASHBOARD_PASSWORD")
        if not user or not secret:
            if not sys.stdin.isatty():
                parser.error("Set DASHBOARD_USER and DASHBOARD_PASSWORD for noninteractive use")
            if not user:
                user = input("Web administrator username [edi-admin]: ").strip() or "edi-admin"
            if not secret:
                secret = getpass.getpass("Web administrator password: ")
            if not secret:
                parser.error("Web administrator password cannot be empty")
        if not os.getenv("RADIUS_DB_PASS") and sys.stdin.isatty():
            db_password = getpass.getpass("Radius ODBC password for address lookup (Enter to skip): ")
            if db_password:
                os.environ["RADIUS_DB_PASS"] = db_password
        print(f"Web application: http://{args.host}:{args.port}/", flush=True)
        app = create_app(args.state, username=user, password=secret, config_path=args.config)
        if args.observe:
            from .central import run_observer_forever
            threading.Thread(target=run_observer_forever, args=(args.state, args.interval),
                             name="radius-file-observer", daemon=True).start()
        serve(app, host=args.host, port=args.port, threads=4)
        return
    if args.central_worker:
        from .central import migrate, run_observer_forever
        migrate(args.state)
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        run_observer_forever(args.state, args.interval)
        return
    cfg = load_config(args.config)
    if args.edi:
        meta = args.meta or Path(str(args.edi) + ".as2.json")
        result = process_file(args.edi, meta, cfg, args.state, submit=args.submit)
        print(json.dumps(result, indent=2))
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    while True:
        cfg = load_config(args.config)
        scan_inbox(args.watch, cfg, args.state, submit=args.submit)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
