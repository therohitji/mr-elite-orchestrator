"""
Agent Capability Registry
=========================

This is the VALIDATION layer for what each agent can do.
Used by Phase 3 Pre-Flight validation ONLY.

DO NOT use this in Phase 1 atomizer.
Phase 1 uses capability_cache (from Supabase) for atom creation.

This registry validates that atoms match actual agent capabilities.
Prevents misrouting of already-created atoms to incompatible agents.

Used by:
  - Phase 3 agent_compatibility_validator: To validate agent assignments
  - Phase 4 Dispatcher: Optional logging of what agents support

KEY PRINCIPLE: Registry metadata matches what atomizer sends, not the other way around.
"""

from typing import Dict, List, Optional
from enum import Enum
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    """Risk classification for capabilities"""

    LOW = "low"  # Read-only, no side effects
    MEDIUM = "medium"  # Modifies data, but reversible
    HIGH = "high"  # Irreversible, financial, or external
    CRITICAL = "critical"  # Requires explicit approval


@dataclass
class CapabilitySpec:
    """Specification of what an agent can do"""

    name: str
    """
    Unique identifier: 'list_events', 'create_event', 'send_email', etc.
    This matches the tool_name in atoms.
    """

    description: str
    """Human-readable description of what this does"""

    agent_name: str
    """Which agent implements this: 'calendar_agent', 'email_agent', etc."""

    operation_type: str
    """READ | WRITE | DELETE | EXECUTE"""

    required_arguments: List[str]
    """Arguments that MUST be provided by user or atomizer"""

    optional_arguments: List[str]
    """Arguments that are nice-to-have but not required"""

    risk_level: RiskLevel
    """LOW | MEDIUM | HIGH | CRITICAL"""

    requires_user_confirmation: bool = False
    """If True, Phase 3 must ask user before execution"""

    requires_policy_approval: bool = False
    """If True, Phase 3 must check policies before execution"""

    incompatible_with: List[str] = None
    """Other capabilities that cannot run in same execution plan"""

    min_args_for_execution: Dict[str, type] = None
    """Strict type checking for critical arguments"""


# ============================================================================
# CALENDAR AGENT CAPABILITIES
# ============================================================================

CALENDAR_AGENT_CAPABILITIES = {
    "availability_check": CapabilitySpec(
        name="availability_check",
        description="Check calendar availability for users",
        agent_name="calendar_agent",
        operation_type="READ",
        required_arguments=[],
        optional_arguments=["user_id", "date_range"],
        risk_level=RiskLevel.LOW,
        requires_user_confirmation=False,
        requires_policy_approval=False,
    ),
    "event_create": CapabilitySpec(
        name="event_create",
        description="Create a new calendar event",
        agent_name="calendar_agent",
        operation_type="WRITE",
        required_arguments=["title"],
        optional_arguments=[
            "start_time",
            "end_time",
            "attendees",
            "description",
            "location",
        ],
        risk_level=RiskLevel.MEDIUM,
        requires_user_confirmation=False,
        requires_policy_approval=False,
    ),
    "event_update": CapabilitySpec(
        name="event_update",
        description="Modify an existing calendar event",
        agent_name="calendar_agent",
        operation_type="WRITE",
        required_arguments=["event_id"],
        optional_arguments=["new_title", "new_start", "new_end", "new_attendees"],
        risk_level=RiskLevel.MEDIUM,
        requires_user_confirmation=True,
        requires_policy_approval=False,
    ),
    "invite_write": CapabilitySpec(
        name="invite_write",
        description="Send calendar invitation to attendees",
        agent_name="calendar_agent",
        operation_type="WRITE",
        required_arguments=["event_id", "attendees"],
        optional_arguments=["message"],
        risk_level=RiskLevel.MEDIUM,
        requires_user_confirmation=True,
        requires_policy_approval=False,
    ),
}

# ============================================================================
# EMAIL AGENT CAPABILITIES
# ============================================================================

EMAIL_AGENT_CAPABILITIES = {
    "email_list": CapabilitySpec(
        name="email_list",
        description="Retrieve email threads from inbox",
        agent_name="email_agent",
        operation_type="READ",
        required_arguments=[],
        optional_arguments=["limit", "label", "search_query"],
        risk_level=RiskLevel.LOW,
        requires_user_confirmation=False,
        requires_policy_approval=False,
    ),
    "email_read": CapabilitySpec(
        name="email_read",
        description="Get details of a specific email thread",
        agent_name="email_agent",
        operation_type="READ",
        required_arguments=["thread_id"],
        optional_arguments=[],
        risk_level=RiskLevel.LOW,
        requires_user_confirmation=False,
        requires_policy_approval=False,
    ),
    "email_send": CapabilitySpec(
        name="email_send",
        description="Send an email to one or more recipients",
        agent_name="email_agent",
        operation_type="WRITE",
        required_arguments=["to", "subject", "body"],
        optional_arguments=["cc", "bcc", "thread_id", "attachments"],
        risk_level=RiskLevel.HIGH,
        requires_user_confirmation=True,
        requires_policy_approval=True,
    ),
    "email_draft": CapabilitySpec(
        name="email_draft",
        description="Create a draft email (not sent)",
        agent_name="email_agent",
        operation_type="WRITE",
        required_arguments=["to", "subject", "body"],
        optional_arguments=["cc", "bcc", "thread_id"],
        risk_level=RiskLevel.LOW,
        requires_user_confirmation=False,
        requires_policy_approval=False,
    ),
}

# ============================================================================
# COMBINED REGISTRY
# ============================================================================


class AgentRegistry:
    """
    Central registry for all agent capabilities.

    Usage:
        registry = AgentRegistry()

        # 1. Check if capability exists
        cap = registry.get_capability("list_events")

        # 2. Get all capabilities for an agent
        caps = registry.get_agent_capabilities("calendar_agent")

        # 3. Find which agent can do a task
        agent = registry.find_agent_for_capability("send_email")

        # 4. Validate argument completeness
        is_valid = registry.validate_arguments("send_email", user_args)
    """

    def __init__(self):
        # Build combined registry
        self._capabilities: Dict[str, CapabilitySpec] = {}
        self._agent_registry: Dict[str, Dict] = {}

        # Register calendar capabilities
        for cap_name, cap_spec in CALENDAR_AGENT_CAPABILITIES.items():
            self._capabilities[cap_name] = cap_spec
            self._register_agent(cap_spec)

        # Register email capabilities
        for cap_name, cap_spec in EMAIL_AGENT_CAPABILITIES.items():
            self._capabilities[cap_name] = cap_spec
            self._register_agent(cap_spec)

        logger.info(
            f"[AGENT REGISTRY] Initialized with {len(self._capabilities)} capabilities "
            f"across {len(self._agent_registry)} agents"
        )

    def _register_agent(self, cap_spec: CapabilitySpec):
        """Register capability under its agent"""
        agent_name = cap_spec.agent_name
        if agent_name not in self._agent_registry:
            self._agent_registry[agent_name] = {
                "capabilities": [],
                "domain": self._extract_domain(agent_name),
            }
        self._agent_registry[agent_name]["capabilities"].append(cap_spec)

    @staticmethod
    def _extract_domain(agent_name: str) -> str:
        """Extract domain from agent name: 'calendar_agent' → 'calendar'"""
        return agent_name.replace("_agent", "").lower()

    def get_capability(self, capability_name: str) -> Optional[CapabilitySpec]:
        """Get capability specification by name"""
        cap = self._capabilities.get(capability_name.lower())
        if not cap:
            logger.warning(f"[AGENT REGISTRY] Unknown capability: {capability_name}")
        return cap

    def get_agent_capabilities(self, agent_name: str) -> List[CapabilitySpec]:
        """Get all capabilities for a specific agent"""
        agent_info = self._agent_registry.get(agent_name)
        if not agent_info:
            logger.warning(f"[AGENT REGISTRY] Unknown agent: {agent_name}")
            return []
        return agent_info["capabilities"]

    def find_agent_for_capability(self, capability_name: str) -> Optional[str]:
        """Find which agent implements a capability"""
        cap = self.get_capability(capability_name)
        if cap:
            return cap.agent_name
        return None

    def validate_arguments(
        self, capability_name: str, provided_args: Dict[str, any]
    ) -> tuple[bool, Optional[str]]:
        """
        Validate that provided arguments satisfy capability requirements

        Returns:
            (is_valid: bool, error_message: Optional[str])
        """
        cap = self.get_capability(capability_name)
        if not cap:
            return False, f"Unknown capability: {capability_name}"

        # Check required arguments
        provided_keys = set(k.lower() for k in provided_args.keys())
        required_keys = set(k.lower() for k in cap.required_arguments)

        missing = required_keys - provided_keys
        if missing:
            return False, f"Missing required arguments for {capability_name}: {missing}"

        return True, None

    def is_compatible(self, cap1: str, cap2: str) -> bool:
        """Check if two capabilities can run in same execution plan"""
        spec1 = self.get_capability(cap1)
        spec2 = self.get_capability(cap2)

        if not spec1 or not spec2:
            return False

        if spec1.incompatible_with and cap2 in spec1.incompatible_with:
            return False
        if spec2.incompatible_with and cap1 in spec2.incompatible_with:
            return False

        return True

    def list_all_capabilities(self) -> Dict[str, List[str]]:
        """Get all capabilities grouped by agent"""
        result = {}
        for agent_name, agent_info in self._agent_registry.items():
            result[agent_name] = [cap.name for cap in agent_info["capabilities"]]
        return result

    def get_risk_level(self, capability_name: str) -> Optional[RiskLevel]:
        """Get risk classification of a capability"""
        cap = self.get_capability(capability_name)
        return cap.risk_level if cap else None

    def requires_approval(self, capability_name: str) -> bool:
        """Check if capability requires policy approval"""
        cap = self.get_capability(capability_name)
        return cap.requires_policy_approval if cap else False

    def requires_confirmation(self, capability_name: str) -> bool:
        """Check if capability requires user confirmation"""
        cap = self.get_capability(capability_name)
        return cap.requires_user_confirmation if cap else False


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

# Singleton instance
_registry_instance = None


def get_registry() -> AgentRegistry:
    """Get or create the global agent registry"""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = AgentRegistry()
    return _registry_instance


# ============================================================================
# CONVENIENCE FUNCTIONS (for use throughout codebase)
# ============================================================================


def get_agent_for_capability(capability_name: str) -> Optional[str]:
    """Quick lookup: Which agent can do this?"""
    return get_registry().find_agent_for_capability(capability_name)


def get_capabilities_for_agent(agent_name: str) -> List[str]:
    """Quick lookup: What can this agent do?"""
    return [cap.name for cap in get_registry().get_agent_capabilities(agent_name)]


def is_capability_supported(capability_name: str) -> bool:
    """Quick lookup: Is this capability available?"""
    return get_registry().get_capability(capability_name) is not None


def validate_capability_args(
    capability_name: str, provided_args: Dict[str, any]
) -> bool:
    """Quick check: Do arguments satisfy this capability's requirements?"""
    is_valid, _ = get_registry().validate_arguments(capability_name, provided_args)
    return is_valid
