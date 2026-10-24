"""Turn every evidence file into a flat list of observations.

An observation says: at time `ts`, file `file_id` saw resource `rid` in
system `system`, it was present / absent, these identifiers were visible in
it (`pii`), these keys link it to a person (`refs`), plus raw attributes.

Snapshot-style files also emit "scope" facts: the file captured the whole
collection of a system at a time, so a resource missing from it was absent.
"""
import csv
import json
import re
import sqlite3
from pathlib import Path

from .ids import (USER_RE, RID_RE, norm_ts, ident_tokens, prefix, system_for)

# Fields whose values are direct identifiers stored inside the record.
PII_FIELDS = {
    "email", "phone", "email_hash", "full_name", "name", "display_name",
    "requester_email", "requester_name", "requester_phone", "owner_email",
    "owner_phone", "customer_email", "uploaded_by", "matched_value",
    "message_text", "message_excerpt",
}
# Fields that link a record to a person without being stored PII themselves.
REF_FIELDS = {
    "customer_reference", "subject_reference", "user_id", "subject_selector",
    "subject_ref", "lookup_key", "match_key", "scan_key", "correlation_key", "subject_key", "owner_key",
    "lookup_value", "query", "customer_ref",
}
TS_FIELDS = ("observed_at", "recorded_at", "updated_at", "indexed_at",
             "captured_at", "exported_at")
NULLS = {None, "", "null", "none", "None", "NULL", "[REDACTED]"}


def _val(v):
    return None if v in NULLS else v


def new_obs(file_id, ts, rid, system=None):
    return {"file_id": file_id, "ts": ts, "rid": rid, "system": system or system_for(rid),
            "present": None, "pii": set(), "refs": set(), "attrs": {}, "kind": "direct"}


def fill_from_fields(obs, rec):
    """Copy identifiers and attributes out of a flat dict record."""
    for k, v in rec.items():
        if isinstance(v, (dict, list)):
            continue
        v = _val(v)
        if v is None:
            continue
        k = k.lower()
        if k in PII_FIELDS:
            toks = ident_tokens(str(v))
            if k in ("full_name", "name", "display_name", "requester_name"):
                toks.add("name:" + str(v).strip().lower())
            obs["pii"] |= toks
            obs["refs"] |= toks
        elif k in REF_FIELDS:
            obs["refs"] |= ident_tokens(str(v))
        obs["attrs"][k] = v


def apply_status(obs):
    """Derive presence / treatment flags from common attribute names."""
    a = obs["attrs"]
    res = str(a.get("result", "")).upper()
    if res in ("NOT_FOUND", "NO_HIT", "MISS"):
        obs["present"] = False
    elif res:
        obs["present"] = True
        if res == "IDENTIFIABLE":
            obs["attrs"]["identifiable"] = True
        elif res in ("ANONYMIZED", "REDACTED"):
            obs["attrs"]["treated"] = res
        elif res == "ACTIVE":
            obs["attrs"]["active"] = True
        elif res in ("DISABLED", "BLOCKED"):
            obs["attrs"]["active"] = False
    if str(a.get("status_code", a.get("http_status", ""))) == "404":
        obs["present"] = False
    elif str(a.get("status_code", a.get("http_status", ""))) == "200":
        obs["present"] = True
    if "rows" in a:
        obs["present"] = str(a["rows"]) not in ("0",)
    if "total_hits" in a:
        obs["present"] = str(a["total_hits"]) != "0"
    if "enabled" in a:
        obs["attrs"]["active"] = str(a["enabled"]).lower() in ("true", "1")
    if "restore_allowed" in a:
        obs["attrs"]["active"] = str(a["restore_allowed"]).lower() in ("true", "1")
    if "eligible" in a and "deletion_block" in a:
        obs["attrs"]["active"] = (str(a["eligible"]).lower() == "true"
                                  and str(a["deletion_block"]).lower() != "true")
    if str(a.get("decision", "")).lower() == "deny":
        obs["attrs"]["active"] = False
    if "available" in a or "download_available" in a:
        avail = str(a.get("available", a.get("download_available"))).lower()
        obs["present"] = avail in ("true", "1")
    if "direct_identifiers" in a:
        obs["attrs"]["identifiable"] = str(a["direct_identifiers"]) != "0"
    if str(a.get("marker_found", "")).lower() == "false":
        obs["present"] = True
    if obs["present"] is None and obs["kind"] == "direct":
        obs["present"] = True


# ---------------------------------------------------------------- JSON

def parse_json(file_id, captured, data):
    out, scopes = [], []
    top_ts = norm_ts(data.get("captured_at") or data.get("exported_at")) or captured

    def walk(node, ctx):
        if isinstance(node, dict):
            if "resource_id" in node and isinstance(node["resource_id"], str):
                emit(node, ctx)
                return
            c = dict(ctx)
            if "data_store" in node:
                c["system"] = node["data_store"]
            if "collection" in node:
                c["collection"] = node["collection"]
            if node.get("captured_at"):
                c["ts"] = norm_ts(node["captured_at"])
            if "records" in node and "data_store" in node:
                scopes.append({"file_id": file_id, "ts": c.get("ts", top_ts),
                               "system": node["data_store"], "kind": "scope",
                               "rids": {r.get("resource_id") for r in node["records"]
                                        if isinstance(r, dict)}})
            for k, v in node.items():
                cc = dict(c)
                if k == "service_exports" and isinstance(v, dict):
                    for sysname, coll in v.items():
                        sc = dict(c, system=sysname)
                        rids = set()
                        for cname, recs in (coll or {}).items():
                            sc2 = dict(sc, collection=cname)
                            for r in recs or []:
                                if isinstance(r, dict):
                                    rids.add(r.get("resource_id"))
                                walk(r, sc2)
                        scopes.append({"file_id": file_id, "ts": top_ts, "system": sysname,
                                       "kind": "scope", "rids": rids})
                    continue
                walk(v, cc)
        elif isinstance(node, list):
            for x in node:
                walk(x, ctx)

    def emit(rec, ctx):
        rid = rec["resource_id"]
        ts = None
        for f in TS_FIELDS:
            if rec.get(f):
                ts = norm_ts(rec[f])
                break
        ts = ts or ctx.get("ts") or top_ts
        sysname = rec.get("system") or system_for(rid)
        o = new_obs(file_id, ts, rid, sysname)
        o["attrs"]["collection"] = ctx.get("collection")
        flat = {}
        for k, v in rec.items():
            if isinstance(v, dict):
                if k == "profile":
                    flat.update(v)
                elif k == "document":
                    flat.update(v)
                elif k == "hits":
                    docs = v.get("documents") or []
                    flat["total_hits"] = v.get("total", len(docs))
                    for d in docs:
                        flat.update({kk: vv for kk, vv in d.items() if kk != "resource_id"})
            else:
                flat[k] = v
        if "profile" in rec and rec["profile"] is None and "status_code" not in rec:
            o["present"] = False
        if rec.get("record_type"):
            o["attrs"]["record_type"] = rec["record_type"]
        # record_lookup style rows carry the person in subject_reference/lookup_value
        fill_from_fields(o, flat)
        if "message_text" in flat and _val(flat["message_text"]):
            o["pii"] |= ident_tokens(flat["message_text"])
        apply_status(o)
        out.append(o)

    walk(data, {"ts": top_ts})
    return out, scopes


# ---------------------------------------------------------------- SQL

def parse_sql(file_id, captured, text):
    con = sqlite3.connect(":memory:")
    con.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)  # evidence SQL must not open or create files
    con.executescript(text)
    m = re.search(r"Captured at:\s*(\S+)", text)
    file_ts = norm_ts(m.group(1)) if m else captured
    out, scopes = [], []
    scope_ts = {}
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    if "snapshot_scope" in tables:
        for sysname, ts in con.execute("select origin_system, captured_at from snapshot_scope"):
            scope_ts[sysname] = norm_ts(ts)
    seen_by_system = {}
    for t in tables:
        if t == "snapshot_scope":
            continue
        cur = con.execute(f'select * from "{t}"')
        cols = [d[0] for d in cur.description]
        for row in cur:
            rec = dict(zip(cols, row))
            if "record_json" in rec:
                inner = json.loads(rec["record_json"])
                sysname = rec.get("origin_system")
                coll = rec.get("collection_name")
                inner.setdefault("updated_at", rec.get("updated_at"))
                rec = inner
            else:
                sysname, coll = None, t
            rid = rec.get("resource_id")
            if not rid:
                continue
            sysname = sysname or system_for(rid)
            ts = norm_ts(rec.get("updated_at") or rec.get("indexed_at")) or file_ts
            o = new_obs(file_id, max(ts, file_ts) if ts else file_ts, rid, sysname)
            o["attrs"]["collection"] = coll
            o["attrs"]["table"] = t
            flat = {}
            for k, v in rec.items():
                if isinstance(v, dict) and k in ("document", "profile"):
                    flat.update(v)
                elif k != "resource_id":
                    flat[k] = v
            fill_from_fields(o, flat)
            if _val(rec.get("message_text")):
                o["pii"] |= ident_tokens(rec["message_text"])
            apply_status(o)
            o["present"] = True
            out.append(o)
            seen_by_system.setdefault(sysname, set()).add(rid)
    for sysname, rids in seen_by_system.items():
        scopes.append({"file_id": file_id, "ts": scope_ts.get(sysname, file_ts),
                       "system": sysname, "kind": "scope", "rids": rids})
    for sysname, ts in scope_ts.items():
        if sysname not in seen_by_system:
            scopes.append({"file_id": file_id, "ts": ts, "system": sysname,
                           "kind": "scope", "rids": set()})
    con.close()
    return out, scopes


# ---------------------------------------------------------------- logs

KV_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=("(?:[^"\\]|\\.)*"|\S+)')
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s*(.*)$")


def parse_log_line(line):
    m = TS_RE.match(line)
    if not m:
        return None
    ts, rest = m.groups()
    kv, service, trace, event = {}, None, None, None
    if rest.startswith("{"):
        try:
            kv = {k: str(v) for k, v in json.loads(rest).items()}
        except ValueError:
            kv = {}
    else:
        b = re.match(r"\[(\w+)\]\s+\[([\w-]+)\]\s+\[([\w-]+)\]\s+(\w+):\s*(.*)", rest)
        p = re.match(r"\|\s*(\w+)\s*\|\s*([\w-]+)\s*\|\s*([\w-]+)\s*\|\s*(\w+)\s*\|\s*(.*)", rest)
        s = re.match(r"<\d+>\d+\s+\S+\s+([\w-]+)\s+([\w-]+)\s+-\s+-\s+(.*)", rest)
        if b:
            _, service, trace, event, rest = b.groups()
        elif p:
            _, service, trace, event, rest = p.groups()
        elif s:
            service, trace, rest = s.groups()
        for k, v in KV_RE.findall(rest):
            if v.startswith('"'):
                v = v[1:-1].replace('\\"', '"')
            kv[k] = v
        if "message" in kv and "=" in kv["message"]:
            for k, v in KV_RE.findall(kv.pop("message")):
                kv[k] = v[1:-1] if v.startswith('"') else v
    service = kv.get("service") or kv.get("function") or kv.get("logger") or service
    trace = kv.get("trace_id") or kv.get("requestId") or trace
    event = kv.get("event") or event
    return {"ts": ts, "service": service, "trace": trace, "event": event, "kv": kv}


def parse_log(file_id, captured, text):
    lines = [x for x in (parse_log_line(l) for l in text.splitlines()) if x]
    keys_by_trace = {}
    for ln in lines:
        if ln["trace"]:
            for k in ("lookup_key", "match_key", "scan_key", "subject_ref", "correlation_key",
                      "subject_key", "owner_key"):
                if ln["kv"].get(k):
                    keys_by_trace.setdefault(ln["trace"], set()).add(ln["kv"][k])
    out = []
    for ln in lines:
        kv = ln["kv"]
        rid = kv.get("resource_id")
        if not rid or not RID_RE.fullmatch(rid):
            continue
        o = new_obs(file_id, ln["ts"], rid, kv.get("system") or system_for(rid))
        o["attrs"]["event"] = ln["event"]
        o["attrs"]["service"] = ln["service"]
        flat = {k: v for k, v in kv.items() if k not in ("resource_id", "service", "event")}
        fill_from_fields(o, flat)
        for key in keys_by_trace.get(ln["trace"], ()):
            o["refs"] |= ident_tokens(key)
        ev = ln["event"] or ""
        if ev.endswith("_started") or ev in ("connection_acquired", "cache_lookup"):
            continue
        if ev in ("message_read",) and _val(kv.get("message_text")):
            o["pii"] |= ident_tokens(kv["message_text"])
        if "attachment" in ev or kv.get("file", "").endswith(".pdf"):
            o["attrs"]["attachment"] = True
        if "result" in kv and kv["result"] == "hit":
            o["present"] = True
            kv2 = dict(o["attrs"]); kv2.pop("result", None); o["attrs"] = kv2
        apply_status(o)
        if ev.endswith("pii_scan") or ev == "analytics_identity_scan":
            o["attrs"]["identifiable"] = True
            o["present"] = True
        out.append(o)
    return out


# ---------------------------------------------------------------- text records

def parse_text(file_id, captured, text):
    out = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.lstrip("- ").split("|")]
        if len(parts) == 6 and RID_RE.fullmatch(parts[2]):
            ts, system, rid, rtype, status, value = parts
            o = new_obs(file_id, norm_ts(ts) or captured, rid, system)
            o["attrs"].update(record_type=rtype, result=status)
            o["refs"] |= ident_tokens(value)
            if status == "IDENTIFIABLE":
                o["pii"] |= ident_tokens(value)
            apply_status(o)
            out.append(o)
    return out


# ---------------------------------------------------------------- documents and images

NL = chr(10)


def parse_free_text(file_id, captured, text, aliases):
    """PDF / OCR text: find resources and the identifiers shown with them.

    A document about one person links every resource in it to that person.
    A screen listing several people (a table) links a resource only to the
    identifiers printed within a few lines of it.
    """
    lines = [l for l in text.replace("·", " ").replace("_oo", "_00").splitlines() if l.strip()]
    toks = ident_tokens(NL.join(lines))
    emails = {t for t in toks if "@" in t}
    users = {t for t in toks if t.startswith("user_")}
    single = len(emails) <= 1 and len(users) <= 1
    mentions = {}
    for i, line in enumerate(lines):
        for rid in RID_RE.findall(line):
            mentions.setdefault(rid, []).append(i)
        for alias, rid in aliases.items():
            # a bare user id names a person, so it identifies a record only on a one-person screen
            if alias and (single or not USER_RE.fullmatch(alias)) and re.search(r"(?<![\w-])" + re.escape(alias) + r"(?!\w)", line):
                mentions.setdefault(rid, []).append(i)
    line_toks = [ident_tokens(l) for l in lines]
    out = []
    for rid, idx in sorted(mentions.items()):
        if single:
            near = set(toks)
        else:
            # table screens: take the closest printed person on each mention's row
            near = set()
            for i in idx:
                for j in (i, i + 1, i - 1, i + 2, i - 2, i + 3, i - 3):
                    if not 0 <= j < len(lines):
                        continue
                    found = {t for t in line_toks[j] if "@" in t or t.startswith("user_")}
                    if found:
                        near |= found
                        break
        o = new_obs(file_id, captured, rid)
        o["pii"] = {t for t in near if not t.startswith("user_")}
        o["refs"] = set(near)
        o["attrs"]["document"] = True
        o["present"] = True
        if prefix(rid) in ("data_flow", "backup"):
            # a connector or archive shown live on an operations screen is running / restorable
            o["attrs"]["active"] = True
        out.append(o)
    text = NL.join(lines)
    # rows like "ORD-20264118 | user_08 | ..." in registers
    for m in re.finditer(r"(ORD-\d+)\s*\|\s*(user_\d+)\s*\|([^\n]*)", text):
        order, uid, rest = m.groups()
        rid = aliases.get(order)
        if rid:
            o = new_obs(file_id, captured, rid)
            o["refs"] = {uid}
            o["present"] = True
            o["attrs"]["retain_note"] = rest.strip()
            out.append(o)
    return out


def ocr_image(path, cache):
    key = f"{path.name}:{path.stat().st_size}"  # a regenerated screenshot gets fresh OCR
    if key in cache:
        return cache[key]
    from rapidocr_onnxruntime import RapidOCR  # optional heavy dependency
    if "_engine" not in cache:
        cache["_engine"] = RapidOCR()
    res, _ = cache["_engine"](str(path))
    cache[key] = "\n".join(r[1] for r in (res or []))
    return cache[key]


def pdf_text(path):
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)


# ---------------------------------------------------------------- driver

def load_evidence(dataset: Path, activities, ocr_cache_path: Path):
    manifest = list(csv.DictReader(open(dataset / "evidence_files.csv", encoding="utf-8")))
    obs, scopes = [], []
    link_aliases, record_aliases = {}, {}
    for a in activities:  # "linked object_19 (delivery_confirmation_19.pdf) to ..."
        m = re.search(r"linked (\S+) \(([^)]+)\)|confirmed (\S+) \(([^)]+)\)", a["details"])
        if m:
            rid = m.group(1) or m.group(3)
            alias = m.group(2) or m.group(4)
            if alias != rid:
                link_aliases[alias] = rid
    deferred = []
    for f in manifest:
        path = dataset / f["file_path"]
        fid, cap = f["file_id"], norm_ts(f["captured_at"])
        suffix = path.suffix.lower()
        if suffix in (".pdf", ".png", ".jpg", ".jpeg"):
            deferred.append((fid, cap, path, suffix))
            continue
        text = path.read_text(encoding="utf-8")
        if suffix == ".json":
            o, s = parse_json(fid, cap, json.loads(text))
        elif suffix == ".sql":
            o, s = parse_sql(fid, cap, text)
        elif suffix == ".log":
            o, s = parse_log(fid, cap, text), []
        else:
            o, s = parse_text(fid, cap, text), []
        for x in o:
            for k in ("order_reference", "ticket_id", "file_name"):
                if x["attrs"].get(k):
                    record_aliases.setdefault(str(x["attrs"][k]), set()).add(x["rid"])
        obs += o
        scopes += s
    # file names repeat across systems, so only unambiguous record aliases count
    aliases = {k: next(iter(v)) for k, v in record_aliases.items() if len(v) == 1}
    aliases.update(link_aliases)
    try:
        cache = json.loads(ocr_cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    for fid, cap, path, suffix in deferred:
        text = pdf_text(path) if suffix == ".pdf" else ocr_image(path, cache)
        obs += parse_free_text(fid, cap, text, aliases)
    cache.pop("_engine", None)
    ocr_cache_path.parent.mkdir(parents=True, exist_ok=True)
    ocr_cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    # a fact is usable only once its file was captured
    cap_by_file = {f["file_id"]: norm_ts(f["captured_at"]) for f in manifest}
    for o in obs:
        o["avail"] = cap_by_file[o["file_id"]]
        o["ts"] = min(o["ts"] or o["avail"], o["avail"])
    for s in scopes:
        s["avail"] = cap_by_file[s["file_id"]]
        s["ts"] = min(s["ts"], s["avail"])
    return obs, scopes
