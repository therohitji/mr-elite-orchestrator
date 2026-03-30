# app/core/control/patterns.py

PII_PATTERNS = {
    "email": r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
    "phone": r"\b\d{10}\b",
    "api_key": r"sk-[a-zA-Z0-9]{20,}",
    "credit_card": r"\b(?:\d[ -]*?){13,16}\b",
}

INJECTION_PATTERNS = [
    "ignore previous instructions",
    "system prompt",
    "developer message",
    "jailbreak",
    "drop database",
    "delete table",
    "sudo",
]

SYNTAX_RISK_CHARS = [";", "--", "/*", "*/"]
