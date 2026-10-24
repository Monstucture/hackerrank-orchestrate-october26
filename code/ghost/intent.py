"""Decide whether request_text authorizes deletion.

Deterministic multilingual keyword gate with three outcomes:
- the person explicitly refuses or limits deletion   -> not a deletion
- another request (copy, opt-out, ...) and no deletion word -> not a deletion
- a deletion word, alone or next to another request -> deletion; when both
  appear the gate is unsure and an LLM, if configured, makes the call
With no deletion word and no known phrase, the LLM decides; offline the gate
says "not a deletion".
"""
import re

DELETE_WORDS = [
    r"\bdelet", r"\beras", r"\bremov", r"\bgone\b", r"\bclos(e|ed|ing)\b", r"\bpurge",
    r"\bwipe", r"\bforget me", r"\bdisconnect",
    r"supprim", r"suppression", r"eliminaci", r"elimin(ar|en|e)\b", r"exclus", r"exclu(ir|am)\b",
    r"\bborr(ar|en|a|e)\b", r"löschen", r"löschung", r"cancellare", r"삭제", r"削除", r"消除", r"删除",
]
# The person says deletion is not wanted, or limits the request to something else.
REFUSALS = [
    r"\bnot (asking|a request) (for|to) (delet|eras|clos|remov)",
    # only when the refusal covers the account itself, not "do not delete my invoices"
    r"\b(don't|do not) (delete|close|erase|remove)( or \w+)? (it|the account|my account|anything(?=\s*([.,;!?]|$)))\b",
    r"\bwithout (removing|deleting|erasing)\b", r"\bmarketing lists? only\b",
]
# Other kinds of requests; they rule out deletion only when no deletion word appears.
OTHER_REQUESTS = [
    r"\bstop the promotional", r"\bunsubscribe", r"\bcopy of (the )?(information|my data|last year)",
    r"\bdownload my\b", r"\bportable format\b", r"\bwhat personal information\b", r"\bmisspelled\b",
    r"\bout of date\b", r"\bchange the email\b", r"\breset(ting)? the login\b",
    r"\binvoices?\b.*\bfor my records\b", r"\bleave my profile\b", r"\bkeep my account\b",
    r"\bkeep the account\b", r"\bleave the account active\b", r"\bcontinue using\b",
]


def keyword_intent(text):
    """True / False when the rules decide, None when unsure."""
    t = text.lower()
    if any(re.search(p, t) for p in REFUSALS):
        return False
    deletion = any(re.search(p, t) for p in DELETE_WORDS)
    other = any(re.search(p, t) for p in OTHER_REQUESTS)
    if deletion and other:
        return None
    if deletion:
        return True
    return False if other else None


def authorizes_deletion(text, llm=None):
    t = text.lower()
    verdict = keyword_intent(text)
    if verdict is None and llm is not None:
        verdict = llm.classify_intent(text)
    if verdict is None:
        # offline, a deletion word outweighs a side request: missing a deletion is the costlier error
        verdict = any(re.search(p, t) for p in DELETE_WORDS)
    return verdict
