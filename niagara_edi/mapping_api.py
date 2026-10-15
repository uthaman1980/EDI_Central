"""Bridge a published direct-customer mapping into the existing SO/SR service."""

import json

from .central import now_utc, session
from .mapping_engine import mapped_orders
from .mappings import get_revision, partner_for, run_preview, scope_digest


def prepare_orders(data,db_path,file_id,revision_id,environment):
    if environment!="TEST":
        raise ValueError("Mapped direct-customer API submissions are available only in TEST")
    with session(db_path) as conn:
        identity=conn.execute("SELECT profile_id,revision FROM central_mapping_revisions WHERE id=?",(revision_id,)).fetchone()
        if not identity:
            raise ValueError("Mapping revision not found")
        profile=get_revision(conn,identity[0],identity[1])
        if (profile["state"]!="PUBLISHED" or profile["environment"]!=environment or
                profile["direction"]!="INBOUND" or profile["target_format"]!="RADIUS_API"):
            raise ValueError("Choose a published TEST inbound mapping for the direct Radius API")
        partner=partner_for(conn,profile)
        if not partner["enabled"]:
            raise ValueError("Enable the partner before preparing or submitting API orders")
        scope=scope_digest(profile,partner)
        publication_proof=conn.execute("""SELECT 1 FROM central_mapping_runs WHERE revision_id=?
            AND rules_digest=? AND scope_digest=? AND error_count=0 AND output_digest IS NOT NULL LIMIT 1""",
            (revision_id,profile["rules_digest"],scope)).fetchone()
        if not publication_proof:
            raise ValueError("Partner scope changed; validate and publish a new mapping version")
        saved=conn.execute("SELECT * FROM central_mapping_file_links WHERE file_id=?",(file_id,)).fetchone()
        if saved and (saved["revision_id"]!=revision_id or saved["rules_digest"]!=profile["rules_digest"] or saved["scope_digest"]!=scope):
            raise ValueError("Saved file mapping or customer scope changed; reconcile before replaying")
        conn.execute("INSERT OR IGNORE INTO central_mapping_file_links(file_id,revision_id,rules_digest,scope_digest,created_at) VALUES(?,?,?,?,?)",
                     (file_id,revision_id,profile["rules_digest"],scope,now_utc()))
        result=run_preview(conn,profile["id"],profile["revision"],data,"order-service")
    if result["errors"]:
        first=result["errors"][0]
        raise ValueError(f"Mapping validation failed ({len(result['errors'])} issue(s)): {first['target']}: {first['message']}")
    orders=mapped_orders(result["rows"],partner,environment)
    cfg={"comp_num":partner["company_number"],"plant_code":partner["plant_code"],
         "cust_code":partner["radius_customer_code"],"item_identifier":"CI","address_map":{},
         "uom_map":{line.uom:line.uom for order in orders for line in order.lines},
         "price_unit_map":{line.price_uom:line.price_uom for order in orders for line in order.lines},
         "shipping_request_mode":profile["shipping_request_mode"]}
    return orders,cfg,f"Mapping {profile['name']} version {profile['revision']} ({profile['rules_digest'][:12]})"


def existing_order_from_other_route(db_path,order,cfg,environment):
    """Hold a known SO from another route instead of posting that PO again."""
    with session(db_path) as conn:
        rows=conn.execute("""SELECT r.business_key,r.so_number,r.request_json FROM radius_records r
            JOIN files f ON f.id=r.file_id WHERE r.entity='XLinkAPISOrder' AND f.environment=?
            AND instr(r.business_key,?)>0""",(environment,f"|{order.po_number}|"))
        for row in rows:
            if row["business_key"]==order.business_key or row["business_key"].startswith(order.business_key+"|resubmit:"):
                continue
            try:
                header=json.loads(row["request_json"])["XLSOrders"]["XLSOrder"][0]
            except (ValueError,KeyError,IndexError,TypeError):
                continue
            if (str(header.get("CompNum"))==str(cfg["comp_num"]) and header.get("PlantCode")==cfg["plant_code"]
                    and header.get("CustCode")==cfg["cust_code"] and header.get("CustRef")==order.po_number):
                return row["so_number"] or "unknown"
    return None
