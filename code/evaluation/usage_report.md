# Usage Report — final full-dataset run

Run that produced `output.csv`: `python code/main.py`, 518 requests, about 5 s on a laptop CPU. The counters below are written by the run itself to `code/evaluation/usage_stats.json`.

## Model usage

| Provider | Model | Purpose | Calls | Input tokens | Output tokens | Total tokens |
| --- | --- | --- | --- | --- | --- | --- |
| Anthropic (optional) | `claude-haiku-5-5` (`GHOST_MODEL`) | Intent fallback for request texts the keyword rules cannot decide | 0 | 0 | 0 | 0 |
| Local, no provider | RapidOCR ONNX (PP-OCR) | Text from the 11 screenshot evidence files | 11 images, once; cached in `code/cache/ocr_text.json` | n/a | n/a | n/a |
| **Overall** | | | **0 LLM calls** | **0** | **0** | **0** |

- Average tokens per request: **0** (0 / 518).
- Estimated total cost: **$0.00**; per request: **$0.00**. OCR and PDF extraction run locally.

## Why the run makes no LLM calls

The audit itself (evidence extraction, identity resolution, state at `audit_at`, policy, step planning) is deterministic code. An LLM is consulted only when the multilingual keyword gate cannot classify a request. In this dataset every one of the 518 evaluation and 32 sample texts matches either a deletion pattern or a non-deletion pattern, so the fallback never fires.

If a future dataset has undecided texts, each fallback call sends a prompt of about 120 input tokens plus the request text, and asks for a one-word answer (max 5 output tokens). Answers are cached per text. Even if all 518 requests went to the fallback, that would be roughly 100k input tokens and 2.6k output tokens in total. The calls, tokens and model name would then appear in `usage_stats.json`.

Development-time use of the coding agent (Claude Code) is recorded in `log.txt`. It is not part of the solution's runtime cost.
