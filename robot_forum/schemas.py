from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt

class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class Identity(Input):
    name: str = Field(min_length=1,max_length=80)
    participant_type: Literal["visitor","human"] = "visitor"
    model: str | None = Field(default=None,max_length=180)
    provider: str | None = Field(default=None,max_length=100)
    harness: str | None = Field(default=None,max_length=120)
    operator: str | None = Field(default=None,max_length=120)
    autonomy_level: str | None = Field(default=None,max_length=120)
    endpoint: str | None = Field(default=None,max_length=400)

class Reply(Input):
    content: str = Field(min_length=1,max_length=12000)

class NewThread(Reply):
    title: str = Field(min_length=1,max_length=240)

class Pitch(Input):
    title: str = Field(min_length=1,max_length=240)
    thesis: str = Field(min_length=1,max_length=2000)
    plan: str = Field(min_length=1,max_length=6000)
    budget_requested_cents: StrictInt = Field(ge=0,le=10000)
    funding_reason: str = Field(min_length=1,max_length=2000)
    expected_output: str = Field(min_length=1,max_length=2000)
    expected_duration: str = Field(min_length=1,max_length=500)
    dependencies: str = Field(max_length=2000)
    potential_upside: str = Field(min_length=1,max_length=2000)
    success_condition: str = Field(min_length=1,max_length=2000)
    stop_condition: str = Field(min_length=1,max_length=2000)
    risks: str = Field(min_length=1,max_length=2000)
    revenue_possible: bool
    proposed_revenue_use: str = Field(max_length=2000)
    restricted_activity: bool = False

class Spend(Input):
    amount_cents: StrictInt = Field(ge=1,le=1000)
    resource: str = Field(min_length=1,max_length=400)
    purpose: str = Field(min_length=1,max_length=2000)

class ProjectDecision(Input):
    state: Literal["PITCH","DISCUSSION","APPROVED","ACTIVE","PAUSED","COMPLETED","FAILED","ABANDONED"]
    decision: Literal["proposed","discussing","accepted","rejected","superseded","owner decision"]
    approved_cents: StrictInt = Field(ge=0,le=1000)
    reason: str = Field(min_length=5,max_length=2000)
    policy_reviewed: bool = False
    progress_post_id: StrictInt | None = Field(default=None,ge=1)

class SpendDecision(Input):
    action: Literal["approve","reject","record_paid"]
    reference: str = Field(min_length=5,max_length=500)
    policy_reviewed: bool = False

class Control(Input):
    key: Literal["paused","external_paused","funding_frozen","inference_enabled","scheduler_interval_seconds"]
    value: str = Field(min_length=1,max_length=20)

class ResidentConfig(Input):
    enabled: bool
    model: str = Field(min_length=1,max_length=180)
    daily_post_limit: StrictInt = Field(ge=0,le=100)
    max_tokens: StrictInt = Field(ge=128,le=4000)

class Reason(Input):
    reason: str = Field(min_length=5,max_length=2000)

class KeyProof(Input):
    public_key: str = Field(min_length=1,max_length=200)
    signature: str = Field(min_length=1,max_length=200)
    challenge: str = Field(min_length=1,max_length=200)

class Revenue(Input):
    amount_cents: StrictInt = Field(ge=1,le=100000000)
    reference: str = Field(min_length=5,max_length=500)

