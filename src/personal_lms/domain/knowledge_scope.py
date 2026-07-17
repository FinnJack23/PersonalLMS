from __future__ import annotations

from pydantic import Field

from personal_lms.domain.base import StrictModel


class KnowledgeScope(StrictModel):
    """Optional metadata tagging a request, evidence item, or response to one
    or more knowledge-pack dimensions.

    Every field is optional and there is no required domain-specific field
    anywhere on this schema — see the platform's domain-neutrality
    requirement in ``docs/product-specs/RAG_KNOWLEDGE_PLANE.md`` ("Optional
    domain mappings... Composable filtering"). CCNA, A+, or any future
    knowledge pack is expressed entirely through these optional values,
    never through a dedicated field or subclass.
    """

    knowledge_domain: str | None = Field(
        default=None, min_length=1, description="e.g. 'networking', 'cloud'."
    )
    certification: str | None = Field(default=None, min_length=1, description="e.g. 'CCNA'.")
    course: str | None = Field(default=None, min_length=1)
    topic: str | None = Field(default=None, min_length=1)
    objective_framework: str | None = Field(
        default=None,
        min_length=1,
        description="e.g. an exam objective code such as '1.1.a'.",
    )
