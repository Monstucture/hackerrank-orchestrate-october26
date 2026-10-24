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
| 1 | Intent gate + schema writer | proposed | Every request gets a valid row; non-deletion → `NOT_APPLICABLE,[]` |
| 2 | Sample evaluation harness | not started | Scores `sample_predictions.csv` vs `sample_output.csv` (status, step, field, sequence) without baking labels |
| 3 | End-to-end audit on samples | not started | Identity + evidence at `audit_at` + policy + step planner; harness score goes up each iteration |
| 4 | Full 518 run + usage/eval reports | not started | 518 sorted rows + `code/evaluation/*.md` |

Slice 3 is one slice because its parts (identity, evidence, policy, planner) can only be scored together: `sample_output.csv` grades whole step lists. Engineer iterates against the slice 2 harness; PM/QA gate only when the slice closes.

## Current slice — 1. Intent gate + schema writer

**What it does:** Read the request file. Decide if `request_text` authorizes deletion. Write exactly one row per case, sorted by `case_id`: non-deletion → `NOT_APPLICABLE,[]`; deletion → `INCOMPLETE,[]` as a placeholder until slice 3 produces real steps.

**Usage:**

1. `python code/main.py --sample` → open `sample_predictions.csv`: 32 rows, all `INCOMPLETE,[]` (every sample case is a deletion request)
2. `python code/main.py` → open `output.csv`: 518 rows, header exact, sorted by `case_id`
3. Confirm non-deletion rows in `output.csv` are `NOT_APPLICABLE` with `[]`

**Acceptance:**

- [ ] No `case_id` hardcoded to a status
- [ ] CSV header exact; `output.csv` has 518 data rows sorted by `case_id`
- [ ] `--sample` writes `sample_predictions.csv` and leaves `output.csv` unchanged
- [ ] Intent function covered by tests using synthetic `request_text` (samples have no `NOT_APPLICABLE` case to test against)
- [ ] README run commands match the two commands above

**Out of scope:** evidence parsing, policy, real step JSON for COMPLETE/INCOMPLETE.

**Demo:** show a non-deletion `request_text` → `NOT_APPLICABLE,[]` and a deletion text → `INCOMPLETE`.

## Handoff loop

```text
PM ⇄ Engineer   suggest + create + one refine
PM → FEATURE.md  name, usage, acceptance
PM → QA          test packet
QA → PM          PASS/FAIL + maintenance
PM → Engineer    fix or next slice
```
