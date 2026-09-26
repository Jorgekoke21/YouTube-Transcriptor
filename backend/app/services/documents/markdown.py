"""Markdown renderer (the master exportable format)."""

from __future__ import annotations

from collections.abc import Sequence

from app.models.document import AnyDocument, ContentBlock, SourceRange
from app.services.documents.common import labels, metadata_lines, range_label, source_lines, time_links


def _escape_cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").strip()


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _link_text(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


def _links(doc: AnyDocument, ranges: Sequence[SourceRange]) -> str:
    return " · ".join(f"[{_link_text(t.label)} ↗]({t.url})" for t in time_links(doc, ranges))


def _range_line(doc: AnyDocument, ranges: Sequence[SourceRange]) -> list[str]:
    if not ranges:
        return []
    return [f"**{range_label(doc)}: {_links(doc, ranges)}**", ""]


def render_block(block: ContentBlock, doc: AnyDocument) -> list[str]:
    suffix = f" ({_links(doc, block.source_ranges)})" if block.source_ranges else ""
    lines: list[str] = []
    if block.type == "paragraph":
        lines.append(block.text.strip() + suffix)
    elif block.type == "quote":
        lines.extend(f"> {line}" if line else ">" for line in block.text.strip().splitlines())
        if suffix:
            lines.append(f">{suffix}")
    elif block.type == "note":
        lines.append(f"> **{labels(doc.output_language)['note']}:** {_one_line(block.text)}{suffix}")
    elif block.type == "bullet_list":
        lines.extend(f"- {_one_line(i)}" for i in block.items)
        if suffix:
            lines.append(f"\n{suffix.strip()}")
    elif block.type == "numbered_list":
        lines.extend(f"{n}. {_one_line(i)}" for n, i in enumerate(block.items, 1))
        if suffix:
            lines.append(f"\n{suffix.strip()}")
    elif block.type == "checklist":
        lines.extend(f"- [ ] {_one_line(i)}" for i in block.items)
        if suffix:
            lines.append(f"\n{suffix.strip()}")
    elif block.type == "table":
        width = max([len(block.table_headers)] + [len(r) for r in block.table_rows])
        headers = list(block.table_headers) + [""] * (width - len(block.table_headers))
        lines.append("| " + " | ".join(_escape_cell(h) for h in headers) + " |")
        lines.append("|" + "|".join(["---"] * width) + "|")
        for row in block.table_rows:
            cells = list(row) + [""] * (width - len(row))
            lines.append("| " + " | ".join(_escape_cell(c) for c in cells) + " |")
        if suffix:
            lines.append(f"\n{suffix.strip()}")
    lines.append("")
    return lines


def render_markdown(doc: AnyDocument) -> str:
    lab = labels(doc.output_language)
    out: list[str] = [f"# {doc.title}", ""]
    for m in metadata_lines(doc):
        value = f"[{m.value}]({m.url})" if m.url else m.value
        out.append(f"> **{m.label}:** {value}  ")
    out.append("")

    sources = source_lines(doc)
    if sources:
        out += [f"**{lab['sources_used']}:**", ""]
        for src in sources:
            channel = f" — {src.channel}" if src.channel else ""
            out.append(f"{src.number}. **{_link_text(src.title)}**{channel} — [{src.url}]({src.url})")
        out.append("")

    if doc.summary.strip():
        out += [f"## {lab['summary']}", "", doc.summary.strip(), ""]

    for section in doc.sections:
        if section.title.strip():
            out += [f"## {section.title.strip()}", ""]
        out += _range_line(doc, section.source_ranges)
        for block in section.blocks:
            out += render_block(block, doc)
        for sub in section.subsections:
            out += [f"### {sub.title.strip()}", ""]
            out += _range_line(doc, sub.source_ranges)
            for block in sub.blocks:
                out += render_block(block, doc)

    if doc.key_points:
        out += [f"## {lab['key_points']}", ""]
        for kp in doc.key_points:
            links = _links(doc, kp.source_ranges)
            out.append(f"- {_one_line(kp.text)}" + (f" ({links})" if links else ""))
        out.append("")

    return "\n".join(out).rstrip() + "\n"
