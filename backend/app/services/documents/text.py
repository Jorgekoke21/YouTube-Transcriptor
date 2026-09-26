"""Plain-text renderer (the processed document, not the raw transcript)."""

from __future__ import annotations

import textwrap
from collections.abc import Sequence

from app.models.document import AnyDocument, ContentBlock, SourceRange
from app.services.documents.common import labels, metadata_lines, range_label, source_lines, strip_markdown, time_links

WIDTH = 100


def _wrap(text: str, indent: str = "", subsequent: str | None = None) -> list[str]:
    return textwrap.wrap(
        strip_markdown(" ".join(text.split())),
        width=WIDTH,
        initial_indent=indent,
        subsequent_indent=subsequent if subsequent is not None else indent,
    ) or [indent.rstrip()]


def _ranges(doc: AnyDocument, ranges: Sequence[SourceRange]) -> str:
    return ", ".join(t.label for t in time_links(doc, ranges))


def _block(block: ContentBlock, doc: AnyDocument) -> list[str]:
    lines: list[str] = []
    if block.type == "paragraph":
        lines += _wrap(block.text)
    elif block.type == "quote":
        lines += _wrap(f"“{block.text.strip()}”", "    ")
    elif block.type == "note":
        lines += _wrap(f"{labels(doc.output_language)['note']}: {block.text}", "  ! ", "    ")
    elif block.type == "bullet_list":
        for item in block.items:
            lines += _wrap(item, "  - ", "    ")
    elif block.type == "numbered_list":
        for n, item in enumerate(block.items, 1):
            prefix = f"  {n}. "
            lines += _wrap(item, prefix, " " * len(prefix))
    elif block.type == "checklist":
        for item in block.items:
            lines += _wrap(item, "  [ ] ", "      ")
    elif block.type == "table":
        if block.table_headers:
            lines.append("  " + " | ".join(strip_markdown(h) for h in block.table_headers))
            lines.append("  " + "-" * min(WIDTH - 2, max(10, len(lines[-1]))))
        for row in block.table_rows:
            lines.append("  " + " | ".join(strip_markdown(c) for c in row))
    if block.source_ranges:
        lines.append(f"  [{_ranges(doc, block.source_ranges)}]")
    lines.append("")
    return lines


def render_text(doc: AnyDocument) -> str:
    lab = labels(doc.output_language)
    out = [doc.title.upper(), "=" * min(WIDTH, max(len(doc.title), 10)), ""]
    out += [f"{m.label}: {m.value}" for m in metadata_lines(doc)]
    out.append("")
    sources = source_lines(doc)
    if sources:
        out.append(f"{lab['sources_used']}:")
        for src in sources:
            channel = f" — {src.channel}" if src.channel else ""
            out.append(f"  {src.number}. {src.title}{channel} — {src.url}")
        out.append("")

    if doc.summary.strip():
        out += [lab["summary"].upper(), "-" * len(lab["summary"])]
        out += _wrap(doc.summary) + [""]

    for section in doc.sections:
        if section.title.strip():
            header = section.title.strip().upper()
            out += ["", header, "-" * min(WIDTH, len(header))]
        if section.source_ranges:
            out.append(f"{range_label(doc)}: {_ranges(doc, section.source_ranges)}")
            out.append("")
        for block in section.blocks:
            out += _block(block, doc)
        for sub in section.subsections:
            out.append(f"» {sub.title.strip()}")
            if sub.source_ranges:
                out.append(f"  {range_label(doc)}: {_ranges(doc, sub.source_ranges)}")
            out.append("")
            for block in sub.blocks:
                out += _block(block, doc)

    if doc.key_points:
        out += ["", lab["key_points"].upper(), "-" * len(lab["key_points"])]
        for kp in doc.key_points:
            out += _wrap(kp.text, "  * ", "    ")
    return "\n".join(out).rstrip() + "\n"
