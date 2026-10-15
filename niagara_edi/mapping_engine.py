"""Declarative, preview-only field mapping. No eval, network, or Radius writes."""

import csv
import hashlib
import io
import json
import re
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal, DecimalException, InvalidOperation, localcontext

from .fixed850 import field, parse_fixed_850
from .outbound856 import parse_outbound_856

FORMATS = {"CSV": "Customer CSV (header row)", "JSON": "Customer JSON (rows)",
           "X12_850": "Customer X12 004010 purchase order",
           "RADIUS_API": "Direct Radius Sales Order + Shipping Request API",
           "RADIUS850": "Radius Standard Inbound 850 TXT", "RADIUS856": "Radius Standard Outbound 856 format 1 TXT"}
OPERATIONS = ("copy", "constant", "lookup", "date", "number", "multiply", "uppercase", "lowercase", "trim")
MAX_ROWS = 1000
# Target field -> required, type, width. Positions are the supplied Radius 850 workbook's common fields.
RADIUS850_FIELDS = {
    "CompNum": (True,"integer",6), "PlantCode": (True,"text",8), "CustCode": (True,"text",8),
    "CustRef": (True,"text",30), "CustOrderDate": (True,"date",8), "CurrCode": (True,"text",4),
    "BillAddressRef": (True,"text",10), "ShipAddressRef": (True,"text",10),
    "CustReqDate": (False,"date",8), "TermsCode": (False,"text",8), "ShipDate": (True,"date",8),
    "CustPOLineRef": (True,"integer",20), "CustItemRef": (True,"text",30),
    "OrderedQty": (True,"positive",12), "OrderUnitCode": (True,"text",10),
    "FCUnitPrice": (True,"nonnegative",13), "PriceUnitCode": (True,"text",10),
    "ReqDate": (True,"date",8), "ShipQty": (True,"positive",12),
}
RADIUS856_FIELDS = ("CompNum","PlantCode","CustCode","BOLNum","ShipReqNum","ShipDate",
                    "ShipReqLineNum","SOrderNum","SOrderLineNum","CustRef","ItemCode","ShipQty")


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()


def validate_contract(direction, message_type, source, target):
    if direction == "INBOUND" and message_type == "850" and source in {"CSV","JSON","X12_850","RADIUS850"} and target in {"RADIUS_API","RADIUS850"}:
        return
    if direction == "OUTBOUND" and message_type == "856" and source in {"RADIUS856","JSON"} and target in {"CSV","JSON"}:
        return
    raise ValueError("This format/direction is not supported by an installed preview adapter")


def validate_rules(rules, target_format):
    if not isinstance(rules,list) or not 1 <= len(rules) <= 200:
        raise ValueError("Provide between 1 and 200 field rules")
    seen = set()
    allowed = {"target","source","operation","value","default","required","input_format","output_format","lookup"}
    for rule in rules:
        if not isinstance(rule,dict) or rule.keys() - allowed:
            raise ValueError("Invalid rule fields")
        target = rule.get("target","")
        if not isinstance(target,str) or not target.strip() or len(target)>100 or any(ord(x)<32 for x in target):
            raise ValueError("Each target field needs a printable name up to 100 characters")
        if target in seen:
            raise ValueError(f"Duplicate target field: {target}")
        seen.add(target)
        if target_format in {"RADIUS_API","RADIUS850"} and target not in RADIUS850_FIELDS:
            raise ValueError(f"Unsupported Radius target field: {target}")
        operation = rule.get("operation","copy")
        if operation not in OPERATIONS:
            raise ValueError("Unsupported mapping operation")
        for key in ("source","value","default","input_format","output_format"):
            if not isinstance(rule.get(key,""),str) or len(rule.get(key,""))>500:
                raise ValueError(f"Rule {key} must be text up to 500 characters")
        if not isinstance(rule.get("required",False),bool):
            raise ValueError("Required flag must be true or false")
        lookup = rule.get("lookup",{})
        if not isinstance(lookup,dict) or len(lookup)>1000 or any(not isinstance(k,str) or not isinstance(v,str) for k,v in lookup.items()):
            raise ValueError("Code lookup must be a JSON object of text-to-text values")
        if operation != "constant" and not rule.get("source","").strip():
            raise ValueError(f"Source field is required for {target}")
        if operation == "lookup" and not lookup:
            raise ValueError(f"Code lookup is empty for {target}")
        if operation == "date" and (not rule.get("input_format") or not rule.get("output_format")):
            raise ValueError("Date rules require input and output formats, such as %m/%d/%Y and %Y-%m-%d")
    if target_format in {"RADIUS_API","RADIUS850"}:
        missing = {name for name,(required,_,_) in RADIUS850_FIELDS.items() if required} - seen
        if missing:
            raise ValueError("Missing required Radius target rules: " + ", ".join(sorted(missing)))


def template_rules(direction, partner):
    if direction == "INBOUND":
        rules = [{"target":name,"source":name,"operation":"copy","required":required}
                 for name,(required,_,_) in RADIUS850_FIELDS.items()]
        for rule in rules:
            value = {"CompNum":partner["company_number"],"PlantCode":partner["plant_code"],"CustCode":partner["radius_customer_code"]}.get(rule["target"])
            if value is not None:
                rule.update(operation="constant",value=str(value),source="")
        return rules
    return [{"target":name,"source":name,"operation":"copy","required":True}
            for name in ("CustCode","BOLNum","ShipReqNum","SOrderNum","CustRef")]


def read_source(data, source_format, partner, direction):
    if len(data)>10*1024*1024:
        raise ValueError("Sample exceeds 10 MB")
    if source_format == "CSV":
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")),strict=True)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames) or any(not x for x in reader.fieldnames):
            raise ValueError("CSV needs unique nonempty column names")
        rows=[]
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("CSV row does not match the header columns")
            rows.append(row)
            if len(rows)>MAX_ROWS:
                raise ValueError("Preview supports at most 1000 source rows")
    elif source_format == "JSON":
        rows=json.loads(data.decode("utf-8-sig"))
        if isinstance(rows,dict):
            rows=rows.get("rows",[rows])
    elif source_format in {"RADIUS850","X12_850"}:
        from .edi import parse_850
        rows=[]
        orders=parse_fixed_850(data,require_complete=False) if source_format=="RADIUS850" else parse_850(data)
        for order in orders:
            for line in order.lines:
                rows.append({"CompNum":order.comp_num,"PlantCode":order.plant_code,"CustCode":order.sender,
                    "CustRef":order.po_number,"CustOrderDate":order.po_date,"CurrCode":order.currency,
                    "BillAddressRef":order.bill_to_id,"ShipAddressRef":order.ship_to_id,
                    "CustReqDate":getattr(order,"cust_req_date",""),"TermsCode":getattr(order,"price_terms",""),
                    "ShipDate":order.shipping_date or line.promised_ship or line.ship_no_later,
                    "CustPOLineRef":line.po_line,"CustItemRef":line.identifiers.get("CI","") or line.identifiers.get("BP",""),
                    "OrderedQty":str(line.quantity),"OrderUnitCode":line.uom,"FCUnitPrice":str(line.price),
                    "PriceUnitCode":line.price_uom,"ReqDate":line.promised_ship or line.ship_no_later,
                    "ShipQty":str(line.ship_quantity if line.ship_quantity is not None else line.quantity)})
                rows[-1].update({f"ItemRef_{key}":value for key,value in line.identifiers.items() if value})
                if source_format=="X12_850":
                    rows[-1].update(ISASender=order.sender,ISAReceiver=order.receiver)
    elif source_format == "RADIUS856":
        docs=parse_outbound_856(data,customer=partner["radius_customer_code"],company=partner["company_number"],plant=partner["plant_code"])
        rows=[]
        doc_iter=iter(docs)
        doc=None
        for record in data.decode("cp1252").splitlines():
            if record.startswith("SNHEAD"):
                doc=next(doc_iter)
                raw_date=field(record,88,8)
                ship_date=datetime.strptime(raw_date,"%d%m%Y").date().isoformat() if raw_date else ""
                line_index=0
            elif record.startswith("SNLINE"):
                line=doc["lines"][line_index]
                line_index+=1
                rows.append({"CompNum":doc["company"],"PlantCode":doc["plant"],"CustCode":doc["customer"],
                    "BOLNum":doc["bol"],"ShipReqNum":doc["shipreq"],"ShipDate":ship_date,
                    "ShipReqLineNum":line["shipreq_line"],"SOrderNum":line["so"],"SOrderLineNum":line["so_line"],
                    "CustRef":line["po"],"ItemCode":field(record,31,25),"ShipQty":field(record,173,14)})
    else:
        raise ValueError("Unsupported source format")
    if not isinstance(rows,list) or not 1<=len(rows)<=MAX_ROWS or any(not isinstance(row,dict) for row in rows):
        raise ValueError("Sample must contain between 1 and 1000 row objects")
    if direction == "OUTBOUND":
        for index,row in enumerate(rows,1):
            if (str(row.get("CustCode","")).strip()!=partner["radius_customer_code"] or
                    str(row.get("CompNum","")).strip()!=str(partner["company_number"]) or
                    str(row.get("PlantCode","")).strip()!=partner["plant_code"]):
                raise ValueError(f"Source row {index} customer/company/plant does not match this mapping's partner")
    return rows


def _source_value(row,path):
    if path in row:
        return row[path]
    value=row
    for part in path.split("."):
        if not isinstance(value,dict) or part not in value:
            return ""
        value=value[part]
    return value


def _transform(row,rule):
    operation=rule.get("operation","copy")
    value=rule.get("value","") if operation=="constant" else _source_value(row,rule.get("source",""))
    if value is None or value=="":
        value=rule.get("default","")
    if value is None or value=="":
        if rule.get("required"):
            raise ValueError("Required source value is missing; provide a mapping or explicit default")
        return ""
    if isinstance(value,(dict,list,bool)):
        raise ValueError("Source value must be scalar text or a number")
    value=str(value)
    if len(value)>5000:
        raise ValueError("Source field exceeds the 5000-character preview limit")
    if operation=="lookup":
        if value not in rule["lookup"]:
            raise ValueError("Source code is not present in this rule's lookup table")
        value=rule["lookup"][value]
    elif operation=="date":
        try:
            value=datetime.strptime(value,rule["input_format"]).strftime(rule["output_format"])
        except ValueError:
            raise ValueError("Source date does not match the configured input format") from None
    elif operation in {"number","multiply"}:
        try:
            number=Decimal(value)
            def bounded(n):
                return n.is_finite() and len(n.as_tuple().digits)<=50 and abs(n.as_tuple().exponent)<=50 and n.adjusted()<=50
            if not bounded(number):
                raise InvalidOperation()
            if operation=="multiply":
                factor=Decimal(rule.get("value",""))
                if not bounded(factor):
                    raise InvalidOperation()
                with localcontext() as context:
                    context.prec=110
                    number*=factor
            if not bounded(number):
                raise InvalidOperation()
            value=format(number,"f")
        except DecimalException:
            raise ValueError("Source is not a finite decimal number") from None
    elif operation=="uppercase":
        value=value.upper()
    elif operation=="lowercase":
        value=value.lower()
    elif operation=="trim":
        value=value.strip()
    if not value.strip() and rule.get("required"):
        raise ValueError("Required mapped value is blank")
    return value


def map_rows(rows,rules):
    output,trace,errors=[],[],[]
    characters=0
    for index,row in enumerate(rows,1):
        mapped={}
        for rule in rules:
            try:
                value=_transform(row,rule)
                characters+=len(value)
                if characters>5*1024*1024:
                    raise ValueError("Mapped sample exceeds the 5 MB preview limit")
                mapped[rule["target"]]=value
                trace.append({"row":index,"source":rule.get("source","") or "Constant",
                              "target":rule["target"],"operation":rule.get("operation","copy"),"value":value})
            except ValueError as exc:
                errors.append({"row":index,"target":rule["target"],"message":str(exc)})
        output.append(mapped)
    return output,trace,errors


def _write_record(tag,size,fields):
    record=list(tag.ljust(size))
    for start,width,value in fields:
        value=str("" if value is None else value)
        if len(value)>width:
            raise ValueError(f"{tag} field at position {start} exceeds its fixed width")
        record[start-1:start-1+width]=value.ljust(width)
    return "".join(record)


def validate_radius_rows(rows,partner):
    groups=OrderedDict()
    header_names=("CompNum","PlantCode","CustCode","CustRef","CustOrderDate","CurrCode","BillAddressRef",
                  "ShipAddressRef","CustReqDate","TermsCode","ShipDate")
    for index,row in enumerate(rows,1):
        for name,(required,kind,width) in RADIUS850_FIELDS.items():
            value=row.get(name,"").strip()
            row[name]=value
            if not value:
                if required:
                    raise ValueError(f"Row {index}: required Radius field {name} is blank")
                continue
            if any(ord(c)<32 for c in value) or not value.isascii():
                raise ValueError(f"Row {index}: {name} must be printable ASCII")
            if name in {"CustRef","CustCode","PlantCode"} and "|" in value:
                raise ValueError(f"Row {index}: {name} cannot contain the order-key separator")
            if kind=="date":
                try:
                    row[name]=datetime.strptime(value,"%Y-%m-%d").date().isoformat()
                except ValueError:
                    raise ValueError(f"Row {index}: {name} must be YYYY-MM-DD") from None
            else:
                if len(value)>width:
                    raise ValueError(f"Row {index}: {name} exceeds Radius width {width}")
                if kind=="integer" and (not value.isdigit() or int(value)<=0):
                    raise ValueError(f"Row {index}: {name} must be a positive integer")
                if kind in {"positive","nonnegative"}:
                    try:
                        if not re.fullmatch(r"\d+(?:\.\d+)?",value):
                            raise InvalidOperation()
                        number=Decimal(value)
                        if not number.is_finite() or number<0 or (kind=="positive" and number==0):
                            raise InvalidOperation()
                    except InvalidOperation:
                        raise ValueError(f"Row {index}: {name} has an invalid quantity or price") from None
                if kind=="integer":
                    row[name]=str(int(value))
        if (row["CustCode"]!=partner["radius_customer_code"] or row["CompNum"]!=str(partner["company_number"]) or row["PlantCode"]!=partner["plant_code"]):
            raise ValueError(f"Row {index}: target customer/company/plant does not match this mapping's partner")
        group=groups.setdefault(row["CustRef"],[])
        if group and any(row.get(key,"")!=group[0].get(key,"") for key in header_names):
            raise ValueError(f"Row {index}: inconsistent header fields for the same PO")
        if any(existing["CustPOLineRef"]==row["CustPOLineRef"] for existing in group):
            raise ValueError(f"Row {index}: duplicate PO line reference")
        if Decimal(row["ShipQty"])>Decimal(row["OrderedQty"]):
            raise ValueError(f"Row {index}: ShipQty exceeds OrderedQty")
        group.append(row)
    return groups


def mapped_orders(rows,partner,environment):
    """Build order objects directly; no intermediate Radius flat file."""
    from .edi import Order, Line
    orders=[]
    for lines in validate_radius_rows(rows,partner).values():
        h=lines[0]
        identity=f"mapped:{environment}:{partner['id']}:{partner['company_number']}:{partner['plant_code']}:{partner['radius_customer_code']}"
        order=Order("","","",identity,"",po_number=h["CustRef"],
                    po_date=h["CustOrderDate"],currency=h["CurrCode"],bill_to_id=h["BillAddressRef"],
                    ship_to_id=h["ShipAddressRef"],source_format="mapped_customer",
                    shipping_date=h["ShipDate"],comp_num=h["CompNum"],plant_code=h["PlantCode"])
        order.cust_req_date=h.get("CustReqDate","")
        order.price_terms=h.get("TermsCode","")
        for number,line in enumerate(lines,1):
            order.lines.append(Line(line["CustPOLineRef"],Decimal(line["OrderedQty"]),line["OrderUnitCode"],
                Decimal(line["FCUnitPrice"]),{"CI":line["CustItemRef"]},shipment_number=h["CustRef"],
                promised_ship=line["ReqDate"],price_uom=line["PriceUnitCode"],radius_line_num=number,
                ship_quantity=Decimal(line["ShipQty"])))
        orders.append(order)
    return orders


def radius850_output(rows,partner):
    groups=validate_radius_rows(rows,partner)
    def d(value):
        return datetime.strptime(value,"%Y-%m-%d").strftime("%d%m%Y") if value else ""
    records=["EDI850Inbound".ljust(267)]
    for lines in groups.values():
        h=lines[0]
        records.append(_write_record("SOHEAD",580,[(7,1,0),(8,6,h["CompNum"]),(14,8,h["PlantCode"]),
            (38,8,d(h["CustOrderDate"])),(46,8,h["CustCode"]),(73,10,h["BillAddressRef"]),(87,10,h["ShipAddressRef"]),
            (157,30,h["CustRef"]),(214,8,h.get("TermsCode","")),(222,8,d(h.get("CustReqDate",""))),(230,4,h["CurrCode"])]))
        for number,line in enumerate(lines,1):
            records.append(_write_record("SOLINE",881,[(7,1,0),(60,8,d(line["ReqDate"])),(68,30,line["CustItemRef"]),
                (98,12,line["OrderedQty"]),(110,10,line["OrderUnitCode"]),(120,13,line["FCUnitPrice"]),
                (133,10,line["PriceUnitCode"]),(850,4,number),(862,20,line["CustPOLineRef"])]))
        records.append(_write_record("SRHEAD",443,[(7,1,0),(8,6,h["CompNum"]),(14,8,h["PlantCode"]),
            (22,8,h["CustCode"]),(45,8,d(h["ShipDate"])),(62,10,h["BillAddressRef"]),(76,10,h["ShipAddressRef"])]))
        for number,line in enumerate(lines,1):
            records.append(_write_record("SRLINE",567,[(7,1,0),(8,8,h["PlantCode"]),(24,4,number),
                (28,30,h["CustRef"]),(83,30,line["CustItemRef"]),(170,12,line["ShipQty"]),
                (455,30,h["CustRef"]),(528,20,line["CustPOLineRef"])]))
    records.append(_write_record("TOTALS",18,[(7,6,len(groups)*2),(13,6,str(len(records)+1).zfill(6))]))
    result="\r\n".join(records)+"\r\n"
    parse_fixed_850(result.encode("ascii"))
    return result


def preview(data,profile,partner,rules):
    validate_contract(profile["direction"],profile["message_type"],profile["source_format"],profile["target_format"])
    validate_rules(rules,profile["target_format"])
    source=read_source(data,profile["source_format"],partner,profile["direction"])
    rows,trace,errors=map_rows(source,rules)
    content=""
    if not errors:
        try:
            if profile["target_format"]=="RADIUS_API":
                validate_radius_rows(rows,partner)
                content=json.dumps(rows,indent=2,ensure_ascii=False)
            elif profile["target_format"]=="RADIUS850":
                content=radius850_output(rows,partner)
            elif profile["target_format"]=="JSON":
                content=json.dumps(rows,indent=2,ensure_ascii=False)
            else:
                output=io.StringIO(newline="")
                writer=csv.DictWriter(output,fieldnames=[rule["target"] for rule in rules])
                writer.writeheader()
                writer.writerows(rows)
                content=output.getvalue()
        except ValueError as exc:
            errors.append({"row":None,"target":"Output validation","message":str(exc)})
    return {"rows":rows,"trace":trace,"errors":errors,"content":content,"row_count":len(rows),
            "source_fields":sorted({key for row in source for key in row})}
