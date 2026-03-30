from typing import Literal, Optional
from pydantic import BaseModel, Field

class StatementMetadata(BaseModel):
    statement_type: Literal[
        "none",
        "opinion",
        "fact",
        "figure",
        "policy_candidate"
    ]
    confidence_score: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="How confident the system is that this is knowledge worth saving"
    )
    extracted_value: Optional[str] = Field(
        None,
        description="Normalized value (number, rule text, fact statement)"
    )

class ClassificationOutput(BaseModel):
    intent: Literal["chat", "query", "task", "goal"]
    complexity_score: int = Field(..., ge=1, le=10)
    domain: Literal["hr", "finance", "engineering", "admin", "general"]
    risk_level: Literal["low", "medium", "high"]
    reasoning: str

    # 🔹 NEW
    statement: StatementMetadata
