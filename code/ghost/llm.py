"""Optional LLM fallback for the intent gate, with token accounting.

Uses the Anthropic Messages API over plain HTTPS when ANTHROPIC_API_KEY is
set. With no key the agent stays fully deterministic and makes no calls.
"""
import json
import os
import sys
import urllib.request

MODEL = os.environ.get("GHOST_MODEL", "claude-haiku-5-5")
PROMPT = ("You audit privacy requests. Answer with exactly one word, YES or NO: does this "
          "customer message ask for their account or personal data to be deleted or erased? "
          "Requests for access, copies, export, correction, login help or marketing opt-out are NO.\n\n"
          "Message:\n{text}")


class LLM:
    def __init__(self, key):
        self.key = key
        self.calls = self.input_tokens = self.output_tokens = 0
        self.cache = {}

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("ANTHROPIC_API_KEY"))

    def classify_intent(self, text):
        if not self.key:
            return None
        if text in self.cache:
            return self.cache[text]
        base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
        body = json.dumps({"model": MODEL, "max_tokens": 5,
                           "messages": [{"role": "user", "content": PROMPT.format(text=text)}]}).encode()
        req = urllib.request.Request(base + "/v1/messages", data=body, headers={
            "x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
        except (OSError, ValueError) as e:
            # unanswered: the caller falls back to its offline rule, so say so loudly
            print(f"warning: LLM intent call failed ({type(e).__name__}); using keyword fallback",
                  file=sys.stderr)
            return None
        self.calls += 1
        self.input_tokens += data.get("usage", {}).get("input_tokens", 0)
        self.output_tokens += data.get("usage", {}).get("output_tokens", 0)
        answer = "".join(b.get("text", "") for b in data.get("content", [])).strip().upper()
        self.cache[text] = answer.startswith("Y")
        return self.cache[text]

    def write_usage(self, path, requests):
        path.write_text(json.dumps({
            "model": MODEL if self.key else None, "calls": self.calls,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "requests": requests}, indent=2), encoding="utf-8")
