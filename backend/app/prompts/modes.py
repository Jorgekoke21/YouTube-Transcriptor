"""Mode-specific instructions, one versioned entry per document type."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.document import DocumentType


@dataclass(frozen=True)
class ModePrompt:
    version: str
    extraction_focus: str
    outline_guidance: str
    writing_guidance: str


TRADING_CATEGORIES = (
    "concepto, estrategia, contexto_de_mercado, setup, condiciones_previas, timeframe, estructura, tendencia, "
    "liquidez, indicadores, confirmaciones, entrada, stop_loss, take_profit, invalidacion, gestion_de_la_posicion, "
    "gestion_de_riesgo, ejemplos, errores, excepciones, reglas_del_profesor, checklist_operativo"
)

MODE_PROMPTS: dict[DocumentType, ModePrompt] = {
    DocumentType.FULL_NOTES: ModePrompt(
        version="full-notes-v1",
        extraction_focus=(
            "Extrae TODAS las ideas con valor formativo: conceptos, definiciones, explicaciones, matices, reglas, "
            "condiciones, pasos, ejemplos, advertencias, errores y excepciones. No empobrezcas el contenido."
        ),
        outline_guidance=(
            "Modo APUNTES COMPLETOS. Diseña una estructura que preserve todas las ideas relevantes, agrupadas "
            "de la forma más útil para estudiar. Usa capítulos y subsecciones adaptados al contenido (no una "
            "plantilla fija). Incluye al final, si hay material, capítulos como 'Errores habituales', 'Reglas "
            "importantes' o 'Ideas clave' sólo cuando la fuente los respalde."
        ),
        writing_guidance=(
            "Redacta apuntes completos y bien organizados: párrafos claros, listas cuando haya enumeraciones, "
            "tablas cuando haya comparaciones, citas destacadas para frases clave del ponente. Conserva "
            "explicaciones, matices y ejemplos importantes; elimina sólo el ruido. No es un resumen corto."
        ),
    ),
    DocumentType.SUMMARY: ModePrompt(
        version="summary-v1",
        extraction_focus="Extrae las ideas principales, conceptos, conclusiones y puntos importantes.",
        outline_guidance=(
            "Modo RESUMEN. Estructura condensada de 3 a 6 capítulos: idea central, puntos importantes, "
            "conceptos y conclusiones (adapta los títulos al contenido real)."
        ),
        writing_guidance=(
            "Redacta un resumen condensado y preciso. Prioriza lo esencial, sin perder matices que cambien el "
            "significado. Usa listas breves cuando ayuden."
        ),
    ),
    DocumentType.STEP_BY_STEP: ModePrompt(
        version="step-by-step-v1",
        extraction_focus=(
            "Presta especial atención a procedimientos: pasos, orden, requisitos previos, condiciones, "
            "decisiones y advertencias asociadas a cada paso."
        ),
        outline_guidance=(
            "Modo GUÍA PASO A PASO. Organiza el documento en torno a los procedimientos detectados. Cada "
            "procedimiento es un capítulo; incluye requisitos previos y advertencias sólo si la fuente los menciona."
        ),
        writing_guidance=(
            "Convierte los procedimientos en listas numeradas ordenadas (numbered_list). Añade en párrafos o "
            "notas las condiciones y advertencias de cada paso. No inventes pasos intermedios que no se digan."
        ),
    ),
    DocumentType.STUDY_GUIDE: ModePrompt(
        version="study-guide-v1",
        extraction_focus=("Extrae conceptos, definiciones, explicaciones, ejemplos mencionados, reglas, errores y puntos clave."),
        outline_guidance=(
            "Modo MANUAL DE ESTUDIO. Capítulos posibles: conceptos y definiciones, explicación, ejemplos "
            "mencionados, reglas, errores, puntos clave, checklist y preguntas de repaso. Incluye sólo los que "
            "tengan contenido."
        ),
        writing_guidance=(
            "Crea material de estudio: definiciones claras (tabla término/definición cuando proceda), "
            "explicaciones, ejemplos mencionados, reglas, errores, checklist (checklist) y preguntas de repaso "
            "cuya respuesta esté en la fuente (numbered_list)."
        ),
    ),
    DocumentType.CLEAN_TRANSCRIPT: ModePrompt(
        version="clean-transcript-v1",
        extraction_focus="",
        outline_guidance="",
        writing_guidance=(
            "Crea una versión limpia de la transcripción: añade puntuación, divide en párrafos, añade títulos "
            "cuando cambie el tema y elimina moderadamente muletillas y repeticiones evidentes. Modifica lo "
            "mínimo posible el contenido: NO resumas, NO reordenes, NO añadas nada."
        ),
    ),
    DocumentType.TRADING: ModePrompt(
        version="trading-v3",
        extraction_focus=(
            "Vídeo de formación de trading. Etiqueta cada elemento (tags) con una o varias de estas categorías "
            f"cuando corresponda: {TRADING_CATEGORIES}. Detecta únicamente lo que aparezca realmente en el vídeo. "
            "Sé especialmente estricto: nunca deduzcas niveles de stop, objetivos, ratios, indicadores o "
            "timeframes que no se mencionen."
        ),
        outline_guidance=(
            "Modo TRADING. Crea capítulos sólo para las categorías con contenido real (por ejemplo: concepto, "
            "contexto, setup, confirmación, entrada, stop loss, take profit, invalidación, gestión, ejemplos, "
            "errores, reglas del profesor, checklist operativo). No crees secciones vacías. Si la ausencia de "
            "un elemento clave de la estrategia (p. ej. stop loss o gestión del riesgo) es importante para "
            "entenderla, puedes crear un capítulo breve que lo indique explícitamente. Reserva un único capítulo final "
            "'Checklist operativo' (si hay condiciones suficientes); no pongas checklists en otros capítulos."
        ),
        writing_guidance=(
            "Redacta un documento operativo para trading. Cuando el purpose del capítulo marque una AUSENCIA, "
            "usa un bloque 'note' con un texto como: 'El profesor no establece en este vídeo una regla concreta "
            "para colocar el Stop Loss.' Si el capítulo lo pide, cierra con un checklist operativo (checklist) "
            "sólo con condiciones mencionadas en el vídeo."
        ),
    ),
}
