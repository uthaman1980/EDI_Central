"""Customer mapping editor, preview, version publication and TEST API handoff."""

import csv
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from flask import Response, abort, redirect, render_template, request, url_for

from .central import session
from .customer_config import load_config
from .mapping_engine import FORMATS, OPERATIONS, RADIUS850_FIELDS, RADIUS856_FIELDS
from .mappings import (clone_revision, create_profile, get_revision, partner_for,
                       publish_revision, run_preview, save_draft)


def register_mapping_routes(app,checked,csrf_token):
    def actor():
        return request.authorization.username

    def render_editor(profile_id,revision,*,result=None,error=None,status=200,rules_override=None):
        with session(app.config["DB_PATH"]) as conn:
            try:
                profile=get_revision(conn,profile_id,revision)
            except ValueError:
                abort(404)
            partner=partner_for(conn,profile)
            versions=[dict(row) for row in conn.execute("SELECT revision,state FROM central_mapping_revisions WHERE profile_id=? ORDER BY revision DESC",(profile_id,))]
            runs=[dict(row) for row in conn.execute("SELECT * FROM central_mapping_runs WHERE revision_id=? ORDER BY id DESC LIMIT 10",(profile["revision_id"],))]
        rules=rules_override if rules_override is not None else json.loads(profile["rules_json"])
        for rule in rules:
            if "lookup_text" not in rule:
                rule["lookup_text"]=json.dumps(rule.get("lookup",{}),ensure_ascii=False)
        fields=list(RADIUS850_FIELDS) if profile["direction"]=="INBOUND" else list(RADIUS856_FIELDS)
        return render_template("mapping_editor.html",profile=profile,partner=partner,rules=rules,
            versions=versions,runs=runs,formats=FORMATS,operations=OPERATIONS,fields=fields,
            csrf_token=csrf_token,result=result,error=error,unsaved=rules_override is not None),status

    @app.get("/central/mappings")
    def mapping_list():
        with session(app.config["DB_PATH"]) as conn:
            partners=[dict(row) for row in conn.execute("SELECT * FROM central_partners ORDER BY display_name")]
            profiles=[dict(row) for row in conn.execute("""SELECT m.*,p.display_name,r.revision,r.state
                FROM central_mapping_profiles m JOIN central_partners p ON p.id=m.partner_id
                JOIN central_mapping_revisions r ON r.profile_id=m.id
                WHERE r.revision=(SELECT MAX(revision) FROM central_mapping_revisions WHERE profile_id=m.id)
                ORDER BY p.display_name,m.direction,m.name""")]
        return render_template("mappings.html",partners=partners,profiles=profiles,formats=FORMATS,csrf_token=csrf_token)

    @app.post("/central/mappings")
    def mapping_create():
        checked()
        try:
            direction,message_type=request.form.get("flow","").split(":")
            with session(app.config["DB_PATH"]) as conn:
                profile_id=create_profile(conn,partner_id=int(request.form.get("partner_id","")),
                    name=request.form.get("name",""),environment=request.form.get("environment","TEST"),
                    direction=direction,message_type=message_type,source_format=request.form.get("source_format",""),
                    target_format=request.form.get("target_format",""),actor=actor(),
                    shipping_request_mode=request.form.get("shipping_request_mode","combined"))
        except (ValueError,sqlite3.IntegrityError) as exc:
            abort(400,str(exc))
        return redirect(url_for("mapping_editor",profile_id=profile_id,revision=1))

    @app.get("/central/mappings/<int:profile_id>/versions/<int:revision>")
    def mapping_editor(profile_id,revision):
        return render_editor(profile_id,revision)

    @app.post("/central/mappings/<int:profile_id>/versions/<int:revision>/save")
    def mapping_save(profile_id,revision):
        checked()
        posted=[]
        try:
            columns={key:request.form.getlist(key) for key in ("target","source","operation","value","default","required","input_format","output_format","lookup")}
            if len({len(values) for values in columns.values()})!=1:
                raise ValueError("Incomplete field-rule form; reload the editor")
            for index in range(len(columns["target"])):
                if not columns["target"][index].strip():
                    continue
                rule={key:values[index] for key,values in columns.items()}
                rule["target"]=rule["target"].strip()
                rule["source"]=rule["source"].strip()
                rule["required"]=rule["required"]=="yes"
                rule["lookup_text"]=rule.pop("lookup") or "{}"
                posted.append(rule)
            rules=[]
            for original in posted:
                rule={key:value for key,value in original.items() if key!="lookup_text"}
                rule["lookup"]=json.loads(original["lookup_text"])
                rules.append(rule)
            with session(app.config["DB_PATH"]) as conn:
                save_draft(conn,profile_id,revision,rules,int(request.form.get("edit_version","")),actor())
        except (ValueError,sqlite3.IntegrityError) as exc:
            return render_editor(profile_id,revision,error=str(exc),status=400,rules_override=posted or None)
        return redirect(url_for("mapping_editor",profile_id=profile_id,revision=revision))

    @app.post("/central/mappings/<int:profile_id>/versions/<int:revision>/preview")
    def mapping_preview(profile_id,revision):
        checked()
        upload=request.files.get("file")
        if not upload or not upload.filename:
            abort(400,"Choose a sample file")
        try:
            with session(app.config["DB_PATH"]) as conn:
                profile=get_revision(conn,profile_id,revision)
                if int(request.form.get("edit_version",""))!=profile["edit_version"]:
                    raise ValueError("Saved rules changed; reload before previewing")
                result=run_preview(conn,profile_id,revision,upload.read(),actor())
            if request.form.get("action")=="download" and not result["errors"]:
                fmt=profile["target_format"]
                suffix="txt" if fmt=="RADIUS850" else "csv" if fmt=="CSV" else "json"
                return Response(result["content"],mimetype="text/plain" if suffix=="txt" else "text/csv" if suffix=="csv" else "application/json",
                    headers={"Content-Disposition":f'attachment; filename="mapping-{profile_id}-v{revision}.{suffix}"'})
            return render_editor(profile_id,revision,result=result,status=400 if result["errors"] else 200)
        except (ValueError,UnicodeError,csv.Error) as exc:
            return render_editor(profile_id,revision,error=str(exc),status=400)

    @app.post("/central/mappings/<int:profile_id>/versions/<int:revision>/<action>")
    def mapping_version_action(profile_id,revision,action):
        checked()
        if action not in {"publish","clone"}:
            abort(404)
        try:
            with session(app.config["DB_PATH"]) as conn:
                current=get_revision(conn,profile_id,revision)
                if int(request.form.get("edit_version",""))!=current["edit_version"]:
                    raise ValueError("Saved rules changed; reload before publishing or copying")
                if action=="publish":
                    publish_revision(conn,profile_id,revision,actor())
                    number=revision
                else:
                    number=clone_revision(conn,profile_id,revision,actor())
        except (ValueError,sqlite3.IntegrityError) as exc:
            return render_editor(profile_id,revision,error=str(exc),status=409)
        return redirect(url_for("mapping_editor",profile_id=profile_id,revision=number))

    @app.post("/central/mappings/<int:profile_id>/versions/<int:revision>/api-preview")
    def mapping_api_preview(profile_id,revision):
        checked()
        config=load_config(app.config["CONFIG_PATH"])
        with session(app.config["DB_PATH"]) as conn:
            try:
                profile=get_revision(conn,profile_id,revision)
            except ValueError:
                abort(404)
        if config.get("environment")!="TEST" or profile["environment"]!="TEST" or profile["state"]!="PUBLISHED" or profile["target_format"]!="RADIUS_API":
            abort(409,"Radius API preview requires a published TEST direct-customer mapping")
        upload=request.files.get("file")
        if not upload or not upload.filename:
            abort(400,"Choose the customer source file")
        root=app.config["INBOX_PATH"].parent/"previews"
        root.mkdir(parents=True,exist_ok=True)
        name="".join(char if char.isalnum() or char in ".-_" else "_" for char in Path(upload.filename).name[:100])
        path=root/(uuid4().hex+"-"+name)
        path.write_bytes(upload.read())
        from .service import process_file
        try:
            result=process_file(path,Path(str(path)+".as2.json"),config,app.config["DB_PATH"],submit=False,
                client=app.config.get("RADIUS_CLIENT"),resolver=app.config.get("ADDRESS_RESOLVER"),
                mapping_revision_id=profile["revision_id"])
        except (ValueError,RuntimeError,OSError) as exc:
            return render_editor(profile_id,revision,error=str(exc),status=400)
        return render_template("mapping_api_preview.html",outcome=result,profile=profile,csrf_token=csrf_token)
