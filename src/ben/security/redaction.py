"""PII redaction for logs (and optionally for model input).

Covers emails, phone numbers, UAE Emirates ID numbers, passport numbers and airline booking
references (PNRs). Pattern-based redaction is a safety net, not a guarantee - users are also
told not to share personal data.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_EMIRATES_ID = re.compile(r"\b784[- ]?\d{4}[- ]?\d{7}[- ]?\d\b")
# 9+ digits with optional separators; 8-digit ISO dates such as 2024-08-02 are left alone.
_PHONE = re.compile(r"(?<![\w/])(?:\+|00)?\d(?:[\s().-]?\d){8,14}(?![\w/])")
_PASSPORT_CTX = re.compile(
    r"(?i)(\bpassport(?:\s*(?:no\.?|number|#))?\s*[:#]?\s*)([A-Z0-9]{6,9})\b"
)
_PNR_CTX = re.compile(
    r"(?i)(\b(?:pnr|booking(?:\s*ref(?:erence)?)?|reservation(?:\s*code)?|record\s+locator|"
    r"confirmation\s+(?:code|number))(?:\s*(?:no\.?|number|code|#))?\s*[:#]?\s*)([A-Z0-9]{6})\b"
)
_PASSPORT = re.compile(r"\b[A-Z]{1,2}\d{6,8}\b")
# 6-char codes mixing letters and digits (typical PNR shape, e.g. X7K2QP).
_PNR = re.compile(r"\b(?=[A-Z0-9]{6}\b)(?=[A-Z]*\d)(?=\d*[A-Z])[A-Z0-9]{6}\b")


@dataclass(frozen=True)
class RedactionResult:
    text: str
    counts: dict[str, int]

    @property
    def redacted(self) -> bool:
        return bool(self.counts)


def redact(text: str | None) -> RedactionResult:
    if not text:
        return RedactionResult(text or "", {})
    counts: Counter[str] = Counter()

    def sub(pattern: re.Pattern, label: str, s: str, group: int | None = None) -> str:
        def repl(m: re.Match) -> str:
            counts[label] += 1
            return f"{m.group(1)}[{label}]" if group else f"[{label}]"

        return pattern.sub(repl, s)

    text = sub(_EMAIL, "EMAIL", text)
    text = sub(_EMIRATES_ID, "EMIRATES_ID", text)
    text = sub(_PASSPORT_CTX, "PASSPORT", text, group=2)
    text = sub(_PNR_CTX, "BOOKING_REF", text, group=2)
    text = sub(_PHONE, "PHONE", text)
    text = sub(_PASSPORT, "PASSPORT", text)
    text = sub(_PNR, "BOOKING_REF", text)
    return RedactionResult(text, dict(counts))


def redact_text(text: str | None) -> str:
    return redact(text).text
