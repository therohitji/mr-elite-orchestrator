# app/core/control/segment1_sanitizer.py

import re
from app.core.control.patterns import (
    PII_PATTERNS,
    INJECTION_PATTERNS,
    SYNTAX_RISK_CHARS,
)


class Segment1Sanitizer:
    """
    Segment-1: Hard Filters (PDF-aligned)
    - PII detection & redaction
    - Prompt-injection blocking
    - Syntax sanity checks
    """

    def scan(self, atom):
        """
        Returns:
        (passed: bool, reason: str, atom)
        """

        # ---------------------------
        # 1. Prompt Injection Guard
        # ---------------------------
        description = atom.description.lower()

        for phrase in INJECTION_PATTERNS:
            if phrase in description:
                return (
                    False,
                    f"Blocked: Prompt-injection detected ({phrase})",
                    atom,
                )

        # ---------------------------
        # 2. PII / DLP Guard
        # ---------------------------
        args_str = str(atom.tool_arguments)
        redacted_args = args_str
        pii_found = False

        for label, pattern in PII_PATTERNS.items():
            if re.search(pattern, redacted_args):
                pii_found = True
                redacted_args = re.sub(pattern, "[REDACTED]", redacted_args)

        if pii_found:
            # Redaction allowed (PDF rule)
            atom.tool_arguments = redacted_args

        # ---------------------------
        # 3. Syntax / Payload Guard
        # ---------------------------
        for bad_char in SYNTAX_RISK_CHARS:
            if bad_char in args_str:
                return (
                    False,
                    f"Blocked: Malformed payload ({bad_char})",
                    atom,
                )

        return True, "Sanitized", atom
