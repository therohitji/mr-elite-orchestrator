from app.phase1.atoms import ExecutionAtom
from app.phase2.contracts import CheckResult

AVAILABLE_RESOURCES = {
    "offer_template",
    "welcome_email_template",
    "onboarding_checklist",
}


def run_resource_check(atom: ExecutionAtom) -> CheckResult | None:
    """
    HARD FAIL if resource missing
    """

    if "offer" in atom.tool_name:
        resource = "offer_template"
    elif "email" in atom.tool_name:
        resource = "welcome_email_template"
    elif "onboarding" in atom.description.lower():
        resource = "onboarding_checklist"
    else:
        return None

    if resource in AVAILABLE_RESOURCES:
        return CheckResult(
            check_type="resource",
            status="pass",
            message=f"Resource '{resource}' available",
        )

    return CheckResult(
        check_type="resource",
        status="fail",
        message=f"Missing resource '{resource}'",
    )
