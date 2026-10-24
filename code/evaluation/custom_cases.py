"""Hand-built evaluation cases that the public sample does not cover.

Each case writes a tiny synthetic dataset to a temp folder, runs the real
pipeline on it, and compares the result with the expected answer.

    python code/evaluation/custom_cases.py
"""
import csv
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

from main import run  # noqa: E402

USERS = [
    ["user_900", "Ada Quill", "ada.quill@example.com", "+44 7700 900111", "a" * 64, "2026-01-01T00:00:00Z"],
    ["user_901", "Bo Lind", "bo.lind@example.com", "+46 70 111 22 33", "b" * 64, "2026-01-01T00:00:00Z"],
    ["user_902", "Cy Moss", "cy.moss.new@example.com", "+1 202 555 0199", "c" * 64, "2026-05-01T00:00:00Z"],
    ["user_903", "Di Park", "di.park@example.com", "+82 10 1111 2222", "d" * 64, "2026-01-01T00:00:00Z"],
    ["user_905", "Eve Stone", "eve.stone@example.com", "+61 4 1234 5678", "e" * 64, "2026-01-01T00:00:00Z"],
    ["user_906", "Fin Gray", "fin.gray@example.com", "+49 151 2345 6789", "f" * 64, "2026-01-01T00:00:00Z"],
]
AUDIT_LOG = [["audit_900", "user_902", "2026-05-01T00:00:00Z", "email", "cy.moss@example.com", "cy.moss.new@example.com"]]
REQUESTS = [
    # A: workflow says COMPLETE, but the failover replica still holds the profile
    ["case_901", "ada.quill@example.com", "2026-08-01T10:00:00Z", "COMPLETE", "2026-08-02T12:00:00Z",
     "2026-08-10T12:00:00Z", "Please delete my account."],
    # B: only a later capture shows the search record; at audit_at it was gone
    ["case_902", "bo.lind@example.com", "2026-08-01T10:00:00Z", "COMPLETE", "2026-08-02T12:00:00Z",
     "2026-08-10T12:00:00Z", "Please erase my account and personal data."],
    # C: request uses the historical email; the tombstone still shows the current email
    ["case_903", "cy.moss@example.com", "2026-08-01T10:00:00Z", "PARTIALLY_COMPLETE", "2026-08-02T12:00:00Z",
     "2026-08-10T12:00:00Z", "Je demande la suppression définitive de mon compte."],
    # D: Spanish data-access request, not a deletion
    ["case_904", "di.park@example.com", "2026-08-01T10:00:00Z", "NOT_STARTED", "2026-08-02T12:00:00Z",
     "2026-08-10T12:00:00Z", "Quiero una copia de mis datos personales, por favor."],
    # E: a backup restore check names the person only on the trace's first line (subject_key)
    ["case_905", "eve.stone@example.com", "2026-08-01T10:00:00Z", "COMPLETE", "2026-08-02T12:00:00Z",
     "2026-08-10T12:00:00Z", "Please delete my account. Backups may stay but must not restore my profile."],
    # F: the same kind of trace, but in a log captured after audit_at: it must be ignored
    ["case_906", "fin.gray@example.com", "2026-08-01T10:00:00Z", "COMPLETE", "2026-08-02T12:00:00Z",
     "2026-08-10T12:00:00Z", "Please delete my account."],
]
ACTIVITY = [
    ["activity_901", "2026-08-02T11:00:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9901",
     "SUCCESS", "Deletion worker submitted primary profile account_9901 to account_service."],
    ["activity_902", "2026-08-02T11:01:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9902",
     "SUCCESS", "Deletion worker submitted primary profile account_9902 to account_service."],
]
SQL = """-- Captured at: 2026-08-05T00:00:00Z
BEGIN TRANSACTION;
CREATE TABLE customer_profiles_primary (resource_id TEXT PRIMARY KEY, user_id TEXT, full_name TEXT, email TEXT, phone TEXT, account_status TEXT, updated_at TEXT);
CREATE TABLE customer_profiles_failover_replica (resource_id TEXT PRIMARY KEY, user_id TEXT, full_name TEXT, email TEXT, phone TEXT, account_status TEXT, updated_at TEXT);
INSERT INTO customer_profiles_failover_replica VALUES('account_9901','user_900','Ada Quill','ada.quill@example.com','+44 7700 900111','ACTIVE','2026-08-04T00:00:00Z');
COMMIT;
"""
LOG_BEFORE = """2026-08-06T09:00:00Z level=DEBUG service=account_service trace_id=t-1 event=profile_lookup_started resource_id=account_9902 lookup_key=bo.lind@example.com
2026-08-06T09:00:01Z level=INFO service=account_service trace_id=t-1 event=profile_lookup_completed resource_id=account_9902 rows=0 http_status=404
2026-08-06T09:01:00Z level=DEBUG service=search_service trace_id=t-2 event=index_query_started resource_id=search_9902 lookup_key=bo.lind@example.com
2026-08-06T09:01:01Z level=INFO service=search_service trace_id=t-2 event=index_query_completed resource_id=search_9902 result=no_hit total_hits=0
"""
# subject_key on the trace's first line is the only link from the backup to the person
TRACE_BEFORE = """2026-08-07T09:00:00Z level=DEBUG service=account_service trace_id=t-5 event=profile_lookup_started resource_id=account_9905 lookup_key=eve.stone@example.com
2026-08-07T09:00:01Z level=INFO service=account_service trace_id=t-5 event=profile_lookup_completed resource_id=account_9905 rows=0 http_status=404
2026-08-07T09:01:00Z level=DEBUG service=backup_service trace_id=t-6 event=restore_policy_check_started subject_key="eve.stone@example.com" attempt=1
2026-08-07T09:01:05Z level=ERROR service=backup_service trace_id=t-6 resource_id=backup_9905 event=restore_candidate_evaluated eligible=true deletion_block=false
2026-08-07T09:02:00Z level=DEBUG service=account_service trace_id=t-7 event=profile_lookup_started resource_id=account_9906 lookup_key=fin.gray@example.com
2026-08-07T09:02:01Z level=INFO service=account_service trace_id=t-7 event=profile_lookup_completed resource_id=account_9906 rows=0 http_status=404
"""
TRACE_AFTER = """2026-08-15T09:01:00Z level=DEBUG service=backup_service trace_id=t-8 event=restore_policy_check_started subject_key="fin.gray@example.com" attempt=1
2026-08-15T09:01:05Z level=ERROR service=backup_service trace_id=t-8 resource_id=backup_9906 event=restore_candidate_evaluated eligible=true deletion_block=false
"""
JSON_AFTER = {"exported_at": "2026-08-20T00:00:00Z", "records": [
    {"recorded_at": "2026-08-20T00:00:00Z", "system": "search_service", "resource_id": "search_9902",
     "record_type": "SEARCH_INDEX_ENTRY", "result": "FOUND", "subject_reference": "bo.lind@example.com"}]}
TOMBSTONE = {"exported_at": "2026-08-06T00:00:00Z", "snapshots": [{
    "captured_at": "2026-08-06T00:00:00Z", "data_store": "identity_service", "collection": "deleted_customer_references",
    "records": [{"resource_id": "tombstone_9903", "subject_reference": "user_902", "name": "Cy Moss",
                 "email": "cy.moss.new@example.com", "phone": None, "email_hash": None, "account_status": "DELETED"}]}]}

EXPECTED = {
    "case_901": ("INCOMPLETE", [
        ("FINDING", "account_service", "account_9901", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_901"),
        ("REMEDIATION", "account_service", "account_9901", "DELETE_RECORD", "ABSENT", ""),
        ("VERIFICATION", "account_service", "account_9901", "VERIFY_RECORD_ABSENT", "ABSENT", "")]),
    "case_902": ("COMPLETE", [
        ("VERIFICATION", "account_service", "account_9902", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_902"),
        ("VERIFICATION", "search_service", "search_9902", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_902")]),
    "case_903": ("INCOMPLETE", [
        ("FINDING", "identity_service", "tombstone_9903", "REPORT_EXPOSED_PII", "PRESENT", "evidence_904"),
        ("REMEDIATION", "identity_service", "tombstone_9903", "REDACT_RECORD", "REDACTED", ""),
        ("VERIFICATION", "identity_service", "tombstone_9903", "VERIFY_RECORD_REDACTED", "REDACTED", "")]),
    "case_904": ("NOT_APPLICABLE", []),
    "case_905": ("INCOMPLETE", [
        ("FINDING", "backup_service", "backup_9905", "REPORT_RESTORE_RISK", "ACTIVE", "evidence_905"),
        ("REMEDIATION", "backup_service", "backup_9905", "BLOCK_RESTORE", "BLOCKED", ""),
        ("VERIFICATION", "account_service", "account_9905", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
        ("VERIFICATION", "backup_service", "backup_9905", "VERIFY_RESTORE_BLOCKED", "BLOCKED", "")]),
    "case_906": ("COMPLETE", [
        ("VERIFICATION", "account_service", "account_9906", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_905")]),
}


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def build_dataset(d):
    shutil.copy(ROOT / "dataset" / "deletion_rules.csv", d / "deletion_rules.csv")
    write_csv(d / "user_identities.csv", ["user_id", "name", "email", "phone", "email_hash", "updated_at"], USERS)
    write_csv(d / "user_audit_log.csv", ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"],
              AUDIT_LOG)
    head = ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"]
    write_csv(d / "deletion_requests.csv", head, REQUESTS)
    write_csv(d / "sample_deletion_requests.csv", head, [])
    write_csv(d / "system_activity.csv", ["activity_id", "event_time", "event_type", "source_system", "target_system",
                                          "resource_id", "reported_result", "details"], ACTIVITY)
    ev = d / "evidence"
    for sub in ("databases", "logs", "records"):
        (ev / sub).mkdir(parents=True)
    (ev / "databases" / "failover.sql").write_text(SQL, encoding="utf-8")
    (ev / "logs" / "lookup.log").write_text(LOG_BEFORE, encoding="utf-8")
    (ev / "records" / "later.json").write_text(json.dumps(JSON_AFTER), encoding="utf-8")
    (ev / "records" / "tombstones.json").write_text(json.dumps(TOMBSTONE), encoding="utf-8")
    (ev / "logs" / "restore_before.log").write_text(TRACE_BEFORE, encoding="utf-8")
    (ev / "logs" / "restore_after.log").write_text(TRACE_AFTER, encoding="utf-8")
    write_csv(d / "evidence_files.csv", ["file_id", "file_path", "origin", "captured_at"], [
        ["evidence_901", "evidence/databases/failover.sql", "failover_capture", "2026-08-05T00:00:00Z"],
        ["evidence_902", "evidence/logs/lookup.log", "readback_logs", "2026-08-06T09:02:00Z"],
        ["evidence_903", "evidence/records/later.json", "late_export", "2026-08-20T00:00:00Z"],
        ["evidence_904", "evidence/records/tombstones.json", "records_snapshot", "2026-08-06T00:00:00Z"],
        ["evidence_905", "evidence/logs/restore_before.log", "service_logs", "2026-08-07T10:00:00Z"],
        ["evidence_906", "evidence/logs/restore_after.log", "service_logs", "2026-08-15T10:00:00Z"],
    ])


# Intent edge cases: a side request must not cancel an explicit deletion, and a limited request stays limited.
INTENT_CASES = [
    ("Please delete my account. I do not need a copy of my data.", True),
    ("Unsubscribe me and delete my account permanently.", True),
    ("Delete everything, there is no reason to keep my account.", True),
    ("Por favor borren mi cuenta.", True),
    ("Please remove me from marketing lists only.", False),
    ("Fix my surname without removing any of my records.", False),
    ("Please delete my account, but do not delete my order invoices.", True),
    ("Close my account. Do not remove the security records you must keep.", True),
    ("Erase my profile; do not erase anything required for tax purposes.", True),
    ("I still use it, so please don't delete or change anything.", False),
    ("I need help resetting the login. Do not close the account.", False),
]


def check_intent():
    from ghost.intent import authorizes_deletion
    bad = [(t, want) for t, want in INTENT_CASES if authorizes_deletion(t) != want]
    for t, want in bad:
        print(f"intent FAIL: expected {want} for {t!r}")
    print(f"{len(INTENT_CASES) - len(bad)}/{len(INTENT_CASES)} intent cases passed")
    return not bad


def main():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        build_dataset(d)
        out = d / "out.csv"
        run("deletion_requests.csv", out, dataset=d)
        got = {r["case_id"]: r for r in csv.DictReader(open(out, encoding="utf-8"))}
    passed = 0
    for cid, (status, steps) in EXPECTED.items():
        g = got[cid]
        gsteps = [(s["phase"], s["system_id"], s["resource_id"], s["operation"], s["state"], s["evidence_id"])
                  for s in json.loads(g["steps_json"])]
        ok = g["deletion_status"] == status and gsteps == steps
        passed += ok
        print(f"{cid}: {'PASS' if ok else 'FAIL'}  expected {status}, got {g['deletion_status']}")
        if not ok:
            print("   expected", steps)
            print("   got     ", gsteps)
    print(f"\n{passed}/{len(EXPECTED)} custom cases passed")
    return check_intent() and passed == len(EXPECTED)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
