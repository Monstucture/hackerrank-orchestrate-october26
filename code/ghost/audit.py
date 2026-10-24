"""Per-case audit: identity -> relevant resources -> state at audit_at -> steps."""
import csv
from collections import defaultdict
from datetime import datetime, timedelta

from .ids import ident_tokens, norm_phone, norm_ts, prefix, secs, system_for

PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]
RECENT_DAYS = 30  # evidence older than this before the request is about an earlier request
OP_ORDER = [
    "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK",
    "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
    "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
    "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED",
]
STATE = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT",
    "REPORT_ACTIVE_DATA_FLOW": "ACTIVE", "REPORT_RESTORE_RISK": "ACTIVE",
    "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED", "DELETE_RECORD": "ABSENT",
    "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT",
    "VERIFY_RECORD_REDACTED": "REDACTED", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}
CATEGORY_BY_PREFIX = {
    "account": "USER_PROFILE", "search": "SEARCH_INDEX_ENTRY", "analytics": "ANALYTICS_PROFILE",
    "support": "SUPPORT_MESSAGE", "export": "USER_EXPORT", "object": "SUPPORT_ATTACHMENT",
    "marketing": "MARKETING_PROFILE", "cache": "CACHE_ENTRY", "email_delivery": "EMAIL_DELIVERY_LOG",
    "audit_record": "SECURITY_AUDIT_LOG", "legal_hold": "LEGAL_HOLD_RECORD",
    "backup": "BACKUP_ARCHIVE", "data_flow": "DATA_FLOW", "tombstone": "ACCOUNT_TOMBSTONE",
    "telemetry": "PRODUCT_TELEMETRY",
}


def fits_batch(activity, offset, tier):
    """Does a worker event `offset` seconds after reported_at fit that workflow?

    Tier 1 is the usual workflow shape: the primary profile is deleted 45-75
    minutes before the report, retained and derived records are treated in the
    half hour before it, and follow-up deletions and checks run 40-125 minutes
    after it. Tier 2 is a short single-pass workflow within minutes of the
    report; it is used only for events no tier-1 workflow explains.
    """
    is_account = prefix(activity["resource_id"]) == "account" and activity["event_type"] == "DELETE_RECORD"
    if tier == 1:
        if is_account:
            return -4700 <= offset <= -2500
        return -1900 <= offset <= -300 or 2200 <= offset <= 7500
    return -700 <= offset <= 700 if is_account else -300 < offset < 2200


WORKER_EVENTS = {"DELETE_RECORD", "ANONYMIZE_RECORD", "REDACT_RECORD", "DISABLE_DATA_FLOW",
                 "BLOCK_RESTORE", "CHECK_DATA_FLOW", "CHECK_RESTORE_ACCESS"}


class World:
    """All participant data, indexed once."""

    def __init__(self, dataset, obs, scopes, activities):
        self.rules = {r["data_category"]: r["required_action"]
                      for r in csv.DictReader(open(dataset / "deletion_rules.csv", encoding="utf-8"))}
        self.users = {r["user_id"]: r for r in csv.DictReader(open(dataset / "user_identities.csv", encoding="utf-8"))}
        self.history = defaultdict(list)
        for r in csv.DictReader(open(dataset / "user_audit_log.csv", encoding="utf-8")):
            self.history[r["user_id"]].append(r)
        self.obs_by_rid = defaultdict(list)
        for o in obs:
            self.obs_by_rid[o["rid"]].append(o)
        for a in activities:  # deletion-worker checks are verifications, not claims
            if a["event_type"] in ("CHECK_DATA_FLOW", "CHECK_RESTORE_ACCESS"):
                d = a["details"].lower()
                active = ("remains enabled" in d or "can still be restored" in d)
                self.obs_by_rid[a["resource_id"]].append({
                    "file_id": a["activity_id"], "ts": norm_ts(a["event_time"]), "avail": norm_ts(a["event_time"]),
                    "rid": a["resource_id"], "system": a["target_system"], "present": True,
                    "pii": set(), "refs": set(), "attrs": {"active": active}, "kind": "activity"})
        self.scopes_by_system = defaultdict(list)
        for s in scopes:
            self.scopes_by_system[s["system"]].append(s)
        self.links = defaultdict(list)  # rid -> [(ts, tokens)]
        for a in activities:
            if a["event_type"] == "LINK_RESOURCE_IDENTITY":
                self.links[a["resource_id"]].append((norm_ts(a["event_time"]), ident_tokens(a["details"])))
        self.token_owner = defaultdict(set)
        for uid in self.users:
            for t in self.identity_tokens(uid, "9999"):
                self.token_owner[t].add(uid)
        self.worker = [a for a in activities if a["source_system"] == "deletion_worker"
                       and a["event_type"] in WORKER_EVENTS]
        self.worker_by_rid = defaultdict(list)
        for a in self.worker:
            self.worker_by_rid[a["resource_id"]].append(a)
        self.file_refs = defaultdict(set)
        for o in obs:
            self.file_refs[o["file_id"]] |= o["refs"]

    # ------------------------------------------------------------ identity

    def resolve_user(self, email):
        e = email.strip().lower()
        for uid, u in self.users.items():
            if u["email"].lower() == e:
                return uid
        for uid, hist in self.history.items():
            for h in hist:
                if h["field_name"] == "email" and e in (h["old_value"].lower(), h["new_value"].lower()):
                    return uid
        return None

    def identity_tokens(self, uid, cutoff):
        u = self.users[uid]
        toks = {uid, u["email"].lower(), u["email_hash"].lower()}
        p = norm_phone(u["phone"])
        if p:
            toks.add(p)
        for h in self.history.get(uid, []):
            if h["event_time"] > cutoff:
                continue
            for v in (h["old_value"], h["new_value"]):
                toks |= ident_tokens(v) | ({v.lower()} if h["field_name"] == "email_hash" else set())
        return toks

    # ------------------------------------------------------------ evidence helpers

    def observations(self, rid, cutoff, keys):
        """Direct, activity and scope-absence observations usable at cutoff.

        A snapshot that lacks the resource proves absence only when the
        snapshot is about this person (it holds another of their records) or
        it confirms a deletion the worker already attempted on the resource.
        """
        out = [o for o in self.obs_by_rid.get(rid, []) if o["avail"] <= cutoff]
        worked = [norm_ts(a["event_time"]) for a in self.worker_by_rid.get(rid, [])]
        for s in self.scopes_by_system.get(system_for(rid), []):
            if s["avail"] > cutoff or rid in s["rids"]:
                continue
            about_person = keys & self.file_refs[s["file_id"]]
            # a deletion attempt the snapshot confirms, unless the record was seen alive after it
            confirmed = any(t <= s["ts"] and not any(
                o["present"] and o["kind"] == "direct" and t < o["ts"] for o in out) for t in worked)
            if about_person or confirmed:
                out.append({"file_id": s["file_id"], "ts": s["ts"], "avail": s["avail"], "rid": rid,
                            "system": system_for(rid), "present": False, "pii": set(), "refs": set(),
                            "attrs": {}, "kind": "scope"})
        return out

    def owners(self, rid, cutoff):
        users = set()
        for o in self.obs_by_rid.get(rid, []):
            if o["avail"] <= cutoff:
                for t in o["refs"]:
                    users |= self.token_owner.get(t, set())
        for ts, toks in self.links.get(rid, []):
            if ts <= cutoff:
                for t in toks:
                    users |= self.token_owner.get(t, set())
        return users


class Auditor:
    def __init__(self, world, cases):
        self.w = world
        self.cases = cases  # all deletion-authorized cases, for batch disambiguation
        self.by_reported = defaultdict(list)
        for c in cases:
            self.by_reported[c["reported_at"]].append(c)
        self.case_by_id = {c["case_id"]: c for c in cases}
        self.first_touch = {}
        self.worker_cases = []  # (activity, ids of cases whose workflow timing fits it)
        for a in world.worker:
            t = norm_ts(a["event_time"])
            self.first_touch[a["resource_id"]] = min(t, self.first_touch.get(a["resource_id"], t))
            offsets = [(c["case_id"], secs(norm_ts(c["reported_at"]), t)) for c in cases]
            fit = ([(cid, off) for cid, off in offsets if fits_batch(a, off, 1)]
                   or [(cid, off) for cid, off in offsets if fits_batch(a, off, 2)])
            # workflows reported at different times both fit: the one reported closest to the event wins
            nearest = min((abs(off) for _, off in fit), default=0)
            cands = {cid for cid, off in fit if abs(off) == nearest}
            if cands:
                self.worker_cases.append((a, cands))

    # ------------------------------------------------------------ relevance

    def batch_resources(self, case, uid, cutoff):
        """Resources the deletion worker touched for this case's batch.

        Returns (named, timed, ambiguous): `named` resources link to this person
        by identifiers, `timed` ones fit only this case's workflow timing, and
        `ambiguous` maps a prefix to unowned resources whose timing fits several
        cases sharing reported_at.
        """
        w = self.w
        named, timed, ambiguous = set(), set(), defaultdict(set)
        for a, cands in self.worker_cases:
            if case["case_id"] not in cands or norm_ts(a["event_time"]) > cutoff:
                continue
            rid = a["resource_id"]
            owners = w.owners(rid, cutoff)
            if owners:
                if uid in owners:
                    named.add(rid)
            elif cands == {case["case_id"]}:
                timed.add(rid)
            elif all(self.case_by_id[c]["reported_at"] == case["reported_at"] for c in cands):
                ambiguous[prefix(rid)].add(rid)
        return named, timed, ambiguous

    def owned(self, case, cutoff):
        """Resources linked to the case's person by identifiers alone."""
        uid = self.w.resolve_user(case["email"])
        keys = self.w.identity_tokens(uid, cutoff)
        rids = {rid for rid, os_ in self.w.obs_by_rid.items()
                if any(o["avail"] <= cutoff and o["refs"] & keys for o in os_)}
        rids |= {rid for rid, links in self.w.links.items()
                 if any(ts <= cutoff and toks & keys for ts, toks in links)}
        return rids, keys, uid

    def relevant(self, case, uid):
        cutoff = norm_ts(case["audit_at"])
        rids, keys, _ = self.owned(case, cutoff)
        named, timed, ambiguous = self.batch_resources(case, uid, cutoff)
        rids |= named
        # a person has one record of each kind: timing alone never adds a second one
        have = {prefix(r) for r in rids}
        rids |= {r for r in timed if prefix(r) not in have}
        # records last seen long before this request belong to an earlier request
        since = norm_ts((datetime.fromisoformat(norm_ts(case["requested_at"]).replace("Z", "+00:00"))
                         - timedelta(days=RECENT_DAYS)).isoformat())
        rids = {r for r in rids if self.last_seen(r, cutoff) >= since}
        have = {prefix(r) for r in rids}
        for pfx, cands in ambiguous.items():
            if pfx not in have:
                rids |= self.resolve_ambiguous(case, pfx, cands, cutoff)
        return rids, keys

    def last_seen(self, rid, cutoff):
        times = [o["ts"] for o in self.w.obs_by_rid.get(rid, []) if o["avail"] <= cutoff]
        times += [norm_ts(a["event_time"]) for a in self.w.worker_by_rid.get(rid, [])
                  if norm_ts(a["event_time"]) <= cutoff]
        times += [ts for ts, _ in self.w.links.get(rid, []) if ts <= cutoff]
        return max(times, default="")

    def resolve_ambiguous(self, case, pfx, cands, cutoff):
        """Several cases share a batch and the resource names nobody.

        Peers that already own a resource of this kind drop out; if the rest
        match the candidates one-to-one they are paired in time order.
        """
        peers = sorted(self.by_reported.get(case["reported_at"], []), key=lambda c: c["case_id"])
        needy = []
        for p in peers:
            owned, _, puid = self.owned(p, cutoff)
            named, timed, _ = self.batch_resources(p, puid, cutoff)
            if not any(prefix(r) == pfx for r in owned | named | timed):
                needy.append(p)
        cands = sorted(cands, key=lambda r: self.first_touch[r])
        if case not in needy or len(needy) != len(cands):
            return set()
        return {cands[needy.index(case)]}

    # ------------------------------------------------------------ state + policy

    def category(self, rid, obs):
        cat = CATEGORY_BY_PREFIX.get(prefix(rid), "UNKNOWN")
        for o in obs:
            rt = o["attrs"].get("record_type")
            if rt:
                cat = rt
        if cat == "SUPPORT_MESSAGE" and any(
                o["attrs"].get("attachment") or o["attrs"].get("collection") == "ticket_attachments"
                or o["attrs"].get("file_name") or o["attrs"].get("document") for o in obs):
            cat = "SUPPORT_ATTACHMENT"
        if cat == "ANALYTICS_PROFILE" and any("email_opt_in" in o["attrs"] for o in obs):
            cat = "MARKETING_PROFILE"
        return cat

    def action_for(self, rid, cat):
        """Policy action; the worker's chosen operation settles support records.

        A support message is redacted only when the conversation must be kept;
        the deletion workflow's planned operation (not its reported result)
        records which treatment the record was classified for.
        """
        action = self.w.rules.get(cat, "DELETE")
        if cat in ("SUPPORT_MESSAGE", "SUPPORT_ATTACHMENT"):
            planned = {a["event_type"] for a in self.w.worker_by_rid.get(rid, [])}
            if "DELETE_RECORD" in planned:
                return "DELETE"
            if "REDACT_RECORD" in planned:
                return "REDACT"
        return action

    @staticmethod
    def latest(obs):
        rank = {"direct": 2, "activity": 1, "scope": 0}
        groups = defaultdict(list)
        for o in obs:
            groups[(o["ts"], o["file_id"])].append(o)
        key = max(groups, key=lambda k: (k[0], max(rank[o["kind"]] for o in groups[k])))
        g = groups[key]
        present = any(o["present"] for o in g)
        # cite the newest direct look at the record that shows the same state;
        # a snapshot's silence is the fallback proof
        cite = key[1]
        if all(o["kind"] == "scope" for o in g):
            same = [k for k, gg in groups.items() if any(o["kind"] != "scope" for o in gg)
                    and any(o["present"] for o in gg) == present and k[0] >= max(
                        (kk[0] for kk, g2 in groups.items() if any(o["present"] for o in g2) != present),
                        default="")]
            if same:
                cite = max(same)[1]
        merged = {"file_id": cite, "ts": key[0], "present": present,
                  "pii": set().union(*(o["pii"] for o in g)), "attrs": {}}
        for o in g:
            for k, v in o["attrs"].items():
                if k in ("identifiable", "active"):
                    merged["attrs"][k] = merged["attrs"].get(k, False) or v
                else:
                    merged["attrs"].setdefault(k, v)
        return merged

    def plan_resource(self, rid, obs, keys):
        """Return (finding, remediation, verification) ops for one resource."""
        cat = self.category(rid, obs)
        s = self.latest(obs)
        a = s["attrs"]
        exposed = bool(s["pii"]) or bool(a.get("identifiable"))
        if a.get("treated"):
            exposed = False
        if cat == "DATA_FLOW":
            return (("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW") if a.get("active") else (None, None)) + \
                ("VERIFY_DATA_FLOW_DISABLED",), s
        if cat == "BACKUP_ARCHIVE":
            return (("REPORT_RESTORE_RISK", "BLOCK_RESTORE") if a.get("active") else (None, None)) + \
                ("VERIFY_RESTORE_BLOCKED",), s
        action = self.action_for(rid, cat)
        if action == "RETAIN":
            return (None, None, "VERIFY_RECORD_PRESENT") if s["present"] else (None, None, None), s
        if action == "DELETE":
            return (("REPORT_REMAINING_RECORD", "DELETE_RECORD") if s["present"] else (None, None)) + \
                ("VERIFY_RECORD_ABSENT",), s
        if not s["present"]:
            return (None, None, None), s
        if action == "ANONYMIZE":
            attributable = exposed or any(a.get(k) for k in ("customer_reference", "user_id"))
            return (("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD") if attributable else (None, None)) + \
                ("VERIFY_RECORD_ANONYMIZED",), s
        # REDACT
        return (("REPORT_EXPOSED_PII", "REDACT_RECORD") if exposed else (None, None)) + \
            ("VERIFY_RECORD_REDACTED",), s

    # ------------------------------------------------------------ case

    def audit(self, case):
        uid = self.w.resolve_user(case["email"])
        if uid is None:
            return "INCOMPLETE", []
        cutoff = norm_ts(case["audit_at"])
        rids, keys = self.relevant(case, uid)
        rows = []
        for rid in sorted(rids):
            obs = self.w.observations(rid, cutoff, keys)
            if not obs:
                continue
            (find, fix, verify), s = self.plan_resource(rid, obs, keys)
            sysname = system_for(rid)
            if find:
                rows.append(("FINDING", find, sysname, rid, s["file_id"]))
                rows.append(("REMEDIATION", fix, sysname, rid, ""))
            if verify:
                rows.append(("VERIFICATION", verify, sysname, rid, s["file_id"]))
        complete = not any(r[0] == "FINDING" for r in rows)
        if not complete:
            rows = [r if r[0] == "FINDING" else (r[0], r[1], r[2], r[3], "") for r in rows]
        rows.sort(key=lambda r: (PHASES.index(r[0]), OP_ORDER.index(r[1]), r[2], r[3]))
        steps = [{"step_no": i + 1, "phase": p, "system_id": sy, "resource_id": rid,
                  "operation": op, "state": STATE[op], "evidence_id": ev}
                 for i, (p, op, sy, rid, ev) in enumerate(rows)]
        return ("COMPLETE" if complete else "INCOMPLETE"), steps
