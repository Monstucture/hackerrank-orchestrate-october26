"""Score predictions against labelled answers.

    python code/evaluation/main.py                       # sample_predictions.csv vs dataset/sample_output.csv
    python code/evaluation/main.py PRED GOLD [--verbose] [--min-status X] [--min-f1 Y]
    python code/evaluation/main.py --check [OUTPUT REQUESTS]  # output contract

Metrics
- status accuracy: deletion_status matches
- step precision / recall / F1: a step matches when all 7 fields match (step_no ignored)
- field accuracy: steps paired by (phase, resource_id); share of the 7 fields that agree
- sequence accuracy: the whole steps array is identical
"""
import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIELDS = ["step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def load(path):
    with open(path, encoding="utf-8", newline="") as f:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(f)}


def key(s):
    return tuple(s[f] for f in FIELDS[1:])


def score(pred, gold, verbose=False):
    n = len(gold)
    status_ok = seq_ok = tp = n_pred = n_gold = 0
    field_hits = field_total = 0
    failures = []
    for cid, (gstatus, gsteps) in sorted(gold.items()):
        pstatus, psteps = pred.get(cid, ("MISSING", []))
        status_ok += pstatus == gstatus
        seq_ok += [key(s) for s in psteps] == [key(s) for s in gsteps]
        pk = [key(s) for s in psteps]
        gk = [key(s) for s in gsteps]
        remaining = list(gk)
        for k in pk:
            if k in remaining:
                remaining.remove(k)
                tp += 1
        n_pred += len(pk)
        n_gold += len(gk)
        pmap = {(s["phase"], s["resource_id"]): s for s in psteps}
        for g in gsteps:
            p = pmap.get((g["phase"], g["resource_id"]))
            field_total += len(FIELDS)
            if p:
                field_hits += sum(p[f] == g[f] for f in FIELDS)
        if pstatus != gstatus or pk != gk:
            failures.append((cid, gstatus, pstatus, set(gk) - set(pk), set(pk) - set(gk)))
    prec = tp / n_pred if n_pred else 0.0
    rec = tp / n_gold if n_gold else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    report = {
        "cases": n, "status_accuracy": status_ok / n, "sequence_accuracy": seq_ok / n,
        "step_precision": prec, "step_recall": rec, "step_f1": f1,
        "field_accuracy": field_hits / field_total if field_total else 0.0,
    }
    if verbose:
        for cid, g, p, missing, extra in failures:
            print(f"\n{cid}: gold={g} pred={p}")
            for m in sorted(missing):
                print("   missing", m)
            for e in sorted(extra):
                print("   extra  ", e)
    return report, failures


OPS = {
    "FINDING": {"REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT",
                "REPORT_ACTIVE_DATA_FLOW": "ACTIVE", "REPORT_RESTORE_RISK": "ACTIVE"},
    "REMEDIATION": {"DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED", "DELETE_RECORD": "ABSENT",
                    "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED"},
    "VERIFICATION": {"VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT",
                     "VERIFY_RECORD_REDACTED": "REDACTED", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
                     "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED"},
}


def check(output, requests):
    """Return a list of contract violations in an output file."""
    errs = []
    with open(output, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    if header != ["case_id", "deletion_status", "steps_json"]:
        errs.append(f"bad header {header}")
    want = sorted(r["case_id"] for r in csv.DictReader(open(requests, encoding="utf-8")))
    ids = [r[0] for r in rows]
    if ids != sorted(ids):
        errs.append("rows not sorted by case_id")
    if sorted(ids) != want:
        errs.append(f"case ids differ from requests ({len(ids)} rows, {len(want)} requests)")
    for cid, status, js in rows:
        steps = json.loads(js)
        if status not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
            errs.append(f"{cid}: bad status {status}")
        if status == "NOT_APPLICABLE" and steps:
            errs.append(f"{cid}: NOT_APPLICABLE must have []")
        if status in ("COMPLETE", "INCOMPLETE") and not steps:
            errs.append(f"{cid}: {status} needs steps")
        if status == "INCOMPLETE" and not any(s.get("phase") == "FINDING" for s in steps):
            errs.append(f"{cid}: INCOMPLETE needs a finding")
        phases = [s["phase"] for s in steps]
        if phases != sorted(phases, key=list(OPS).index):
            errs.append(f"{cid}: phases out of order")
        order = [op for ops in OPS.values() for op in ops]
        keys = [(order.index(s["operation"]), s["system_id"], s["resource_id"])
                for s in steps if s.get("operation") in order]
        for ph in OPS:
            ks = [k for k, s in zip(keys, steps) if s["phase"] == ph]
            if ks != sorted(ks):
                errs.append(f"{cid}: {ph} steps not in operation/system/resource order")
        for i, s in enumerate(steps, 1):
            if set(s) != set(FIELDS):
                errs.append(f"{cid}: step {i} fields {sorted(s)}")
                continue
            if s["step_no"] != i:
                errs.append(f"{cid}: step_no {s['step_no']} at position {i}")
            if OPS.get(s["phase"], {}).get(s["operation"]) != s["state"]:
                errs.append(f"{cid}: illegal {s['phase']}/{s['operation']}/{s['state']}")
            future = s["phase"] == "REMEDIATION" or (status == "INCOMPLETE" and s["phase"] == "VERIFICATION")
            if future and s["evidence_id"]:
                errs.append(f"{cid}: step {i} should have empty evidence_id")
            if not future and not s["evidence_id"]:
                errs.append(f"{cid}: step {i} needs evidence_id")
        if status == "COMPLETE" and any(p != "VERIFICATION" for p in phases):
            errs.append(f"{cid}: COMPLETE may only verify")
    return errs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="PRED GOLD, or with --check: OUTPUT REQUESTS")
    ap.add_argument("--check", action="store_true", help="validate an output file against the contract")
    ap.add_argument("--verbose", action="store_true", help="list missing and extra steps per case")
    ap.add_argument("--min-status", type=float, help="fail if status accuracy is below this")
    ap.add_argument("--min-f1", type=float, help="fail if step F1 is below this")
    a = ap.parse_args()
    if a.check:
        out = Path(a.files[0]) if a.files else ROOT / "output.csv"
        req = Path(a.files[1]) if len(a.files) > 1 else ROOT / "dataset" / "deletion_requests.csv"
        errs = check(out, req)
        print("\n".join(errs) if errs else f"{out.name}: contract OK")
        sys.exit(1 if errs else 0)
    pred = Path(a.files[0]) if a.files else ROOT / "sample_predictions.csv"
    gold = Path(a.files[1]) if len(a.files) > 1 else ROOT / "dataset" / "sample_output.csv"
    report, _ = score(load(pred), load(gold), verbose=a.verbose)
    print()
    for k, v in report.items():
        print(f"{k:18} {v:.3f}" if isinstance(v, float) else f"{k:18} {v}")
    low = [f"{name} {report[name]:.3f} < {floor}"
           for name, floor in (("status_accuracy", a.min_status), ("step_f1", a.min_f1))
           if floor is not None and report[name] < floor]
    if low:
        print("REGRESSION: " + "; ".join(low))
        sys.exit(1)


if __name__ == "__main__":
    main()
