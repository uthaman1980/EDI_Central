"""Validated, atomic customer configuration for the local admin application."""

import json
import os
import re
import tempfile
from pathlib import Path


KEY = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
KINDS = {"van": "customers", "direct": "x12_partners"}


def load_config(path: Path) -> dict:
    if not path.exists():
        return {"environment": "TEST", "customers": {}, "x12_partners": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a JSON object")
    data.setdefault("customers", {})
    data.setdefault("x12_partners", {})
    if not isinstance(data["customers"], dict) or not isinstance(data["x12_partners"], dict):
        raise ValueError("Customer profile collections must be JSON objects")
    return data


def save_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def parse_map(value: str, label: str) -> dict[str, str]:
    result = {}
    for number, raw in enumerate(value.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"{label} line {number} must be SOURCE=RADIUS_CODE")
        source, target = (part.strip() for part in line.split("=", 1))
        if not source or not target or source in result:
            raise ValueError(f"{label} line {number} is empty or duplicated")
        result[source] = target
    return result


def format_map(value: dict) -> str:
    return "\n".join(f"{source}={target}" for source, target in sorted(value.items()))


def profile_from_form(form) -> tuple[str, str, dict]:
    kind = form.get("kind", "")
    if kind not in KINDS:
        raise ValueError("Choose Direct customer 850 or VAN transposed file")
    key = form.get("key", "").strip()
    if not KEY.fullmatch(key):
        raise ValueError("Customer routing ID must use 1 to 32 letters, numbers, _ or -")
    cust_code = form.get("cust_code", "").strip()
    plant = form.get("plant_code", "").strip()
    try:
        company = int(form.get("comp_num", ""))
    except ValueError as exc:
        raise ValueError("Company number must be a positive integer") from exc
    if company <= 0 or not cust_code or not plant:
        raise ValueError("Radius customer, company, and plant are required")
    profile = {
        "display_name": form.get("display_name", "").strip() or key,
        "enabled": form.get("enabled") == "on",
        "cust_code": cust_code,
        "comp_num": company,
        "plant_code": plant,
        "item_identifier": form.get("item_identifier", "").strip().upper(),
        "item_map": parse_map(form.get("item_map", ""), "Item map") if kind == "direct" else {},
        "uom_map": parse_map(form.get("uom_map", ""), "Order unit map"),
        "price_unit_map": parse_map(form.get("price_unit_map", ""), "Price unit map"),
        "address_map": parse_map(form.get("address_map", ""), "Address map") if kind == "direct" else {},
    }
    shipping_request_mode = form.get("shipping_request_mode", "combined")
    if shipping_request_mode not in {"combined", "per_line"}:
        raise ValueError("Shipping request creation must be combined or one per line")
    profile["shipping_request_mode"] = shipping_request_mode
    if kind == "van":
        profile["item_identifier"] = "CI"
        profile["item_map"] = {}
    else:
        transport = form.get("transport", "as2")
        if transport not in {"as2", "trusted_drop"}:
            raise ValueError("Direct transport must be verified AS2 or controlled file drop")
        profile.update({
            "transport": transport,
            "as2_sender": form.get("as2_sender", "").strip(),
            "as2_receiver": form.get("as2_receiver", "").strip(),
            "isa_receiver": form.get("isa_receiver", "").strip(),
        })
        if not profile["item_identifier"]:
            raise ValueError("X12 item identifier qualifier is required")
    if profile["enabled"]:
        if not all((profile["uom_map"], profile["price_unit_map"])):
            raise ValueError("Enabled customers require order unit and price unit mappings")
        if kind == "direct" and not profile["item_map"]:
            raise ValueError("Enabled direct customers require an item mapping")
        if kind == "direct" and not profile["isa_receiver"]:
            raise ValueError("Enabled direct customers require ISA receiver")
        if kind == "direct" and profile["transport"] == "as2" and not all((profile["as2_sender"], profile["as2_receiver"])):
            raise ValueError("Enabled AS2 customers require AS2 sender and receiver")
    return kind, key, profile
