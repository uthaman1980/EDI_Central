import argparse
import json
import logging
import shutil
import time
from uuid import uuid4
from pathlib import Path

from .audit import Audit
from .service import process_file


def scan_inbox(inbox: Path, cfg: dict, state: Path, *, submit: bool, client=None, resolver=None):
    inbox.mkdir(parents=True, exist_ok=True)
    processed = inbox.parent / "archive"
    processed.mkdir(exist_ok=True)
    failed = inbox.parent / "failed"
    failed.mkdir(exist_ok=True)
    audit = Audit(state)
    outcomes = []
    for edi in sorted(inbox.glob("*.edi")):
        meta = Path(str(edi) + ".as2.json")
        if not meta.exists():
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
        prefix = uuid4().hex[:12] + "-"
        for source in (edi, meta):
            if source.exists():
                shutil.move(str(source), str(destination / (prefix + source.name)))
        outcomes.append((edi.name, status))
    return outcomes


def main():
    parser = argparse.ArgumentParser(description="Process verified Niagara AS2 850 handoff")
    parser.add_argument("edi", type=Path, nargs="?")
    parser.add_argument("--meta", type=Path, help="Gateway verification receipt JSON; defaults to <edi>.as2.json")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--state", type=Path, default=Path("state/orders.sqlite3"))
    parser.add_argument("--submit", action="store_true", help="POST to configured Radius environment")
    parser.add_argument("--watch", type=Path, help="Poll AS2 gateway inbox for *.edi plus *.edi.as2.json")
    parser.add_argument("--dashboard", action="store_true", help="Serve the read-only admin dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Dashboard bind address")
    parser.add_argument("--port", type=int, default=8088, help="Dashboard port")
    parser.add_argument("--interval", type=int, default=10)
    args = parser.parse_args()
    if sum([bool(args.edi), bool(args.watch), args.dashboard]) != 1:
        parser.error("Provide exactly one of an EDI path, --watch, or --dashboard")
    if args.interval < 1:
        parser.error("--interval must be positive")
    if args.dashboard:
        from waitress import serve
        from .dashboard import create_app
        serve(create_app(args.state), host=args.host, port=args.port, threads=4)
        return
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    if args.edi:
        meta = args.meta or Path(str(args.edi) + ".as2.json")
        result = process_file(args.edi, meta, cfg, args.state, submit=args.submit)
        print(json.dumps(result, indent=2))
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    while True:
        scan_inbox(args.watch, cfg, args.state, submit=args.submit)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
