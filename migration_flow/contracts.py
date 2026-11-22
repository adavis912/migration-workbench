"""Message contract and migration-record model (Checkpoint 5.1, Section 4).

Every task and result between the Orchestrator and a specialist agent is a Message. It is validated
before it is accepted, so a malformed result never reaches the next stage.
"""
import datetime as dt
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AGENTS = ("orchestrator", "discovery", "wave_planner", "reviewer", "builder", "validation", "human")


class Stage(str, Enum):
    planned = "planned"                    # in an approved wave plan, nothing built yet
    building = "building"
    built = "built"                        # code generated, terraform validate passed
    in_review = "in_review"
    review_rejected = "review_rejected"    # sent back to the Builder with reasons
    reviewed = "reviewed"                  # Reviewer approved the change set
    validated = "validated"                # Validation agent finished (dry-run: static checks only)
    ready = "ready"                        # ready for the cutover gate
    held = "held"                          # deferred: own failure, or a failure elsewhere in its move group
    blocked = "blocked"                    # retries exhausted; needs a person


ALLOWED_TRANSITIONS = {
    Stage.planned: {Stage.building, Stage.held},
    Stage.building: {Stage.built, Stage.blocked, Stage.held},
    Stage.built: {Stage.in_review, Stage.held},
    Stage.in_review: {Stage.reviewed, Stage.review_rejected, Stage.blocked, Stage.held},
    Stage.review_rejected: {Stage.building, Stage.blocked, Stage.held},
    Stage.reviewed: {Stage.validated, Stage.held},
    Stage.validated: {Stage.ready, Stage.held},
    Stage.ready: {Stage.held},
    Stage.held: {Stage.planned},
    Stage.blocked: {Stage.planned},
}


def now():
    return dt.datetime.now(dt.timezone.utc)


class Message(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    task_id: str
    from_agent: Literal[AGENTS] = Field(alias="from")
    to: Literal[AGENTS]
    type: Literal["task", "result", "blocked", "review_request", "review_verdict", "approval"]
    wave_id: str
    app_id: str
    payload: dict[str, Any] = {}
    evidence: list[str] = []
    status: str = "pending"
    attempt: int = Field(1, ge=1, le=3)
    deadline: dt.datetime

    @field_validator("app_id")
    @classmethod
    def _app_id(cls, v):
        import re
        if not re.fullmatch(r"[A-Z]{2,4}-[A-Z0-9-]{2,20}", v): raise ValueError(f"bad app_id {v!r}")
        return v

    @field_validator("evidence")
    @classmethod
    def _evidence_for_results(cls, v, info):
        if info.data.get("type") in ("result", "review_verdict", "blocked", "approval") and not v:
            raise ValueError("result, verdict and blocked messages must cite evidence IDs")
        return v


class BuildPayload(BaseModel):
    """Builder -> Orchestrator result payload."""
    model_config = ConfigDict(extra="forbid")
    files: dict[str, str]                       # path -> sha256 (content lives in the work dir)
    validate_ok: bool
    validate_output: str = ""
    summary: str
    assumptions: list[str] = []


class ReviewPayload(BaseModel):
    """Reviewer -> Orchestrator verdict payload."""
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["approve", "request_changes"]
    policy_failures: list[str] = []
    findings: list[dict] = []
