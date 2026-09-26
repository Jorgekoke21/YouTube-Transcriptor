"""Structured LLM client.

`StructuredLLM` is the tiny seam the pipeline uses to ask for a Pydantic object.
`OpenAIStructuredLLM` is the only place that calls the OpenAI SDK: Responses API +
Structured Outputs (`responses.parse(text_format=...)`), stateless (`store=False`,
no `previous_response_id`), no tools (the model never browses the web).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core.errors import AppError, ErrorCode
from app.models.usage import AIUsage

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class StructuredLLM(ABC):
    model: str

    @property
    def fingerprint(self) -> str:
        """Everything about the model configuration that changes outputs (part of the cache key)."""
        return self.model

    @abstractmethod
    def parse(self, *, stage: str, instructions: str, input_text: str, schema: type[T], usage: AIUsage) -> T:
        """Return an instance of `schema` produced by the model."""


class _RetryableOutputError(Exception):
    pass


class OpenAIStructuredLLM(StructuredLLM):
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 180.0,
        max_retries: int = 3,
        reasoning: dict[str, str] | None = None,
        max_output_tokens: int = 32000,
        output_attempts: int = 2,
    ) -> None:
        from openai import OpenAI  # local import keeps the SDK out of module import time for tests

        # Network/429/5xx retries with exponential backoff are handled by the SDK itself.
        self._client = OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)
        self.model = model
        self.reasoning = dict(reasoning or {})
        self.max_output_tokens = max_output_tokens
        self.output_attempts = output_attempts

    @property
    def fingerprint(self) -> str:
        return f"{self.model}|" + ",".join(f"{k}={v}" for k, v in sorted(self.reasoning.items()))

    def parse(self, *, stage: str, instructions: str, input_text: str, schema: type[T], usage: AIUsage) -> T:
        import openai

        kwargs: dict = {
            "model": self.model,
            "instructions": instructions,
            "input": input_text,
            "text_format": schema,
            "store": False,
            "max_output_tokens": self.max_output_tokens,
        }
        if self.reasoning:
            kwargs["reasoning"] = dict(self.reasoning)

        last_error: Exception | None = None
        for attempt in range(1, self.output_attempts + 1):
            try:
                response = self._client.responses.parse(**kwargs)
                u = getattr(response, "usage", None)
                usage.record(stage, getattr(u, "input_tokens", 0), getattr(u, "output_tokens", 0))
                logger.info(
                    "AI call stage=%s model=%s in=%s out=%s status=%s",
                    stage,
                    self.model,
                    getattr(u, "input_tokens", "?"),
                    getattr(u, "output_tokens", "?"),
                    getattr(response, "status", "?"),
                )
                if getattr(response, "status", None) == "incomplete":
                    raise _RetryableOutputError("incomplete response (output token limit?)")
                parsed = response.output_parsed
                if parsed is None:
                    raise _RetryableOutputError("no parsed output (refusal or empty)")
                return parsed
            except (_RetryableOutputError, ValidationError, openai.LengthFinishReasonError) as exc:
                last_error = exc
                logger.warning("AI stage=%s attempt=%s unusable output: %s", stage, attempt, type(exc).__name__)
            except openai.AuthenticationError as exc:
                raise AppError(ErrorCode.AI_NOT_CONFIGURED, "La OPENAI_API_KEY no es válida.") from exc
            except openai.NotFoundError as exc:
                raise AppError(
                    ErrorCode.AI_PROCESSING_FAILED, f"El modelo configurado ('{self.model}') no está disponible."
                ) from exc
            except openai.BadRequestError as exc:
                logger.error("AI stage=%s bad request: %s", stage, getattr(exc, "message", type(exc).__name__))
                raise AppError(ErrorCode.AI_PROCESSING_FAILED) from exc
            except openai.OpenAIError as exc:  # after SDK retries (timeouts, rate limits, 5xx)
                logger.error("AI stage=%s failed: %s", stage, type(exc).__name__)
                raise AppError(ErrorCode.AI_PROCESSING_FAILED) from exc

        raise AppError(ErrorCode.AI_PROCESSING_FAILED, "La IA no devolvió una respuesta estructurada válida.") from last_error
