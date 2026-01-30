from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

_FORBIDDEN_TEXT_PATTERNS = [
    r"\bpage\s*\d+\b",
    r"\bsource\b",
    r"\b引用\b",
    r"\b参考\b",
    r"\b出处\b",
    r"\b来源\b",
    r"\b页码\b",
    r"第\s*\d+\s*页",
    r"\[\d+\]",
]

_FORBIDDEN_RE = re.compile("|".join(_FORBIDDEN_TEXT_PATTERNS), re.IGNORECASE)


class Section(BaseModel):
    key: str = Field(..., description="Section placeholder key, e.g. SECTION_1_INTRO")
    text: str = Field(..., description="Formal section text")

    @field_validator("key")
    @classmethod
    def _validate_key(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("section key is empty")
        return v

    @field_validator("text")
    @classmethod
    def _validate_text(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("section text is empty")
        if _FORBIDDEN_RE.search(v):
            raise ValueError("section text contains evidence/citation markers")
        return v


class Evidence(BaseModel):
    source: str = Field(..., description="Source file name")
    page: int = Field(..., ge=1, description="Source page number (1-based)")
    excerpt: str = Field(..., description="Evidence excerpt")

    @field_validator("source", "excerpt")
    @classmethod
    def _strip_text(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("evidence field is empty")
        return v


class EvidenceItem(BaseModel):
    claim: str = Field(..., description="Claim text")
    evidence: List[Evidence] = Field(default_factory=list)

    @field_validator("claim")
    @classmethod
    def _strip_claim(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("claim is empty")
        return v


class EvidenceNote(BaseModel):
    topic: str = Field(..., description="Evidence topic")
    items: List[EvidenceItem] = Field(default_factory=list)

    @field_validator("topic")
    @classmethod
    def _strip_topic(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("topic is empty")
        return v


class DocGenPayload(BaseModel):
    title: str = Field(default="", description="Document title")
    doc_no: str = Field(default="", description="Document number")
    sections: List[Section] = Field(default_factory=list)
    evidence_notes: List[EvidenceNote] = Field(default_factory=list)

    @field_validator("title", "doc_no")
    @classmethod
    def _strip_simple(cls, v: str) -> str:
        return (v or "").strip()


def validate_docgen_payload(data: Dict[str, Any]) -> DocGenPayload:
    return DocGenPayload.model_validate(data)


def docgen_payload_schema() -> Dict[str, Any]:
    return DocGenPayload.model_json_schema()
