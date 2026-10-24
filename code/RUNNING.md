# Running the deletion-audit agent

This guide covers setup, the full run, the sample run, the evaluation tools and packaging. Every command runs from the **repository root**, the folder that holds `README.md`, `dataset/` and `code/`.

## 1. Requirements

| Need | Version | Why |
| --- | --- | --- |
| Python | 3.11 or newer (tested on 3.12.10) | Runs the agent. Only the standard library plus two packages. |
| `pypdf` | 4.0 or newer | Reads text from the PDF evidence. |
| `rapidocr-onnxruntime` | 1.3 or newer | Reads text from screenshots. Needed only when the OCR cache misses (section 5). |
| `dataset/` | as shipped with the challenge | The agent reads only this folder. |

No API key, GPU or network access is needed.

## 2. Setup

Create a virtual environment outside `code/`, so it never ends up in `code.zip`.

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r code/requirements.txt
```

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r code/requirements.txt
```

If you skip the virtual environment, run only the `pip install` line.

## 3. Produce the submission file

```bash
python code/main.py
```

This command:
- audits all 518 requests in `dataset/deletion_requests.csv`;
- writes `output.csv` to the repository root, sorted by `case_id`;
- writes the run's model usage to `code/evaluation/usage_stats.json`.

Expected output, in about 4 seconds:

```text
wrote 518 rows to <repo root>/output.csv in 3.9s
```

The output is deterministic: two runs give byte-identical files.

To write the file somewhere else:

```bash
python code/main.py --out /path/to/my_output.csv
```

## 4. Check and score

| Command | What it does | Passing result |
| --- | --- | --- |
| `python code/evaluation/main.py --check` | Checks `output.csv` against the output contract: header, 518 rows in `case_id` order, legal statuses, JSON shape, phase and step order, legal operation and state pairs, evidence rules. | `output.csv: contract OK`, exit code 0 |
| `python code/main.py --sample` | Runs the 32 public sample requests and writes `sample_predictions.csv` at the root. | `wrote 32 rows ...` |
| `python code/evaluation/main.py` | Scores `sample_predictions.csv` against `dataset/sample_output.csv`. | Prints status accuracy, step precision, recall and F1, field accuracy and sequence accuracy. |
| `python code/evaluation/main.py --min-status 1.0 --min-f1 0.92` | The same score, but exits with code 1 if status accuracy or step F1 drops below the floor. Use it after any rule change. | No `REGRESSION:` line, exit code 0 |
| `python code/evaluation/main.py --verbose` | The same score, plus the missing and extra steps for each case that differs. | Used for debugging. |
| `python code/evaluation/custom_cases.py` | Builds 8 synthetic cases in a temporary folder, runs the real pipeline on them, and runs 11 intent edge cases. | `8/8 custom cases passed` and `11/11 intent cases passed` |

Run `--sample` before scoring: the scorer reads the `sample_predictions.csv` that the sample run writes.

Other files can be passed by position:

```bash
python code/evaluation/main.py PREDICTIONS.csv GOLD.csv
python code/evaluation/main.py --check OUTPUT.csv REQUESTS.csv
```

Current sample scores: status 1.000, step F1 0.955, field 0.921, sequence 19/32. The full numbers are in `code/evaluation/evaluation_report.md`.

## 5. OCR cache

Screenshot text is cached in `code/cache/ocr_text.json`. Each entry is keyed by file name and file size, so a changed screenshot is read again.

- With the cache present, `rapidocr-onnxruntime` is never imported. This is the normal case.
- To redo OCR from scratch, delete `code/cache/ocr_text.json` and run `python code/main.py`. The first run is slower while the screenshots are read; the cache is then rewritten.

## 6. Optional LLM

The intent gate decides each request with keyword rules. It asks a Claude model only when a request mixes a deletion word with another request and the rules cannot decide. On this dataset the rules decide every request, so the model is never called and the run costs nothing.

| Variable | Default | Effect |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | not set | When set, unsure intent cases go to the model. When not set, a deletion word wins. |
| `GHOST_MODEL` | `claude-haiku-5-5` | The model used for those calls. |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` | API endpoint, for a proxy or gateway. |

Export the key in your shell (`export ANTHROPIC_API_KEY=...`, or `$env:ANTHROPIC_API_KEY = "..."` in PowerShell). The agent does not read `.env` files by itself. Never write the key in code or in `log.txt`. If a call fails, the agent prints a warning and falls back to the keyword rules. The call count and tokens of the full run go to `code/evaluation/usage_stats.json` and are summarized in `code/evaluation/usage_report.md`.

## 7. Package the submission

Remove the generated caches first, then zip `README.md` and `code/` without the dataset.

macOS / Linux:

```bash
zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/.DS_Store"
```

Windows (PowerShell):

```powershell
Get-ChildItem code -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
Compress-Archive -Path README.md, code -DestinationPath code.zip -Force
```

Upload these items:

| Item | Source |
| --- | --- |
| `code.zip` | Built above. It contains the code, this guide and both evaluation reports. |
| `output.csv` | Repository root, from section 3. |
| `chat_transcript` | Repository root `log.txt`. |

Submission page: https://wwww.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists

## 8. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ModuleNotFoundError: No module named 'pypdf'` | Packages not installed, or the virtual environment is not active. | Activate `.venv` and run `pip install -r code/requirements.txt`. |
| `ModuleNotFoundError: No module named 'rapidocr_onnxruntime'` | The OCR cache is missing or a screenshot changed, so OCR has to run. | Install the requirements, or restore `code/cache/ocr_text.json`. |
| `FileNotFoundError` for a file in `dataset/` | The agent looks for `dataset/` next to `code/`, wherever you run it from. | Check that `dataset/` sits next to `code/` with all its files and the `evidence/` folder. |
| `REGRESSION: step_f1 ...` | A change lowered the sample score. | Run `python code/evaluation/main.py --verbose` to see which cases changed. |
| `--check` lists violations | `output.csv` is old or was edited by hand. | Run `python code/main.py` again, then `--check`. |
| Scorer shows old numbers | `sample_predictions.csv` is from an earlier run. | Run `python code/main.py --sample` first. |
| `warning: LLM intent call failed ...` | Bad key, no network, or the API is down. | The run still finishes with the keyword rules. Fix the key or unset it. |
