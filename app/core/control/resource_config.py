# app/core/control/resource_config.py
"""
Resource Limits Configuration - Production-Ready
Tuned for enterprise scale with per-tool granularity
"""

RESOURCE_LIMITS = {
    # ────────────────────── GLOBAL LIMITS ──────────────────────
    "max_requests_per_minute": 300,  # Global rate limit across all tools
    "max_tokens_per_minute": 50_000,  # GPT-4 token budget per minute
    "max_cost_per_minute_usd": 5.0,  # USD cost budget per minute
    # ────────────────────── PER-TOOL LIMITS ──────────────────────
    # High-risk tools (write operations)
    "max_send_email_per_minute": 10,
    "max_send_slack_per_minute": 15,
    "max_draft_offer_per_minute": 5,
    "max_approve_payment_per_minute": 3,
    "max_create_user_per_minute": 10,
    "max_delete_user_per_minute": 2,
    # Medium-risk tools (moderate operations)
    "max_fetch_data_per_minute": 50,
    "max_search_database_per_minute": 100,
    "max_generate_report_per_minute": 20,
    # Low-risk tools (read-only)
    "max_read_file_per_minute": 100,
    "max_list_items_per_minute": 100,
    "max_check_status_per_minute": 200,
    # Default for unlisted tools
    "max_default_per_minute": 20,
    # ────────────────────── CIRCUIT BREAKER ──────────────────────
    "fail_safe_mode": "PAUSE",  # PDF rule: Pause on infra failure
    "circuit_breaker_threshold": 5,  # Failures before circuit opens
    "circuit_breaker_timeout_seconds": 60,  # Time before retry
    # ────────────────────── TOKEN BUDGETS ──────────────────────
    # Per-atom token limits (prevent runaway costs)
    "max_tokens_per_atom": 5000,  # Max tokens for single atom
    "max_context_tokens": 4000,  # Max context from Phase 2
    "max_output_tokens": 2000,  # Max LLM output tokens
    # ────────────────────── COST BUDGETS ──────────────────────
    # Per-atom cost limits
    "max_cost_per_atom_usd": 0.50,  # Max cost for single atom
    "high_risk_cost_multiplier": 2.0,  # Cost multiplier for high-risk
    # ────────────────────── APPROVAL THRESHOLDS ──────────────────────
    # Auto-approval limits
    "auto_approve_cost_threshold_usd": 0.10,  # Auto-approve if cost < $0.10
    "auto_approve_risk_levels": ["low", "medium"],  # Auto-approve these risk levels
    # ────────────────────── DEGRADED MODE ──────────────────────
    # When Redis unavailable, use in-memory with relaxed limits
    "degraded_mode_multiplier": 2.0,  # Allow 2x normal limit in degraded mode
}


# ────────────────────── TOOL RISK CLASSIFICATIONS ──────────────────────
TOOL_RISK_LEVELS = {
    # CRITICAL: Irreversible, financial impact, data loss
    "approve_payment": "critical",
    "delete_user": "critical",
    "delete_data": "critical",
    "fire_employee": "critical",
    "drop_table": "critical",
    # HIGH: Visible external impact, compliance risk
    "send_email": "high",
    "send_slack": "high",
    "draft_offer": "high",
    "post_to_social": "high",
    "create_user": "high",
    "modify_permissions": "high",
    # MEDIUM: Internal operations, limited scope
    "fetch_data": "medium",
    "search_database": "medium",
    "generate_report": "medium",
    "update_record": "medium",
    # LOW: Read-only, no side effects
    "read_file": "low",
    "list_items": "low",
    "check_status": "low",
    "get_info": "low",
}


# ────────────────────── APPROVAL REQUIREMENTS ──────────────────────
APPROVAL_REQUIREMENTS = {
    "critical": {
        "requires_approval": True,
        "min_approvers": 2,  # Requires 2 approvals
        "approval_timeout_hours": 4,  # Urgent approval (4 hours)
    },
    "high": {
        "requires_approval": True,
        "min_approvers": 1,
        "approval_timeout_hours": 24,
    },
    "medium": {
        "requires_approval": False,  # Auto-approved
        "min_approvers": 0,
        "approval_timeout_hours": 0,
    },
    "low": {
        "requires_approval": False,
        "min_approvers": 0,
        "approval_timeout_hours": 0,
    },
}
