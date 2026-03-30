# app/routers/__init__.py
"""
API Routers for modular phase testing.
Each phase has its own router for isolated testing.
"""

from .phase1 import router as phase1_router
from .phase1 import run_phase1_pipeline, Phase1Result

from .phase2 import router as phase2_router
from .phase2 import run_phase2_pipeline, Phase2Result

from .phase3 import router as phase3_router
from .phase3 import run_phase3_pipeline, Phase3Result

from .phase4 import router as phase4_router
from .phase4 import run_phase4_pipeline, Phase4Result

__all__ = [
    "phase1_router", "run_phase1_pipeline", "Phase1Result",
    "phase2_router", "run_phase2_pipeline", "Phase2Result",
    "phase3_router", "run_phase3_pipeline", "Phase3Result",
    "phase4_router", "run_phase4_pipeline", "Phase4Result",
]

