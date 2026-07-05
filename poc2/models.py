"""Domain models for the PoC.

Kept intentionally lean. The full design (§9) has a richer per-field provenance
envelope; here we track `source` at the record level only, enough to prove the
flow. Grow this toward the full provenance model when Gate 1 (HITL) lands.
"""
from __future__ import annotations

import typing
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class SchemaModel(BaseModel):
    """Base model that tolerates LLM structured output returning `null` for a
    list field. Pydantic's default_factory only fires when a key is ABSENT; an
    explicit `null` would raise. Models coerce null -> [] for list-typed fields.
    """

    @model_validator(mode="before")
    @classmethod
    def _coerce_none_lists(cls, data):
        if isinstance(data, dict):
            for name, field in cls.model_fields.items():
                if data.get(name) is None and typing.get_origin(field.annotation) in (list, List):
                    data[name] = []
        return data


class Source(str, Enum):
    CRM = "crm"
    ENRICHMENT = "enrichment"
    IDENTIFICATION = "identification"
    RESEARCH = "research"
    VERIFIED = "verified"


# ---- Inputs (mock CRM shape, §11.1) -------------------------------------

class Contact(SchemaModel):
    contact_id: str
    first_name: str
    last_name: str
    title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    source: Source = Source.CRM
    # Set by Contact Identification (1 = highest outreach priority). None for
    # CRM-provided contacts. A future reconciliation strategy can select on it.
    priority: Optional[int] = None
    identification_rationale: Optional[str] = None
    # hris_committee strategy extras: buying-committee role key (see
    # hris_contact_prioritizer.ALL_ROLE_KEYS) and personalization anchors
    # ({type, quality, source}) the generators can personalize from.
    role: Optional[str] = None
    anchors: List[dict] = Field(default_factory=list)


class Account(SchemaModel):
    account_id: str
    bdr_id: str
    name: str
    domain: Optional[str] = None
    industry: Optional[str] = None
    size_band: Optional[str] = None
    hq_region: Optional[str] = None
    contacts: List[Contact] = Field(default_factory=list)
    # Optional caller-attached fetched pages (str or {"url","text"}) for the
    # hris_committee identification strategy; wins over Exa/fixture fetching.
    # Excluded from prompt serializations alongside `contacts` (see PROMPT_EXCLUDE).
    page_texts: List[typing.Union[str, dict]] = Field(default_factory=list)


# Fields never serialized into LLM prompts (bulk data, not account facts).
PROMPT_EXCLUDE = {"contacts", "page_texts"}


# ---- Agent outputs (structured) -----------------------------------------

class AccountValidation(SchemaModel):
    """Haiku · Account Validation (§7 #2)."""
    status: str = Field(description="VALID or NEEDS_ENRICHMENT")
    missing_fields: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)


class ContactIdentification(SchemaModel):
    """Contact Identification output (§4.4, §7 #placeholder sub-workflow).

    A prioritized list of candidate contacts found for the Account's company.
    `contacts` carry source=identification and a `priority` (1 = highest).
    The hris_committee strategy also fills the Contact Access Score fields;
    the placeholder strategy leaves them None/empty.
    """
    account_id: str
    strategy: str
    contacts: List["Contact"] = Field(default_factory=list)
    access_score: Optional[int] = None
    grade: Optional[str] = None
    size_bucket: Optional[str] = None
    email_pattern: Optional[dict] = None
    incumbent_signals: List[str] = Field(default_factory=list)
    trigger_events: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ---- Contact enrichment (agentic half of the hris_committee strategy) ----

class Anchor(SchemaModel):
    """Personalization anchor. `quality` is the agent's judgment call."""
    type: str = Field(description="short lowercase token, e.g. recent_post, promotion")
    quality: str = Field(description="strong | moderate | weak")
    source: Optional[str] = Field(default=None, description="URL/page the anchor came from")


class EnrichmentContact(SchemaModel):
    """One contact's judgment fields, merged onto heuristic extraction by
    hris_contact_prioritizer (agent-supplied values win)."""
    name: str
    title: Optional[str] = None
    role: Optional[str] = Field(default=None, description="controlled role key, omit if unsure")
    linkedin_found: bool = False
    email_direct: Optional[str] = Field(default=None, description="address seen on a fetched page only")
    phone: Optional[str] = Field(default=None, description="published on a fetched page only")
    personalization_anchors: List[Anchor] = Field(default_factory=list)


class Enrichment(SchemaModel):
    """Sonnet · Contact Enrichment judgment (adapted from AGENT-PROMPT-contacts.md).
    Everything must trace to the supplied page texts — never fabricated."""
    contacts: List[EnrichmentContact] = Field(default_factory=list)
    trigger_events: List[str] = Field(default_factory=list)
    warm_paths: List[str] = Field(default_factory=list)
    incumbent_signals: List[str] = Field(default_factory=list)


class ContactVerification(SchemaModel):
    """Haiku · Contact Verification (§7 #3)."""
    contact_id: str
    status: str = Field(description="VERIFIED or INSUFFICIENT")
    reason: str


class ResearchFinding(SchemaModel):
    kind: str = Field(description="e.g. funding, exec_hire, product_launch, tech_stack")
    summary: str
    origin: str = Field(description="tool name or URL the finding came from")


class ResearchResult(SchemaModel):
    """Sonnet · Research Agent (§7 #6)."""
    company_facts: List[str]
    trigger_events: List[ResearchFinding]


class AccountSummary(SchemaModel):
    """Sonnet · Account Summarizer (§7 #7). Exactly 5 bullets per §3."""
    bullets: List[str]


# ---- Terminal artifacts -------------------------------------------------

class Artifact(SchemaModel):
    artifact_type: str  # account_summary | email | linkedin | talk_track
    account_id: str
    contact_id: Optional[str] = None
    content: str
    model_tier: str
    source: Source = Source.RESEARCH
