"""Versioned prompt families. Changing a prompt ⇒ bump its version."""

from __future__ import annotations

from app.models.document import DocumentType
from app.prompts.base import BASE_PROMPT, BASE_PROMPT_VERSION, language_instruction
from app.prompts.modes import MODE_PROMPTS, ModePrompt
from app.prompts.stages import PIPELINE_PROMPT_VERSION


def get_prompt_versions(document_type: DocumentType) -> dict[str, str]:
    return {
        "base": BASE_PROMPT_VERSION,
        "pipeline": PIPELINE_PROMPT_VERSION,
        "mode": MODE_PROMPTS[document_type].version,
    }


def prompt_version_string(document_type: DocumentType) -> str:
    v = get_prompt_versions(document_type)
    return f"{v['base']}+{v['pipeline']}+{v['mode']}"


__all__ = [
    "BASE_PROMPT",
    "MODE_PROMPTS",
    "ModePrompt",
    "get_prompt_versions",
    "language_instruction",
    "prompt_version_string",
]
