# Production Configuration for Phase 1

PHASE1_CONFIG = {
    # Classification
    "classifier": {
        "heuristic_enabled": True,
        "semantic_enabled": True,
        "complexity_range": (1, 10),
    },
    # Planning
    "planner": {
        "timeout_seconds": 30,
        "rag_enabled": True,
        "policy_context_limit": 5,
    },
    # Atomization
    "atomizer": {
        "dag_validation": True,
        "circular_dependency_check": True,
    },
    # Pre-Flight
    "pre_flight": {
        "vector_db_enabled": True,
        "blocking_threshold": 0.85,
        "fail_open": True,
    },
    # Tool Registry
    "tools": {
        "deterministic_resolution": True,
        "unresolved_status": "PAUSED",
    },
    # API
    "api": {
        "async_enabled": True,
        "startup_init_vector_db": True,
    },
}
