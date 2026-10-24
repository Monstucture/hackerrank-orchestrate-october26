"""Identifier normalisation shared by every module."""
import re
from datetime import datetime, timezone

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+\d[\d\s().-]{6,}\d")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")
USER_RE = re.compile(r"\buser_\d+\b")
RID_RE = re.compile(r"\b(?:account|search|analytics|support|export|data_flow|backup|"
                    r"audit_record|tombstone|legal_hold|marketing|cache|email_delivery|"
                    r"object|telemetry)_\d+\b")

SYSTEM_BY_PREFIX = {
    "account": "account_service", "search": "search_service",
    "analytics": "analytics_platform", "support": "support_platform",
    "export": "export_service", "data_flow": "integration_service",
    "backup": "backup_service", "audit_record": "security_service",
    "tombstone": "identity_service", "legal_hold": "legal_service",
    "marketing": "marketing_platform", "cache": "cache_service",
    "email_delivery": "email_delivery_service", "object": "object_storage",
    "telemetry": "telemetry_service",
}


def prefix(rid):
    return re.sub(r"_\d+$", "", rid)


def system_for(rid):
    return SYSTEM_BY_PREFIX.get(prefix(rid), prefix(rid))


def norm_phone(s):
    d = re.sub(r"\D", "", s)
    return "tel:" + d if len(d) >= 8 else None


def ident_tokens(text):
    """All identifier tokens found in a free-text value."""
    text = str(text)
    toks = {e.lower() for e in EMAIL_RE.findall(text)}
    toks |= {h.lower() for h in HASH_RE.findall(text.lower())}
    toks |= set(USER_RE.findall(text))
    for p in PHONE_RE.findall(text):
        n = norm_phone(p)
        if n:
            toks.add(n)
    return toks


def norm_ts(s):
    if not s:
        return None
    s = str(s).strip()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def secs(a, b):
    """Seconds from timestamp a to b."""
    pa = datetime.fromisoformat(a.replace("Z", "+00:00"))
    pb = datetime.fromisoformat(b.replace("Z", "+00:00"))
    return (pb - pa).total_seconds()
