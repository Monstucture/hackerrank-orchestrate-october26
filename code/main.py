"""Ghost Data Still Exists - deletion-audit agent.

    python code/main.py            # all requests -> output.csv (repo root)
    python code/main.py --sample   # sample requests -> sample_predictions.csv
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ghost.audit import Auditor, World  # noqa: E402
from ghost.evidence import load_evidence  # noqa: E402
from ghost.intent import authorizes_deletion  # noqa: E402
from ghost.llm import LLM  # noqa: E402


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def build(dataset, llm):
    activities = read_csv(dataset / "system_activity.csv")
    obs, scopes = load_evidence(dataset, activities, ROOT / "code" / "cache" / "ocr_text.json")
    world = World(dataset, obs, scopes, activities)
    everyone = read_csv(dataset / "deletion_requests.csv") + read_csv(dataset / "sample_deletion_requests.csv")
    for c in everyone:
        c["authorized"] = authorizes_deletion(c["request_text"], llm)
    return Auditor(world, [c for c in everyone if c["authorized"]]), {c["case_id"] + c["email"]: c for c in everyone}


def run(requests_file, out_file, dataset=ROOT / "dataset"):
    t0 = time.time()
    llm = LLM.from_env()
    auditor, known = build(dataset, llm)
    rows = []
    for req in read_csv(dataset / requests_file):
        case = known[req["case_id"] + req["email"]]
        if not case["authorized"]:
            rows.append((req["case_id"], "NOT_APPLICABLE", []))
            continue
        status, steps = auditor.audit(case)
        rows.append((req["case_id"], status, steps))
    rows.sort(key=lambda r: r[0])
    with open(out_file, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for cid, status, steps in rows:
            w.writerow([cid, status, json.dumps(steps, separators=(",", ":"), ensure_ascii=False)])
    print(f"wrote {len(rows)} rows to {out_file} in {time.time() - t0:.1f}s")
    return llm, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="run the 32 sample requests")
    ap.add_argument("--out", help="output path")
    args = ap.parse_args()
    if args.sample:
        run("sample_deletion_requests.csv", Path(args.out or ROOT / "sample_predictions.csv"))
    else:
        llm, n = run("deletion_requests.csv", Path(args.out or ROOT / "output.csv"))
        llm.write_usage(ROOT / "code" / "evaluation" / "usage_stats.json", n)


if __name__ == "__main__":
    main()
