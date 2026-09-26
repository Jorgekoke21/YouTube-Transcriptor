"""Consolidated (multi-video) document pipeline.

A collection is an UNORDERED SET of videos: the order in which the URLs were pasted has no meaning. Sources are
put in a canonical order (sorted by youtube_id) and named V1, V2… in that order, so the same set of videos always
yields the same evidence ids; the model decides the thematic order of the document.

Per-video extractions (stage A, usually from the cache) → evidence pool namespaced per video (`V2.c0-i3`) →
(B′) consolidation plan: equivalent concepts, agreements, differences between sources, thematic outline →
(C′) writer per chapter → (D′) verification per chapter against the evidence and the original transcripts →
deterministic provenance resolution.

Full transcripts are never sent again: B′ and C′ only see the extracted evidence, and D′ only sees the
transcript lines around the cited evidence (like the single-video verification).

Provenance is enforced by code, not by the model: the writer cites evidence ids, and every id is resolved to
(youtube_id, start, end) from the evidence pool. Ids the model invents, or ids outside a chapter's evidence,
are discarded; a content block without valid evidence is dropped (fidelity > completeness).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.core.errors import AppError, ErrorCode
from app.models.document import (
    Citation,
    ContentBlock,
    DocumentSection,
    DocumentSubsection,
    DocumentType,
    KeyPoint,
    SourceRange,
)
from app.models.usage import AIUsage
from app.prompts import MODE_PROMPTS, get_prompt_versions
from app.prompts.collection import (
    COLLECTION_PROMPT_VERSION,
    COLLECTION_VERIFICATION_PROMPT,
    COLLECTION_WRITER_PROMPT,
    CONSOLIDATION_PROMPT,
    MULTI_SOURCE_RULES,
)
from app.services.ai.base import CollectionAIResult, ProgressCallback, VideoEvidence
from app.services.ai.evidence import EvidenceItem
from app.services.ai.pipeline import DocumentPipeline, _dump, strip_inline_seconds
from app.services.ai.schemas import (
    CitedBlock,
    CitedSection,
    CitedSubsection,
    CollectionPlan,
    ConceptGroup,
    OutlineChapter,
    OutlineKeyPoint,
    SourceDifference,
    SourcePosition,
    VerifiedCollectionChapter,
    WrittenCollectionChapter,
)
from app.services.ai.timestamps import TimestampIndex, merge_ranges
from app.utils.timecode import format_timestamp
from app.utils.youtube_url import timestamp_url

logger = logging.getLogger(__name__)

SECTION_GAP = 60.0
BLOCK_GAP = 30.0

SECTION_TITLES = {
    "es": {"agreements": "Coincidencias entre fuentes", "differences": "Diferencias entre enfoques", "video": "Vídeo"},
    "en": {
        "agreements": "Points of agreement between sources",
        "differences": "Differences between approaches",
        "video": "Video",
    },
}


# ---------------------------------------------------------------------- evidence pool


@dataclass
class EvidencePool:
    """All evidence of a collection, namespaced per source. Sources are in canonical order (by youtube_id)."""

    sources: list[VideoEvidence]
    refs: list[str]
    by_id: dict[str, EvidenceItem]
    indexes: dict[str, TimestampIndex]

    @classmethod
    def build(cls, sources: Sequence[VideoEvidence]) -> EvidencePool:
        # Set semantics: one entry per video, canonical order. V1/V2… never depend on the input order.
        unique = {s.video.video_id: s for s in sources}
        sources = [unique[vid] for vid in sorted(unique)]
        refs = [f"V{n}" for n in range(1, len(sources) + 1)]
        by_id: dict[str, EvidenceItem] = {}
        for ref, src in zip(refs, sources, strict=True):
            for item in sorted(src.evidence, key=lambda e: (e.start, e.id)):
                pooled = item.for_source(ref, src.video.video_id)
                by_id[pooled.id] = pooled
        indexes = {src.video.video_id: TimestampIndex(src.transcript.segments) for src in sources}
        return cls(sources=list(sources), refs=refs, by_id=by_id, indexes=indexes)

    def order(self, ref_or_video: str) -> int:
        for i, (ref, src) in enumerate(zip(self.refs, self.sources, strict=True)):
            if ref_or_video in (ref, src.video.video_id):
                return i
        return len(self.sources)

    def source(self, ref: str) -> VideoEvidence:
        return self.sources[self.order(ref)]

    def sort_key(self, item: EvidenceItem) -> tuple[int, float, str]:
        return (self.order(item.source), item.start, item.id)

    def display(self, ref: str, lang: str) -> str:
        """How the differences section names a source: its title (V1/V2 are internal, order-free refs)."""
        video = self.source(ref).video
        return video.title or f"{SECTION_TITLES.get(lang, SECTION_TITLES['es'])['video']} {video.video_id}"


def sourced_ranges(items: Sequence[EvidenceItem], pool: EvidencePool, max_gap: float) -> list[SourceRange]:
    """Merge evidence ranges per video (never across videos), videos in canonical order, each range tagged with its video."""
    by_video: dict[str, list[SourceRange]] = {}
    for item in sorted(items, key=pool.sort_key):
        by_video.setdefault(item.video_id, []).append(SourceRange(start=item.start, end=item.end))
    ranges: list[SourceRange] = []
    for video_id, rngs in by_video.items():
        ranges += [SourceRange(start=r.start, end=r.end, video_id=video_id) for r in merge_ranges(rngs, max_gap=max_gap)]
    return ranges


# ---------------------------------------------------------------------- plan normalisation (pure, unit tested)


def _known(ids: Sequence[str], pool: EvidencePool, exclude: set[str]) -> list[str]:
    return [i for i in dict.fromkeys(ids) if i in pool.by_id and i not in exclude]


def normalize_plan(plan: CollectionPlan, pool: EvidencePool) -> CollectionPlan:
    """Make the model's plan safe to use.

    - differences: each position keeps only items of ITS source; a difference needs 2+ distinct sources.
      Items in a difference are presented only in the differences section (never fused in a chapter).
    - concept groups: unknown/conflicting ids dropped; `agreement` requires 2+ distinct videos (checked here).
    - chapters: unknown ids dropped, each id used once, items of a concept group kept in one chapter,
      forgotten items re-attached (nothing is silently lost).
    """
    by_id = pool.by_id

    differences: list[SourceDifference] = []
    conflict_ids: set[str] = set()
    for diff in plan.differences:
        merged: dict[str, SourcePosition] = {}
        for pos in diff.positions:
            ref = pos.source.strip().upper()
            ids = [i for i in _known(pos.item_ids, pool, conflict_ids) if by_id[i].source == ref]
            statement = pos.statement.strip()
            if not ids or not statement:
                continue
            if ref in merged:
                merged[ref].item_ids += [i for i in ids if i not in merged[ref].item_ids]
                merged[ref].statement += " " + statement
            else:
                merged[ref] = SourcePosition(source=ref, statement=statement, item_ids=ids)
        if len(merged) >= 2 and diff.topic.strip():
            positions = sorted(merged.values(), key=lambda p: pool.order(p.source))
            differences.append(SourceDifference(topic=diff.topic.strip(), positions=positions))
            conflict_ids |= {i for p in positions for i in p.item_ids}

    groups: list[ConceptGroup] = []
    grouped: set[str] = set()
    for g in plan.concept_groups:
        ids = _known(g.item_ids, pool, conflict_ids | grouped)
        if len(ids) < 2 or not g.statement.strip():
            continue
        n_sources = len({by_id[i].source for i in ids})
        relation = g.relation
        if n_sources >= 2 and relation == "repetition":
            relation = "agreement"
        elif n_sources < 2 and relation == "agreement":
            relation = "repetition"
        groups.append(ConceptGroup(statement=g.statement.strip(), item_ids=ids, relation=relation))
        grouped |= set(ids)

    used: set[str] = set()
    chapters: list[OutlineChapter] = []
    for ch in plan.chapters:
        ch.item_ids = _known(ch.item_ids, pool, conflict_ids | used)
        used |= set(ch.item_ids)
        for sub in ch.subsections:
            sub.item_ids = _known(sub.item_ids, pool, conflict_ids | used)
            used |= set(sub.item_ids)
        chapters.append(ch)

    # Keep each concept group in a single place (the location of its first assigned item).
    for g in groups:
        lists = _id_lists(chapters)
        anchor = next((lst for i in g.item_ids for lst in lists if i in lst), None)
        if anchor is None:
            continue
        for i in g.item_ids:
            for lst in lists:
                if lst is not anchor and i in lst:
                    lst.remove(i)
            if i not in anchor:
                anchor.append(i)
            used.add(i)
    chapters = _drop_empty(chapters)

    omitted = {i for i in plan.omitted_item_ids if i in by_id} - used - conflict_ids
    orphans = [by_id[i] for i in by_id if i not in used and i not in omitted and i not in conflict_ids]
    if orphans and not chapters:
        chapters.append(OutlineChapter(title=plan.title.strip() or "Contenido", purpose="", item_ids=[], subsections=[]))
    group_of = {i: g for g in groups for i in g.item_ids}
    for item in sorted(orphans, key=pool.sort_key):
        target = _orphan_target(item, chapters, group_of, pool)
        target.item_ids.append(item.id)
        logger.info("Collection plan: orphan item %s attached to chapter '%s'", item.id, target.title)

    key_points = []
    for kp in plan.key_points:
        ids = _known(kp.item_ids, pool, conflict_ids)
        if kp.text.strip() and ids:
            key_points.append(OutlineKeyPoint(text=kp.text.strip(), item_ids=ids))

    return CollectionPlan(
        title=plan.title.strip(),
        summary=plan.summary.strip(),
        concept_groups=groups,
        differences=differences,
        chapters=chapters,
        key_points=key_points,
        omitted_item_ids=sorted(omitted),
    )


def _id_lists(chapters: list[OutlineChapter]) -> list[list[str]]:
    return [lst for ch in chapters for lst in [ch.item_ids, *(s.item_ids for s in ch.subsections)]]


def _drop_empty(chapters: list[OutlineChapter]) -> list[OutlineChapter]:
    kept = []
    for ch in chapters:
        ch.subsections = [s for s in ch.subsections if s.item_ids]
        if ch.item_ids or ch.subsections:
            kept.append(ch)
    return kept


def chapter_item_ids(chapter: OutlineChapter) -> list[str]:
    return list(dict.fromkeys(list(chapter.item_ids) + [i for s in chapter.subsections for i in s.item_ids]))


def _orphan_target(
    item: EvidenceItem, chapters: list[OutlineChapter], group_of: dict[str, ConceptGroup], pool: EvidencePool
) -> OutlineChapter:
    """Chapter of the item's concept group, else the chapter with the closest item of the SAME video."""
    group = group_of.get(item.id)
    if group:
        for ch in chapters:
            if set(chapter_item_ids(ch)) & set(group.item_ids):
                return ch

    def distance(ch: OutlineChapter) -> float:
        same = [pool.by_id[i] for i in chapter_item_ids(ch) if pool.by_id[i].video_id == item.video_id]
        return min((abs(e.start - item.start) for e in same), default=float("inf"))

    best = min(chapters, key=distance)
    return best if distance(best) != float("inf") else chapters[0]


# ---------------------------------------------------------------------- provenance resolution (pure, unit tested)

_SOURCE_REF_RE = re.compile(r"\s*[\(\[]\s*V\d+(?:\s*(?:,|y|and|/|&)\s*V\d+)*\s*[\)\]]")


def strip_source_refs(text: str) -> str:
    """Internal refs such as "(V1, V2)" never reach the reader: links are generated from the citations."""
    return _SOURCE_REF_RE.sub("", strip_inline_seconds(text)).strip()


@dataclass
class Resolution:
    cited: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)


def resolve_blocks(
    blocks: Sequence[CitedBlock], allowed: dict[str, EvidenceItem], pool: EvidencePool, res: Resolution, where: str
) -> tuple[list[ContentBlock], list[EvidenceItem]]:
    """Turn cited blocks into content blocks whose ranges come only from valid evidence ids."""
    out: list[ContentBlock] = []
    cited: dict[str, EvidenceItem] = {}
    for b in blocks:
        text = strip_source_refs(b.text)
        items = [t for t in (strip_source_refs(i) for i in b.items if i and i.strip()) if t]
        rows = [[strip_source_refs(c) for c in r] for r in b.table_rows if any(c.strip() for c in r)]
        if b.type in ("paragraph", "quote", "note") and not text:
            continue
        if b.type in ("bullet_list", "numbered_list", "checklist") and not items:
            continue
        if b.type == "table" and not rows:
            continue
        ids = [i for i in dict.fromkeys(b.evidence_ids) if i in allowed]
        if not ids and b.type != "note":
            res.notes.append(f"[{where}] missing_citation: bloque sin evidencias válidas → eliminado")
            continue
        res.cited |= set(ids)
        cited |= {i: allowed[i] for i in ids}
        out.append(
            ContentBlock(
                type=b.type,
                text=text,
                items=items,
                table_headers=list(b.table_headers),
                table_rows=rows,
                source_ranges=sourced_ranges([allowed[i] for i in ids], pool, BLOCK_GAP),
            )
        )
    return out, list(cited.values())


def resolve_section(
    cited: CitedSection, allowed: dict[str, EvidenceItem], pool: EvidencePool, res: Resolution, fallback_title: str
) -> DocumentSection:
    """Section ranges cover all of its evidence (per video); subsection ranges cover what their blocks cite."""
    title = strip_source_refs(cited.title) or fallback_title
    blocks, _ = resolve_blocks(cited.blocks, allowed, pool, res, title)
    section = DocumentSection(
        title=title,
        blocks=blocks,
        source_ranges=sourced_ranges(list(allowed.values()), pool, SECTION_GAP),
        subsections=[],
    )
    for sub in cited.subsections:
        sub_blocks, sub_items = resolve_blocks(sub.blocks, allowed, pool, res, title)
        if not sub_blocks:
            continue
        sub_title = strip_source_refs(sub.title)
        if not sub_title:
            (section.subsections[-1].blocks if section.subsections else section.blocks).extend(sub_blocks)
            continue
        section.subsections.append(
            DocumentSubsection(title=sub_title, blocks=sub_blocks, source_ranges=sourced_ranges(sub_items, pool, SECTION_GAP))
        )
    return section


def build_key_points(plan: CollectionPlan, pool: EvidencePool, res: Resolution) -> list[KeyPoint]:
    points = []
    for kp in plan.key_points:
        items = [pool.by_id[i] for i in kp.item_ids if i in pool.by_id]
        if not kp.text.strip() or not items:
            continue
        res.cited |= {e.id for e in items}
        points.append(KeyPoint(text=strip_source_refs(kp.text), source_ranges=sourced_ranges(items, pool, SECTION_GAP)))
    return points


def build_citations(cited: set[str], pool: EvidencePool) -> list[Citation]:
    citations = []
    for item in sorted((pool.by_id[i] for i in cited if i in pool.by_id), key=pool.sort_key):
        citations.append(
            Citation(
                evidence_id=item.id,
                youtube_id=item.video_id,
                video_title=pool.source(item.source).video.title,
                source_start=item.start,
                source_end=item.end,
                source_url=timestamp_url(item.video_id, item.start),
                text=item.text,
            )
        )
    return citations


# ---------------------------------------------------------------------- deterministic sections


def agreements_section(plan: CollectionPlan, lang: str) -> CitedSection | None:
    agreements = [g for g in plan.concept_groups if g.relation == "agreement"]
    if not agreements:
        return None
    blocks = [
        CitedBlock(type="paragraph", text=g.statement, items=[], table_headers=[], table_rows=[], evidence_ids=g.item_ids)
        for g in agreements
    ]
    title = SECTION_TITLES.get(lang, SECTION_TITLES["es"])["agreements"]
    return CitedSection(title=title, blocks=blocks, subsections=[])


def differences_section(plan: CollectionPlan, pool: EvidencePool, lang: str) -> CitedSection | None:
    """One subsection per difference, one paragraph per source. Built by code: sources are never fused."""
    if not plan.differences:
        return None
    subsections = [
        CitedSubsection(
            title=diff.topic,
            blocks=[
                CitedBlock(
                    type="paragraph",
                    text=f"**{pool.display(p.source, lang)}:** {p.statement}",
                    items=[],
                    table_headers=[],
                    table_rows=[],
                    evidence_ids=p.item_ids,
                )
                for p in diff.positions
            ],
        )
        for diff in plan.differences
    ]
    title = SECTION_TITLES.get(lang, SECTION_TITLES["es"])["differences"]
    return CitedSection(title=title, blocks=[], subsections=subsections)


def _block_sources(block: CitedBlock, pool: EvidencePool) -> set[str]:
    return {pool.by_id[i].source for i in block.evidence_ids if i in pool.by_id}


def keeps_attribution(original: CitedSection, verified: CitedSection, pool: EvidencePool, kind: str) -> bool:
    """Structural invariants of the generated sections that verification must not break."""
    blocks = [b for s in verified.subsections for b in s.blocks] + list(verified.blocks)
    if kind == "differences":
        # every position still single-source, and every source of every difference still present
        if any(len(_block_sources(b, pool)) != 1 for b in blocks):
            return False
        before = {(s.title, src) for s in original.subsections for b in s.blocks for src in _block_sources(b, pool)}
        after = {(s.title, src) for s in verified.subsections for b in s.blocks for src in _block_sources(b, pool)}
        return before <= after
    if kind == "agreements":
        return all(len(_block_sources(b, pool)) >= 2 for b in blocks)
    return True


# ---------------------------------------------------------------------- pipeline


@dataclass
class _Part:
    """A section of the consolidated document before resolution."""

    kind: str  # "chapter" | "agreements" | "differences"
    title: str
    allowed: dict[str, EvidenceItem]
    cited: CitedSection | None = None


class CollectionPipeline(DocumentPipeline):
    def run_collection(
        self,
        sources: Sequence[VideoEvidence],
        document_type: DocumentType,
        output_language: str,
        on_progress: ProgressCallback | None = None,
        usage: AIUsage | None = None,
    ) -> CollectionAIResult:
        progress = on_progress or (lambda _data: None)
        usage = usage or AIUsage(model=self.llm.model)
        pool = EvidencePool.build(sources)
        if len(pool.sources) < 2:  # distinct videos
            raise AppError(ErrorCode.NOT_ENOUGH_VIDEOS)
        if not pool.by_id:
            raise AppError(ErrorCode.AI_PROCESSING_FAILED, "No se ha encontrado contenido formativo aprovechable en los vídeos.")

        progress({"stage": "consolidating"})
        plan = normalize_plan(self._plan(pool, document_type, output_language, usage), pool)

        parts = [_Part("chapter", ch.title, {i: pool.by_id[i] for i in chapter_item_ids(ch)}) for ch in plan.chapters]
        written = self._write_chapters(plan, pool, document_type, output_language, usage, progress)
        for part, chapter_section in zip(parts, written, strict=True):
            part.cited = chapter_section
        for kind, cited in (
            ("agreements", agreements_section(plan, output_language)),
            ("differences", differences_section(plan, pool, output_language)),
        ):
            if cited is not None:
                ids = [i for b in [*cited.blocks, *(b for s in cited.subsections for b in s.blocks)] for i in b.evidence_ids]
                parts.append(_Part(kind, cited.title, {i: pool.by_id[i] for i in ids}, cited))

        notes: list[str] = []
        if self.config.enable_verification:
            notes = self._verify_parts(parts, pool, document_type, output_language, usage, progress)

        res = Resolution()
        sections: list[DocumentSection] = []
        for part in parts:
            assert part.cited is not None
            section = resolve_section(part.cited, part.allowed, pool, res, part.title)
            if section.blocks or section.subsections:
                sections.append(section)
        key_points = build_key_points(plan, pool, res)
        prompt_versions = get_prompt_versions(document_type) | {"collection": COLLECTION_PROMPT_VERSION}
        return CollectionAIResult(
            title=plan.title or " · ".join(s.video.title or s.video.video_id for s in pool.sources[:2]),
            summary=plan.summary,
            sections=sections,
            key_points=key_points,
            citations=build_citations(res.cited, pool),
            usage=usage,
            prompt_versions=prompt_versions,
            model=self.llm.model,
            verification_notes=notes + res.notes,
        )

    def _collection_instructions(self, stage_prompt: str, document_type: DocumentType, lang: str, mode_text: str) -> str:
        return self._instructions(f"{MULTI_SOURCE_RULES}\n{stage_prompt}", document_type, lang, mode_text)

    # ------------------------------------------------------------------ B′. consolidation plan

    def _plan(self, pool: EvidencePool, document_type: DocumentType, lang: str, usage: AIUsage) -> CollectionPlan:
        mode = MODE_PROMPTS[document_type]
        instructions = self._collection_instructions(CONSOLIDATION_PROMPT, document_type, lang, mode.outline_guidance)
        lines = []
        for ref, src in zip(pool.refs, pool.sources, strict=True):
            kind = "automática" if src.transcript.is_generated else "manual"
            lines.append(
                f"{ref}: {src.video.title or src.video.video_id} — canal: {src.video.channel or '-'} — "
                f"duración: {format_timestamp(src.transcript.duration)} — transcripción {kind}"
            )
        items = sorted(pool.by_id.values(), key=pool.sort_key)
        text = (
            f"FUENTES ({len(pool.sources)}):\n" + "\n".join(lines) + "\n\n"
            f"IDEAS EXTRAÍDAS ({len(items)}), agrupadas por fuente y en orden temporal, una por línea:\n"
            + "\n".join(_dump(e.to_prompt()) for e in items)
        )
        return self.llm.parse(
            stage="consolidation", instructions=instructions, input_text=text, schema=CollectionPlan, usage=usage
        )

    # ------------------------------------------------------------------ C′. writer

    def _write_chapters(self, plan, pool, document_type, lang, usage, progress) -> list[CitedSection]:
        mode = MODE_PROMPTS[document_type]
        instructions = self._collection_instructions(COLLECTION_WRITER_PROMPT, document_type, lang, mode.writing_guidance)
        overview = [{"title": c.title, "purpose": c.purpose} for c in plan.chapters]
        sources = [
            {"source": ref, "title": s.video.title, "channel": s.video.channel}
            for ref, s in zip(pool.refs, pool.sources, strict=True)
        ]
        topics = [d.topic for d in plan.differences]
        total = len(plan.chapters)
        progress({"stage": "writing", "done": 0, "total": total})

        def write(chapter: OutlineChapter) -> CitedSection:
            ids = set(chapter_item_ids(chapter))
            items = sorted((pool.by_id[i] for i in ids), key=pool.sort_key)
            payload = {
                "document_title": plan.title,
                "sources": sources,
                "outline": overview,
                "chapter_to_write": {
                    "title": chapter.title,
                    "purpose": chapter.purpose,
                    "item_ids": chapter.item_ids,
                    "subsections": [{"title": s.title, "item_ids": s.item_ids} for s in chapter.subsections],
                },
                "concept_groups": [g.model_dump() for g in plan.concept_groups if ids & set(g.item_ids)],
                "differences_handled_elsewhere": topics,
                "evidence": [e.to_prompt() for e in items],
            }
            written = self.llm.parse(
                stage="collection_writer",
                instructions=instructions,
                input_text=_dump(payload),
                schema=WrittenCollectionChapter,
                usage=usage,
            )
            return written.section

        return self._map(write, plan.chapters, lambda d: progress({"stage": "writing", "done": d, "total": total}))

    # ------------------------------------------------------------------ D′. verification

    def _verify_parts(self, parts: list[_Part], pool, document_type, lang, usage, progress) -> list[str]:
        instructions = self._collection_instructions(COLLECTION_VERIFICATION_PROMPT, document_type, lang, "")
        total = len(parts)
        progress({"stage": "verifying", "done": 0, "total": total})

        def verify(part: _Part) -> list[str]:
            assert part.cited is not None
            items = sorted(part.allowed.values(), key=pool.sort_key)
            excerpts = {}
            for ref, src in zip(pool.refs, pool.sources, strict=True):
                ranges = [e.range for e in items if e.source == ref]
                if ranges:
                    excerpts[ref] = pool.indexes[src.video.video_id].excerpt(ranges)
            payload = {
                "other_chapters": [p.title for p in parts if p is not part],
                "section": part.cited.model_dump(),
                "evidence": [e.to_prompt() for e in items],
                "source_transcripts": excerpts,
            }
            result = self.llm.parse(
                stage="collection_verification",
                instructions=instructions,
                input_text=_dump(payload),
                schema=VerifiedCollectionChapter,
                usage=usage,
            )
            notes = [f"[{part.title}] {i.type}: {i.description} → {i.action_taken}" for i in result.issues]
            if keeps_attribution(part.cited, result.section, pool, part.kind):
                part.cited = result.section
            else:
                notes.append(
                    f"[{part.title}] la verificación alteraba la atribución por fuente → se conserva la versión anterior"
                )
            return notes

        results = self._map(verify, parts, lambda d: progress({"stage": "verifying", "done": d, "total": total}))
        return [n for ns in results for n in ns]
