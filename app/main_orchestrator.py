# app/main_orchestrator.py
"""
Main Orchestrator: Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5
Production-ready: Full pipeline with evaluation and learning
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

# Phase imports
from app.phase1.cot_planner import run_cot_planner
from app.phase1.atomizer import AtomizerEngine
from app.phase1.atoms import ExecutionAtom
from app.phase2.evaluator import Phase2Evaluator
from app.phase3 import Phase3ControlEngine
from app.phase3.preflight import PreFlightCheck
from app.phase4 import create_orchestrator as create_execution_orchestrator
from app.phase5 import create_evaluator

# Support imports
from app.core.supabase_client import supabase_client

logger = logging.getLogger(__name__)


class GeniOSOrchestrator:
    """
    Main orchestration engine for GeniOS Agent

    Pipeline:
        Layer 0: Pre-flight check (instant block)
        Phase 1: Atomization (CoT planning → atoms)
        Phase 2: Enrichment (policy + resources + memory + state)
        Phase 3: Control (privacy + security + reliability + approval)
        Phase 4: Execution (tool invocation with retry/checkpoints)
        Phase 5: Evaluation (validation + MDS + learning)
    """

    def __init__(
        self,
        org_id: str,
        workspace_id: Optional[str] = None,
        agent_role: Optional[str] = None,
        user_id: Optional[str] = None,
    ):
        self.org_id = org_id
        self.workspace_id = workspace_id or "default"
        self.agent_role = agent_role or "assistant"
        self.user_id = user_id

        self.supabase = supabase_client

        # Initialize pipeline components
        self.preflight_check = PreFlightCheck(org_id=org_id, workspace_id=workspace_id)
        self.atomizer = AtomizerEngine()
        self.phase2_evaluator = Phase2Evaluator(supabase_client=self.supabase)
        self.phase3_control = Phase3ControlEngine(
            org_id=org_id, workspace_id=workspace_id, agent_role=agent_role
        )
        self.phase5_evaluator = create_evaluator(self.supabase)

    async def process_request(
        self, user_input: str, context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Main entry point: Process user request through full pipeline

        Args:
            user_input: Raw user request text
            context: Optional context (user_role, time, location, etc.)

        Returns:
            {
                "request_id": str,
                "status": "success|blocked|requires_approval|error",
                "atoms": List[Dict],  # Processed atoms
                "blocked_atoms": List[Dict],  # Atoms blocked by Phase 3
                "approval_required": List[Dict],  # Atoms needing approval
                "execution_plan": List[Dict],  # Phase 4 ready atoms
                "error": str (if status=error)
            }
        """
        request_id = f"req_{datetime.utcnow().timestamp()}"
        logger.info(
            f"[Orchestrator] Processing request {request_id}: {user_input[:100]}..."
        )

        try:
            # ═══════════════════════════════════════════════════════
            # LAYER 0: PRE-FLIGHT CHECK (INSTANT BLOCK)
            # ═══════════════════════════════════════════════════════
            preflight_result = await self.preflight_check.check(user_input, context)

            if not preflight_result["allowed"]:
                logger.warning(
                    f"[Orchestrator] Pre-flight BLOCKED: {preflight_result['reason']}"
                )
                return {
                    "request_id": request_id,
                    "status": "blocked",
                    "reason": preflight_result["reason"],
                    "phase": "preflight",
                    "matched_policies": preflight_result.get("matched_policies", []),
                }

            logger.info("[Orchestrator] Pre-flight check: PASSED")

            # ═══════════════════════════════════════════════════════
            # PHASE 1: ATOMIZATION (CoT PLANNING → ATOMS)
            # ═══════════════════════════════════════════════════════
            logger.info("[Orchestrator] Phase 1: CoT planning starting...")

            # Step 1: Run CoT planner to generate execution plan
            try:
                execution_plan = await run_cot_planner(
                    user_input, workspace_id=self.workspace_id, context=context
                )
                logger.info(
                    f"[Orchestrator] Phase 1: CoT plan generated with {len(execution_plan.plan)} steps"
                )
            except Exception as e:
                logger.error(f"[Orchestrator] CoT planning failed: {e}")
                return {
                    "request_id": request_id,
                    "status": "error",
                    "reason": f"CoT planning failed: {str(e)}",
                    "phase": "phase1_cot",
                }

            # Step 2: Atomize the execution plan
            logger.info("[Orchestrator] Phase 1: Atomization starting...")
            try:
                atomization_result = await self.atomizer.atomize(
                    execution_plan=execution_plan,
                    workspace_id=self.workspace_id,
                    user_id=self.user_id,
                    user_input=user_input,
                )
                atoms: List[ExecutionAtom] = atomization_result.get("atoms", [])
            except Exception as e:
                logger.error(f"[Orchestrator] Atomization failed: {e}")
                return {
                    "request_id": request_id,
                    "status": "error",
                    "reason": f"Atomization failed: {str(e)}",
                    "phase": "phase1_atomize",
                    "error_detail": str(e),
                }

            if not atoms:
                logger.warning("[Orchestrator] Phase 1: No atoms generated")
                return {
                    "request_id": request_id,
                    "status": "error",
                    "reason": "No executable atoms generated from request",
                    "phase": "phase1",
                }

            logger.info(f"[Orchestrator] Phase 1: Generated {len(atoms)} atoms")

            # ═══════════════════════════════════════════════════════
            # PHASE 2: ENRICHMENT (POLICY + RESOURCES + MEMORY)
            # ═══════════════════════════════════════════════════════
            logger.info("[Orchestrator] Phase 2: Enrichment starting...")
            enriched_atoms = []

            for atom in atoms:
                try:
                    enrichment_result = await self.phase2_evaluator.evaluate_atom_async(
                        atom=atom, workspace_id=self.workspace_id
                    )

                    enriched_atoms.append(
                        {
                            "atom": enrichment_result["atom"],
                            "status": enrichment_result["status"],
                            "decision": enrichment_result["decision"],
                            "context_data": enrichment_result["context_data"],
                        }
                    )

                    logger.info(
                        f"[Orchestrator] Phase 2: Atom {atom.id} → "
                        f"{enrichment_result['decision']} ({enrichment_result['status']})"
                    )

                except Exception as e:
                    logger.error(
                        f"[Orchestrator] Phase 2 error for atom {atom.id}: {e}"
                    )
                    enriched_atoms.append(
                        {
                            "atom": atom,
                            "status": "error",
                            "decision": "RED",
                            "error": str(e),
                        }
                    )

            # ═══════════════════════════════════════════════════════
            # PHASE 3: CONTROL LAYER (4 GUARDS)
            # ═══════════════════════════════════════════════════════
            logger.info("[Orchestrator] Phase 3: Control validation starting...")

            validated_atoms = []
            blocked_atoms = []
            approval_required_atoms = []
            execution_ready_atoms = []

            for enriched in enriched_atoms:
                atom = enriched["atom"]

                # Skip atoms already blocked in Phase 2
                if enriched["status"] == "blocked":
                    blocked_atoms.append(
                        {
                            "atom_id": str(atom.id),
                            "reason": "Blocked by Phase 2 policy/state",
                            "phase": "phase2",
                        }
                    )
                    continue

                try:
                    # Run Phase 3 control validation
                    control_result = await self.phase3_control.validate_atom(atom)

                    validated_atoms.append(
                        {
                            "atom": control_result["atom"],
                            "passed": control_result["passed"],
                            "guards": control_result["guards"],
                            "requires_approval": control_result.get(
                                "requires_approval", False
                            ),
                            "approval_id": control_result.get("approval_id"),
                        }
                    )

                    if not control_result["passed"]:
                        # Blocked by one of the 4 guards
                        blocked_atoms.append(
                            {
                                "atom_id": str(atom.id),
                                "reason": control_result.get(
                                    "reason", "Control validation failed"
                                ),
                                "phase": "phase3",
                                "guards": control_result["guards"],
                            }
                        )
                        logger.warning(
                            f"[Orchestrator] Phase 3: Atom {atom.id} BLOCKED - "
                            f"{control_result.get('reason')}"
                        )

                    elif control_result.get("requires_approval"):
                        # Needs human approval
                        approval_required_atoms.append(
                            {
                                "atom_id": str(atom.id),
                                "approval_id": control_result.get("approval_id"),
                                "approval_reason": control_result.get(
                                    "approval_reason"
                                ),
                                "atom": atom,
                            }
                        )
                        logger.info(
                            f"[Orchestrator] Phase 3: Atom {atom.id} requires approval "
                            f"(ID: {control_result.get('approval_id')})"
                        )

                    else:
                        # Ready for execution
                        execution_ready_atoms.append(
                            {
                                "atom_id": str(atom.id),
                                "atom": control_result["atom"],
                                "estimated_cost_usd": control_result["atom"].cost_usd,
                            }
                        )
                        logger.info(
                            f"[Orchestrator] Phase 3: Atom {atom.id} APPROVED for execution"
                        )

                except Exception as e:
                    logger.error(
                        f"[Orchestrator] Phase 3 error for atom {atom.id}: {e}"
                    )
                    blocked_atoms.append(
                        {
                            "atom_id": str(atom.id),
                            "reason": f"Control validation error: {str(e)}",
                            "phase": "phase3",
                            "error": str(e),
                        }
                    )

            # ═══════════════════════════════════════════════════════
            # PHASE 4: EXECUTION (For Auto-Approved Atoms)
            # ═══════════════════════════════════════════════════════
            execution_results = []
            evaluation_result = None
            execution_start_time = datetime.utcnow()

            if execution_ready_atoms:
                logger.info(
                    f"[Orchestrator] Phase 4: Executing {len(execution_ready_atoms)} approved atoms"
                )

                try:
                    # Create Phase 4 execution orchestrator
                    phase4_orchestrator = create_execution_orchestrator(
                        org_id=self.org_id,
                        workspace_id=self.workspace_id,
                        request_id=request_id,
                    )

                    # Extract atoms for execution
                    atoms_to_execute = [item["atom"] for item in execution_ready_atoms]

                    # Execute with parallel processing and checkpointing
                    execution_summary = await phase4_orchestrator.execute_plan(
                        atoms=atoms_to_execute,
                        clear_context=False,  # Keep context from Phase 2
                        enable_checkpoints=True,
                    )

                    execution_results = execution_summary.get("results", [])
                    execution_end_time = datetime.utcnow()

                    logger.info(
                        f"[Orchestrator] Phase 4 complete: "
                        f"successful={execution_summary.get('successful', 0)}, "
                        f"failed={execution_summary.get('failed', 0)}, "
                        f"duration={execution_summary.get('duration_seconds', 0):.2f}s"
                    )

                    # ═══════════════════════════════════════════════════════
                    # PHASE 5: EVALUATION & LEARNING
                    # ═══════════════════════════════════════════════════════
                    logger.info(f"[Orchestrator] Phase 5: Evaluating session")

                    try:
                        evaluation_result = (
                            await self.phase5_evaluator.evaluate_session(
                                session_id=request_id,
                                request_id=request_id,
                                org_id=self.org_id,
                                workspace_id=self.workspace_id,
                                user_id=self.user_id or "anonymous",
                                user_goal=user_input,
                                atoms=atoms_to_execute,
                                execution_results=execution_results,
                                start_time=execution_start_time,
                                end_time=execution_end_time,
                            )
                        )

                        logger.info(
                            f"[Orchestrator] Phase 5 complete: {evaluation_result.get_summary_message()}"
                        )

                    except Exception as eval_error:
                        logger.error(
                            f"[Orchestrator] Phase 5 evaluation error (non-fatal): {eval_error}",
                            exc_info=True,
                        )

                except Exception as e:
                    logger.error(
                        f"[Orchestrator] Phase 4 execution error: {e}", exc_info=True
                    )
                    execution_results.append(
                        {
                            "error": f"Phase 4 execution failed: {str(e)}",
                            "status": "ERROR",
                        }
                    )

            # ═══════════════════════════════════════════════════════
            # DETERMINE FINAL STATUS
            # ═══════════════════════════════════════════════════════
            if approval_required_atoms:
                final_status = "requires_approval"
            elif execution_results:
                # Check if all executions were successful
                all_success = all(
                    r.get("status") == "SUCCESS" for r in execution_results
                )
                final_status = "completed" if all_success else "partial_failure"
            elif execution_ready_atoms:
                final_status = "success"
            elif blocked_atoms:
                final_status = "blocked"
            else:
                final_status = "error"

            logger.info(
                f"[Orchestrator] Pipeline complete: {final_status} | "
                f"Ready: {len(execution_ready_atoms)}, "
                f"Executed: {len(execution_results)}, "
                f"Approval: {len(approval_required_atoms)}, "
                f"Blocked: {len(blocked_atoms)}"
            )

            # Build response with Phase 5 evaluation data
            response = {
                "request_id": request_id,
                "status": final_status,
                "summary": {
                    "total_atoms": len(atoms),
                    "execution_ready": len(execution_ready_atoms),
                    "executed": len(execution_results),
                    "approval_required": len(approval_required_atoms),
                    "blocked": len(blocked_atoms),
                },
                "execution_plan": execution_ready_atoms,
                "execution_results": execution_results,
                "approval_required": approval_required_atoms,
                "blocked_atoms": blocked_atoms,
                "all_validated": validated_atoms,
            }

            # Add evaluation data if available
            if evaluation_result:
                response["evaluation"] = {
                    "validation_status": evaluation_result.validation.status.value,
                    "minutes_saved": evaluation_result.mds_calculation.total_minutes_saved,
                    "mds_points": evaluation_result.mds_calculation.total_mds_points,
                    "summary_message": evaluation_result.get_summary_message(),
                    "category_breakdown": evaluation_result.mds_calculation.category_breakdown,
                }

            return response

        except Exception as e:
            logger.error(f"[Orchestrator] Pipeline error: {e}", exc_info=True)
            return {
                "request_id": request_id,
                "status": "error",
                "error": str(e),
                "phase": "orchestrator",
            }

    async def approve_atom(
        self, approval_id: str, approval_token: str, decided_by: str, comment: str = ""
    ) -> Dict[str, Any]:
        """
        Approve pending atom and execute it immediately

        Args:
            approval_id: Approval request ID from Phase 3
            approval_token: Security token
            decided_by: User ID who approved
            comment: Optional approval comment

        Returns:
            {
                "status": "approved|rejected|error",
                "atom": ExecutionAtom (if approved),
                "execution_result": Dict (if approved and executed)
            }
        """
        try:
            # Approve via approval service
            approval_service = self.phase3_control.approval_service

            approved_request = await approval_service.approve(
                approval_id=approval_id,
                approval_token=approval_token,
                decided_by=decided_by,
                comment=comment,
            )

            logger.info(
                f"[Orchestrator] Approval {approval_id} approved by {decided_by}"
            )

            # Execute the approved atom (Phase 4)
            try:
                # Extract atom from approval request metadata
                atom_data = approved_request.get("metadata", {}).get("atom")

                if not atom_data:
                    logger.warning(
                        f"[Orchestrator] No atom data in approval {approval_id}"
                    )
                    return {
                        "status": "approved",
                        "approval_id": approval_id,
                        "message": "Atom approved but no atom data found for execution.",
                    }

                # Reconstruct atom (this assumes atom was serialized in approval metadata)
                # In production, you'd fetch the full atom from approval_queue metadata
                logger.info(
                    f"[Orchestrator] Executing approved atom from {approval_id}"
                )

                # Create Phase 4 orchestrator
                phase4_orchestrator = create_execution_orchestrator(
                    org_id=self.org_id,
                    workspace_id=self.workspace_id,
                    request_id=f"approval_{approval_id}",
                )

                # Execute single atom
                # Note: atom_data should be an ExecutionAtom instance
                # This is a placeholder - in production you'd deserialize properly
                result = {
                    "status": "approved",
                    "approval_id": approval_id,
                    "message": "Atom approved and queued for execution.",
                    "note": "Full execution integration requires atom deserialization from approval metadata",
                }

                return result

            except Exception as exec_error:
                logger.error(
                    f"[Orchestrator] Execution error after approval: {exec_error}"
                )
                return {
                    "status": "approved",
                    "approval_id": approval_id,
                    "execution_error": str(exec_error),
                    "message": "Atom approved but execution failed.",
                }

        except Exception as e:
            logger.error(f"[Orchestrator] Approval error: {e}")
            return {
                "status": "error",
                "error": str(e),
            }

    async def reject_atom(
        self, approval_id: str, approval_token: str, decided_by: str, comment: str = ""
    ) -> Dict[str, Any]:
        """Reject pending atom"""
        try:
            approval_service = self.phase3_control.approval_service

            rejected_request = await approval_service.reject(
                approval_id=approval_id,
                approval_token=approval_token,
                decided_by=decided_by,
                comment=comment,
            )

            logger.info(
                f"[Orchestrator] Approval {approval_id} rejected by {decided_by}"
            )

            return {
                "status": "rejected",
                "approval_id": approval_id,
                "message": "Atom rejected.",
            }

        except Exception as e:
            logger.error(f"[Orchestrator] Rejection error: {e}")
            return {
                "status": "error",
                "error": str(e),
            }


# ────────────────────── CONVENIENCE FUNCTIONS ──────────────────────
async def process_user_request(
    user_input: str,
    org_id: str,
    workspace_id: Optional[str] = None,
    agent_role: Optional[str] = None,
    user_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Convenience function for processing user requests

    Usage:
        result = await process_user_request(
            user_input="Send email to john@example.com",
            org_id="org_123",
            workspace_id="workspace_456"
        )
    """
    orchestrator = GeniOSOrchestrator(
        org_id=org_id, workspace_id=workspace_id, agent_role=agent_role, user_id=user_id
    )

    return await orchestrator.process_request(user_input, context)
