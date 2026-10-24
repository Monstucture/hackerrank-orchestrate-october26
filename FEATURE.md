# Ghost Data — product slices

Living spec. PM updates this after each refine, then hands it to QA.

## Goal

Prove whether a Northstar Shop user's personal data is truly gone at `audit_at`, then emit grader-valid `output.csv`.

## Usage of the product (always)

```bash
python code/main.py            # all 518 requests -> output.csv (repo root, the submission file)
python code/main.py --sample   # 32 sample requests -> sample_predictions.csv (never touches output.csv)
```

Judges / QA compare `sample_predictions.csv` to `dataset/sample_output.csv` and check `output.csv` against the schema.

## Slice order

| # | Name | Status | Success |
|---|------|--------|---------|
| 1 | Intent gate + schema writer | done | Every request gets a valid row; non-deletion → `NOT_APPLICABLE,[]` |
| 2 | Sample evaluation harness | done | Scores `sample_predictions.csv` vs `sample_output.csv` (status, step, field, sequence) without baking labels |
| 3 | End-to-end audit on samples | done (status 0.969, step F1 0.908) | Identity + evidence at `audit_at` + policy + step planner; harness score goes up each iteration |
| 4 | Full 518 run + usage/eval reports | done | 518 sorted rows + `code/evaluation/*.md` |

Slice 3 is one slice because its parts (identity, evidence, policy, planner) can only be scored together: `sample_output.csv` grades whole step lists. Engineer iterates against the slice 2 harness; PM/QA gate only when the slice closes.

## Current state

All four slices are done. How the agent works, how to run it and how it scores: see `README.md` ("Our Solution") and `code/evaluation/evaluation_report.md`. The next cycle starts a new slice here.

## Handoff loop

```text
PM ⇄ Engineer   suggest + create + one refine
PM → FEATURE.md  name, usage, acceptance
PM → QA          test packet
QA → PM          PASS/FAIL + maintenance
PM → Engineer    fix or next slice
```
