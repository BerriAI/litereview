"""Evidence-based reassessment of findings carried across review requests."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class FindingAssessment(BaseModel):
    id: str
    status: Literal["open", "fixed", "uncertain"]
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def fixed_requires_evidence(self):
        if self.status == "fixed" and not any(item.strip() for item in self.evidence):
            raise ValueError("A fixed finding needs evidence from the current checkout.")
        return self
