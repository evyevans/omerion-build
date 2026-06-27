from typing import Annotated, Any, Optional
from operator import add

from pydantic import Field

from omerion_core.domain.wartt_schemas import ClientProfile
from omerion_core.state.base import AgentRunState


def _take_last(current: Any, update: Any) -> Any:
    """Reducer: last writer wins for scalar mid-graph fields."""
    return update if update is not None else current


class IntakeState(AgentRunState):
    agent_name: str = "client_intake"

    # ── Set at graph START ────────────────────────────────────────
    blueprint_id: str
    transcript_text: str = ""
    founder_notes: str = ""

    # ── Populated mid-graph ───────────────────────────────────────
    client_id: Annotated[Optional[str], _take_last] = None
    client_profile: Annotated[Optional[ClientProfile], _take_last] = None
    confidence_score: Annotated[float, _take_last] = 0.0
    extraction_attempts: Annotated[int, _take_last] = 0

    # ── List accumulators (reducer = add = list concatenation) ────
    data_gaps: Annotated[list[str], add] = Field(default_factory=list)
    rag_similar_profiles: Annotated[list[dict], add] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}
