import argparse
import json
import logging
import shutil
import time
from pathlib import Path

from .service import process_file


def main():
    parser = argparse.ArgumentParser(description="Process verified Niagara AS2 850 handoff")
    parser.add_argument("edi", type=Path, nargs="?")
    parser.add_argument("--meta", type=Path, help="Gateway verification receipt JSON; defaults to <edi>.as2.json")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--state", type=Path, default=Path("state/orders.sqlite3"))
    parser.add_argument("--submit", action="store_true", help="POST to configured Radius environment")
    parser.add_argument("--watch", type=Path, help="Poll AS2 gateway inbox for *.edi plus *.edi.as2.json")
    parser.add_argument("--interval", type=int, default=10)
    args = parser.parse_args()
    if bool(args.edi) == bool(args.watch):
        parser.error("Provide either an EDI path or --watch")
    if args.interval < 1:
        parser.error("--interval must be positive")
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    if args.edi:
        meta = args.meta or Path(str(args.edi) + ".as2.json")
        result = process_file(args.edi, meta, cfg, args.state, submit=args.submit)
        print(json.dumps(result, indent=2))
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    inbox = args.watch
    inbox.mkdir(parents=True, exist_ok=True)
    processed = inbox.parent / "archive"
    processed.mkdir(exist_ok=True)
    while True:
        for edi in sorted(inbox.glob("*.edi")):
            meta = Path(str(edi) + ".as2.json")
            if not meta.exists():
                continue
            try:
                result = process_file(edi, meta, cfg, args.state, submit=args.submit)
                logging.info("Processed %s: %s", edi.name, result)
                for source in (edi, meta):
                    shutil.move(str(source), str(processed / source.name))
            except Exception:
                logging.exception("Failed %s; left in inbox for reconciliation", edi.name)
                time.sleep(args.interval)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
