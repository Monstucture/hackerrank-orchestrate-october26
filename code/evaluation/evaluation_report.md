# Evaluation Report — Ghost Data Still Exists

All numbers come from the final code in this archive.

- Sample metrics: `python code/main.py --sample && python code/evaluation/main.py`
- Custom cases: `python code/evaluation/custom_cases.py`
- Output contract: `python code/evaluation/main.py --check`

## 1. Metric definitions

| Metric | Definition |
| --- | --- |
| Case-level status accuracy | Share of cases whose `deletion_status` matches the answer. |
| Step precision / recall / F1 | A predicted step matches a gold step when `phase, system_id, resource_id, operation, state, evidence_id` are all equal (`step_no` ignored). Matching is one-to-one. |
| Field-level accuracy | Gold steps are paired with predicted steps by `(phase, resource_id)`. The score is the share of the 7 fields that agree; an unpaired gold step scores 0 on every field. |
| Sequence accuracy | The whole `steps_json` array is identical to the answer, in order. |

## 2. Results on the 32 public sample cases

| Metric | Value |
| --- | --- |
| Status accuracy | **1.000** (32 / 32) |
| Step precision | **0.961** |
| Step recall | **0.902** |
| Step F1 | **0.930** |
| Field-level accuracy | **0.887** |
| Sequence accuracy | **0.469** (15 / 32 exact) |

Status confusion (gold → predicted): COMPLETE → COMPLETE 10, INCOMPLETE → INCOMPLETE 22.

Per phase:

| Phase | Gold steps | Predicted | Exact matches |
| --- | --- | --- | --- |
| FINDING | 41 | 41 | 41 |
| REMEDIATION | 41 | 41 | 41 |
| VERIFICATION | 111 | 99 | 92 |

Findings and remediation are exact. Most lost points are in verification. The causes are listed in section 5.

Full dataset (518 cases): 10 `NOT_APPLICABLE`, 151 `COMPLETE`, 357 `INCOMPLETE`. `--check` reports no contract violations. It checks the header, row set and order, JSON shape, phase order, order within each phase, legal operation/state pairs, evidence rules, and that every deletion case has steps and every INCOMPLETE case has a finding. Two runs with different `PYTHONHASHSEED` values produce byte-identical files.

## 3. Additional evaluation cases (created by us)

`code/evaluation/custom_cases.py` builds a small synthetic dataset, runs the real pipeline on it and compares each case with a hand-written answer. Result: **6 / 6 pass**.

| Case | Scenario | What it tests | Expected |
| --- | --- | --- | --- |
| case_901 | The workflow reports COMPLETE and the primary table is empty, but the failover replica still holds the profile. | Replication drift: a replica copy counts as a remaining record; workflow claims are ignored. | `INCOMPLETE`: REPORT_REMAINING_RECORD citing the SQL snapshot, then DELETE_RECORD and VERIFY_RECORD_ABSENT |
| case_902 | Before `audit_at`, a lookup log shows the account (404) and the search entry (0 hits) are gone. An export captured after `audit_at` says the search entry was FOUND. | The time cutoff: evidence captured after `audit_at` must not change the answer. | `COMPLETE`: two VERIFY_RECORD_ABSENT steps citing the pre-audit log |
| case_903 | French request sent from the person's old email (only in `user_audit_log.csv`). The account tombstone still shows the name and current email. | Historical identity resolution, multilingual intent, and the tombstone rule (keep `subject_reference`, remove direct identifiers). | `INCOMPLETE`: REPORT_EXPOSED_PII, REDACT_RECORD, VERIFY_RECORD_REDACTED |
| case_904 | Spanish request for a copy of personal data. | The intent gate must not treat data-access requests as deletion. | `NOT_APPLICABLE`, `[]` |
| case_905 | A backup restore check names the person only on the first line of a log trace (`subject_key`); the backup appears only on the last line. The log is captured before `audit_at`. | Joining identifiers across a trace. With the join removed, this case fails. | `INCOMPLETE`: REPORT_RESTORE_RISK citing the log, BLOCK_RESTORE, then VERIFY_RECORD_ABSENT (account) and VERIFY_RESTORE_BLOCKED |
| case_906 | The same kind of trace, but in a log captured after `audit_at`. | The time cutoff also applies to trace links. | `COMPLETE`: only the account check, citing the earlier log |

The same script also checks **11 intent edge cases** (all pass).
- Explicit deletions next to a side request must count as deletion: "Please delete my account. I do not need a copy of my data.", "Unsubscribe me and delete my account permanently.", "…no reason to keep my account", Spanish "borren mi cuenta".
- Deletions that keep some records must also count: "delete my account, but do not delete my order invoices", "Close my account. Do not remove the security records…", "do not erase anything required for tax purposes".
- Limited requests or refusals must not count: "marketing lists only", "without removing any of my records", "please don't delete or change anything", "Do not close the account".

`python code/evaluation/main.py --min-status 1.0 --min-f1 0.92` exits non-zero if the sample score drops below those floors, so a parser or rule change that loses accuracy fails loudly.

## 4. Failures found and improvements made

We scored the sample after each change. Each row below is one iteration.

| # | Failure found | Fix | Status | Step F1 | Field |
| --- | --- | --- | --- | --- | --- |
| 0 | Baseline: generic parsers, identity matching, rule table. | — | 0.781 | 0.775 | 0.645 |
| 1 | Valid deletion texts without the word "delete" ("remove that search result", "the profile is gone") became `NOT_APPLICABLE`. "remaining copy of my profile" hit a "copy of" access pattern. Partial snapshot bundles made retained audit records look deleted. | Broadened the deletion vocabulary and tightened the access patterns. A snapshot's silence now proves absence only if the snapshot is about the person or confirms a deletion the worker attempted. | 0.906 | 0.837 | 0.811 |
| 2 | Absent records cited a later snapshot instead of the direct 404 lookup. Support records that should be deleted were redacted. A legal register mentioning `user_17` attached `tombstone_17` to six people. Multi-row screenshots linked every row to every email. | Prefer the newest direct sighting with the same state. Use the worker's planned operation (DELETE vs REDACT, not its reported result) to classify support records. Link identifiers in multi-person documents by proximity only. | 0.938 | 0.878 | 0.828 |
| 3 | A fixed ±2 h batch window gave resources to neighbouring workflows. | Workflow-shaped timing windows: profile deletion 45–75 min before the report, derived records 5–30 min before, follow-ups 40–125 min after. | 0.938 | 0.867 | 0.802 |
| 4 | Regression from #3: search documents in SQL replicas lost their email. The `document` JSON object was not flattened. A focused snapshot also hid an account that a later direct lookup showed still exists. | Flatten nested `document` / `profile` objects in SQL rows. A sighting after the deletion attempt overrides a snapshot's silence. | 0.969 | 0.903 | 0.861 |
| 5 | On the full dataset, 136 deletion cases had no account step: workflows reported one hour apart competed for the same events. | Tiered windows: short single-pass windows are used only when no normal workflow explains an event. Cases without an account step fell from 136 to 17. | 0.969 | 0.901 | 0.863 |
| 6 | Screenshots in table form linked a row to the email on the row above. | Take the nearest identifier, preferring the line that follows the resource ID. | 0.969 | 0.898 | 0.863 |
| 7 | Records from the same person's earlier request (months before) were reported again, e.g. a February attachment in a September case. | Drop resources whose latest evidence is more than 30 days before `requested_at`. | **0.969** | **0.908** | **0.866** |
| 8 | Code audit: a side phrase ("copy of my data", "unsubscribe", "keep my account") overruled an explicit deletion, and Spanish "borren" was not recognised. Evidence SQL could `ATTACH` and create files. The OCR cache was keyed by file name only. A failed LLM call silently meant "not a deletion". | Three-tier intent gate (refusal / side request / deletion; unsure cases go to the LLM, offline deletion wins). SQLite attach limit set to 0. OCR cache keyed by name and size. LLM failures warn and fall back. Removed a dead SQL fallback and unused fields. Added a regression floor to the harness. | 0.969 | 0.908 | 0.866 (output unchanged) |
| 9 | Review with the QA and PM agents. PM found the real cause of case_17: a log trace names the person with `subject_key` / `owner_key` on its first line, and the parser did not join those keys across the trace (66 such lines in 11 logs). The new refusal rule turned "delete my account, but do not delete my invoices" into NOT_APPLICABLE. The stricter `--check` then showed sample case_04 as COMPLETE with no steps: its analytics event fit its own pre-report window and the late window of three cases reported two hours earlier, so it was dropped. | Join `subject_key` / `owner_key` on `trace_id`. Refusals count only when they cover the account itself. When workflows reported at different times both fit an event, the one reported closest to it wins. A resource linked only by timing is skipped if the person already owns one of that kind through identifiers. `--check` now checks order within each phase, empty step lists and INCOMPLETE without findings. On the full set, 15 rows changed; 2 cases flipped to INCOMPLETE, both through `subject_key` traces in logs captured before `audit_at`. | **1.000** | **0.930** | **0.887** |

## 5. Known remaining errors (sample)

1. **Accounts nobody can tell apart.** Several cases share one `reported_at`, and their deleted accounts appear only as anonymous 404 lookups or deletion events. Nothing before `audit_at` names the owner, so the agent pairs them in time order, which is a guess (cases 26–28, 31, 32). Without a guess the step would be missing anyway.
2. **Retained audit records evidenced only after `audit_at`.** For 7 sample cases, the only file naming the case's security-audit record was captured after `audit_at`, or no file names it at all. The contract forbids using later evidence, so the agent leaves out these `VERIFY_RECORD_PRESENT` steps. This is the largest source of lost verification recall.
3. **A stale account in case_17.** `account_6523` belongs to the same person's March request. It passes the 30-day filter because a worker event for another batch touched it in September. This costs one step and keeps case_17 from an exact match.
4. **Anonymized analytics profiles in shared batches.** These carry no identifier, so with several peers they cannot be assigned (cases 21, 25, 26).
