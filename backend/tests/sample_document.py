"""A hand-written ProcessedDocument covering every block type (used by renderer tests)."""

from __future__ import annotations

from app.models.document import (
    ContentBlock,
    DocumentSection,
    DocumentSubsection,
    DocumentType,
    KeyPoint,
    ProcessedDocument,
    SourceRange,
    SourceVideo,
)


def block(type_: str, text: str = "", items=None, headers=None, rows=None, ranges=None) -> ContentBlock:
    return ContentBlock(
        type=type_,  # type: ignore[arg-type]
        text=text,
        items=items or [],
        table_headers=headers or [],
        table_rows=rows or [],
        source_ranges=ranges or [],
    )


def sample_document(output_language: str = "es") -> ProcessedDocument:
    return ProcessedDocument(
        title="Divergencias RSI",
        summary="El vídeo explica qué es una divergencia y por qué **no es una entrada por sí sola**.",
        sections=[
            DocumentSection(
                title="Qué es una divergencia",
                blocks=[
                    block(
                        "paragraph",
                        "Una divergencia aparece cuando precio y RSI no acompañan.",
                        ranges=[SourceRange(start=12.3, end=24.4)],
                    ),
                ],
                source_ranges=[SourceRange(start=7.3, end=32.8)],
                subsections=[
                    DocumentSubsection(
                        title="Divergencia alcista",
                        blocks=[block("bullet_list", items=["Precio: mínimo más bajo", "RSI: mínimo más alto"])],
                        source_ranges=[SourceRange(start=12.3, end=24.4)],
                    ),
                    DocumentSubsection(
                        title="Comparativa",
                        blocks=[
                            block(
                                "table",
                                headers=["Tipo", "Precio", "RSI"],
                                rows=[
                                    ["Alcista", "Mínimo más bajo", "Mínimo más alto"],
                                    ["Bajista", "Máximo más alto", "Máximo | más bajo"],
                                ],
                            )
                        ],
                        source_ranges=[],
                    ),
                ],
            ),
            DocumentSection(
                title="Confirmación de entrada",
                blocks=[
                    block("quote", "Una divergencia por sí sola no es una entrada."),
                    block(
                        "numbered_list",
                        items=[
                            "Detectar la divergencia",
                            "Esperar la ruptura del último máximo relevante",
                            "Entrar tras la ruptura",
                        ],
                    ),
                    block("note", "No se especifica una regla concreta sobre el Stop Loss en el vídeo."),
                    block("checklist", items=["¿Hay divergencia?", "¿Ha roto la estructura?"]),
                ],
                source_ranges=[SourceRange(start=1122.4, end=1270.0)],
                subsections=[],
            ),
        ],
        key_points=[KeyPoint(text="La divergencia no es una entrada.", source_ranges=[SourceRange(start=34.8, end=40.8)])],
        source_video=SourceVideo(
            video_id="dQw4w9WgXcQ",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            title="Divergencias RSI: cómo operarlas",
            channel="Canal de ejemplo",
            transcript_language="Español",
            transcript_language_code="es",
            transcript_is_generated=True,
            duration_seconds=1300.0,
        ),
        document_type=DocumentType.TRADING,
        output_language=output_language,
        generated_at="2026-09-25T20:00:00+00:00",
        prompt_versions={"base": "base-v1"},
        model="test-model",
    )
