"""JSON schemas (as Pydantic models) for every AI stage (Structured Outputs).

All fields are required and without defaults so they are valid in OpenAI strict
JSON Schema mode.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.document import BlockType, DocumentSection

# --------------------------------------------------------------------------- A. Extraction

ItemKind = Literal[
    "concept",
    "definition",
    "explanation",
    "rule",
    "condition",
    "procedure_step",
    "example",
    "warning",
    "mistake",
    "exception",
    "recommendation",
    "opinion",
    "hypothesis",
    "data",
    "important_point",
    "other",
]


class ExtractedItem(BaseModel):
    kind: ItemKind
    certainty: Literal["stated", "tentative"] = Field(
        description="'tentative' when the speaker expresses doubt, belief or hypothesis (creo, quizá, a veces...)."
    )
    text: str = Field(description="Faithful paraphrase of what the source says. Never add external knowledge.")
    tags: list[str] = Field(description="Short topic tags or, in trading mode, trading categories.")
    source_start: float = Field(description="Start second, copied from a [seconds] marker of the source.")
    source_end: float = Field(description="End second, copied from a [seconds] marker of the source (end of the idea).")


class ExtractedProcedure(BaseModel):
    name: str
    steps: list[str] = Field(description="Ordered steps exactly as described in the source.")
    source_start: float
    source_end: float


class TopicMention(BaseModel):
    name: str
    summary: str
    source_start: float
    source_end: float


class ChunkExtraction(BaseModel):
    topics: list[TopicMention]
    items: list[ExtractedItem]
    procedures: list[ExtractedProcedure]


# --------------------------------------------------------------------------- B. Outline


class OutlineSubsection(BaseModel):
    title: str
    item_ids: list[str]


class OutlineChapter(BaseModel):
    title: str
    purpose: str = Field(description="One sentence: what this chapter covers (internal, not shown).")
    item_ids: list[str]
    subsections: list[OutlineSubsection]


class OutlineKeyPoint(BaseModel):
    text: str
    item_ids: list[str]


class Outline(BaseModel):
    title: str
    summary: str
    chapters: list[OutlineChapter]
    key_points: list[OutlineKeyPoint]
    omitted_item_ids: list[str] = Field(
        description="Items deliberately left out because they have no formative value (greetings, promotion...)."
    )


# --------------------------------------------------------------------------- C. Writer


class WrittenChapter(BaseModel):
    section: DocumentSection


# --------------------------------------------------------------------------- D. Verification

IssueType = Literal[
    "unsupported_claim",
    "possible_invention",
    "duplicate",
    "contradiction",
    "wrong_timestamp",
    "redundant_section",
]


class VerificationIssue(BaseModel):
    type: IssueType
    description: str
    action_taken: str


class VerifiedChapter(BaseModel):
    issues: list[VerificationIssue]
    section: DocumentSection


# --------------------------------------------------------------------------- Clean transcript mode


class CleanParagraph(BaseModel):
    text: str
    source_start: float
    source_end: float


class CleanSection(BaseModel):
    heading: str = Field(description="Short heading when the topic changes; empty string to continue the previous one.")
    paragraphs: list[CleanParagraph]


class CleanChunk(BaseModel):
    sections: list[CleanSection]


# --------------------------------------------------------------------------- Consolidated (multi-video) documents
# The model never writes timestamps or video ids here: it cites evidence ids, and code resolves each id to
# (youtube_id, start, end). That is what guarantees the provenance of every block.

ConceptRelation = Literal["agreement", "repetition", "complementary"]


class ConceptGroup(BaseModel):
    statement: str = Field(description="Faithful formulation of the shared idea, using only the grouped items.")
    item_ids: list[str]
    relation: ConceptRelation = Field(
        description="agreement: same point in 2+ different videos. repetition: repeated within one video. "
        "complementary: same topic, different compatible details."
    )


class SourcePosition(BaseModel):
    source: str = Field(description="Source reference, e.g. 'V2'.")
    statement: str = Field(description="What THIS source says on the topic (faithful paraphrase of its items only).")
    item_ids: list[str] = Field(description="Ids of items from this source only.")


class SourceDifference(BaseModel):
    topic: str
    positions: list[SourcePosition]


class CollectionPlan(BaseModel):
    title: str
    summary: str
    concept_groups: list[ConceptGroup]
    differences: list[SourceDifference]
    chapters: list[OutlineChapter]
    key_points: list[OutlineKeyPoint]
    omitted_item_ids: list[str] = Field(description="Items with no formative value (greetings, promotion...).")


class CitedBlock(BaseModel):
    type: BlockType = Field(
        description=(
            "paragraph: text. bullet_list/numbered_list/checklist: items. table: table_headers + table_rows. "
            "quote: highlighted statement. note: remark such as an explicit absence of information."
        )
    )
    text: str = Field(description="Text for paragraph, quote and note blocks. Empty string otherwise.")
    items: list[str] = Field(description="Items for list/checklist blocks. Empty list otherwise.")
    table_headers: list[str] = Field(description="Column headers for table blocks. Empty otherwise.")
    table_rows: list[list[str]] = Field(description="Rows for table blocks. Empty otherwise.")
    evidence_ids: list[str] = Field(description="Ids of ALL the evidence items that support this block (required).")


class CitedSubsection(BaseModel):
    title: str
    blocks: list[CitedBlock]


class CitedSection(BaseModel):
    title: str
    blocks: list[CitedBlock]
    subsections: list[CitedSubsection]


class WrittenCollectionChapter(BaseModel):
    section: CitedSection


CollectionIssueType = Literal[
    "unsupported_claim",
    "possible_invention",
    "wrong_attribution",
    "mixed_sources",
    "missing_citation",
    "duplicate",
    "redundant_section",
]


class CollectionVerificationIssue(BaseModel):
    type: CollectionIssueType
    description: str
    action_taken: str


class VerifiedCollectionChapter(BaseModel):
    issues: list[CollectionVerificationIssue]
    section: CitedSection
