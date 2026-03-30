# Phase-2 PDF: policies are text rules, not code

POLICY_RULES = [
    {
        "id": "EMAIL_APPROVAL",
        "rule": "Any external email must be approved by a human.",
        "severity": "soft",
    },
    {
        "id": "HR_SENSITIVE",
        "rule": "HR actions involving people data require manager approval.",
        "severity": "hard",
    },
]
