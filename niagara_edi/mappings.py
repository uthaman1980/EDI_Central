"""Versioned customer mapping definitions and sample validation evidence."""

import json
import hashlib
import csv

from .central import now_utc
from .mapping_engine import digest, preview, template_rules, validate_contract, validate_rules


def partner_for(conn,profile):
    partner=conn.execute("SELECT * FROM central_partners WHERE id=?",(profile["partner_id"],)).fetchone()
    if not partner:
        raise ValueError("Unknown partner")
    return dict(partner)


def scope_digest(profile,partner):
    return digest({key:profile[key] for key in ("partner_id","environment","direction","message_type","source_format","target_format","shipping_request_mode")} |
                  {key:partner[key] for key in ("radius_customer_code","company_number","plant_code")})


def get_revision(conn,profile_id,revision):
    row=conn.execute("""SELECT p.*,r.id AS revision_id,r.revision,r.state,r.rules_json,r.rules_digest,
        r.edit_version,r.updated_at,r.published_at,r.published_by FROM central_mapping_profiles p
        JOIN central_mapping_revisions r ON r.profile_id=p.id WHERE p.id=? AND r.revision=?""",(profile_id,revision)).fetchone()
    if not row:
        raise ValueError("Mapping revision not found")
    return dict(row)


def event(conn,message,actor):
    conn.execute("INSERT INTO central_events(created_at,level,stage,message) VALUES(?,?,?,?)",
                 (now_utc(),"info","Mapping configuration",f"{actor}: {message}"))


def create_profile(conn,*,partner_id,name,environment,direction,message_type,source_format,target_format,actor,shipping_request_mode="combined"):
    name=name.strip()
    if not name or len(name)>80 or environment not in {"TEST","LIVE"}:
        raise ValueError("Mapping name and TEST/LIVE environment are required")
    validate_contract(direction,message_type,source_format,target_format)
    if shipping_request_mode not in {"combined","per_line"}:
        raise ValueError("Invalid Shipping Request creation mode")
    partner=partner_for(conn,{"partner_id":partner_id})
    cursor=conn.execute("""INSERT INTO central_mapping_profiles
        (partner_id,name,environment,direction,message_type,source_format,target_format,shipping_request_mode,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)""",(partner_id,name,environment,direction,message_type,source_format,target_format,shipping_request_mode,now_utc()))
    profile_id=cursor.lastrowid
    rules=template_rules(direction,partner)
    conn.execute("""INSERT INTO central_mapping_revisions
        (profile_id,revision,rules_json,rules_digest,created_at,updated_at) VALUES(?,1,?,?,?,?)""",
        (profile_id,json.dumps(rules),digest(rules),now_utc(),now_utc()))
    event(conn,f"Created mapping {profile_id}, draft version 1",actor)
    return profile_id


def save_draft(conn,profile_id,revision,rules,edit_version,actor):
    profile=get_revision(conn,profile_id,revision)
    if profile["state"]!="DRAFT":
        raise ValueError("Published versions are immutable; create a new draft version")
    validate_rules(rules,profile["target_format"])
    cursor=conn.execute("""UPDATE central_mapping_revisions SET rules_json=?,rules_digest=?,edit_version=edit_version+1,updated_at=?
        WHERE id=? AND state='DRAFT' AND edit_version=?""",
        (json.dumps(rules),digest(rules),now_utc(),profile["revision_id"],edit_version))
    if not cursor.rowcount:
        raise ValueError("Another editor changed this draft; reload before saving")
    event(conn,f"Saved mapping {profile_id}, draft version {revision}",actor)


def run_preview(conn,profile_id,revision,data,actor):
    profile=get_revision(conn,profile_id,revision)
    partner=partner_for(conn,profile)
    try:
        result=preview(data,profile,partner,json.loads(profile["rules_json"]))
    except (ValueError,UnicodeError,csv.Error) as exc:
        result={"rows":[],"trace":[],"errors":[{"row":None,"target":"Source validation","message":str(exc)}],
                "content":"","row_count":0,"source_fields":[]}
    conn.execute("""INSERT INTO central_mapping_runs
        (revision_id,rules_digest,scope_digest,input_digest,output_digest,row_count,error_count,created_at,actor)
        VALUES(?,?,?,?,?,?,?,?,?)""",(profile["revision_id"],profile["rules_digest"],scope_digest(profile,partner),
        hashlib.sha256(data).hexdigest(),hashlib.sha256(result["content"].encode()).hexdigest() if result["content"] else None,
        result["row_count"],len(result["errors"]),now_utc(),actor))
    return result


def publish_revision(conn,profile_id,revision,actor):
    profile=get_revision(conn,profile_id,revision)
    if profile["state"]!="DRAFT":
        raise ValueError("This version is already published")
    proof=conn.execute("""SELECT 1 FROM central_mapping_runs WHERE revision_id=? AND rules_digest=?
        AND scope_digest=? AND error_count=0 AND output_digest IS NOT NULL LIMIT 1""",
        (profile["revision_id"],profile["rules_digest"],scope_digest(profile,partner_for(conn,profile)))).fetchone()
    if not proof:
        raise ValueError("Run a successful sample preview with the current saved rules and partner scope before publishing")
    cursor=conn.execute("UPDATE central_mapping_revisions SET state='PUBLISHED',published_at=?,published_by=? WHERE id=? AND state='DRAFT' AND rules_digest=? AND edit_version=?",
                 (now_utc(),actor,profile["revision_id"],profile["rules_digest"],profile["edit_version"]))
    if not cursor.rowcount:
        raise ValueError("Draft changed while publishing; reload and validate again")
    event(conn,f"Published mapping definition {profile_id} version {revision}; no worker activation or delivery",actor)


def clone_revision(conn,profile_id,revision,actor):
    profile=get_revision(conn,profile_id,revision)
    latest=conn.execute("SELECT MAX(revision) FROM central_mapping_revisions WHERE profile_id=?",(profile_id,)).fetchone()[0]
    number=latest+1
    conn.execute("""INSERT INTO central_mapping_revisions
        (profile_id,revision,rules_json,rules_digest,created_at,updated_at) VALUES(?,?,?,?,?,?)""",
        (profile_id,number,profile["rules_json"],profile["rules_digest"],now_utc(),now_utc()))
    event(conn,f"Copied mapping {profile_id} version {revision} to draft version {number}",actor)
    return number
