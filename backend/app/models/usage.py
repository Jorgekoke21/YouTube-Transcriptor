"""AI usage accounting (tokens / calls). Cost is not computed yet."""

from __future__ import annotations

import threading

from pydantic import BaseModel, Field, PrivateAttr


class StageUsage(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class AIUsage(BaseModel):
    model: str | None = None
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    by_stage: dict[str, StageUsage] = Field(default_factory=dict)

    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    def record(self, stage: str, input_tokens: int | None, output_tokens: int | None) -> None:
        with self._lock:
            inp, out = input_tokens or 0, output_tokens or 0
            self.calls += 1
            self.input_tokens += inp
            self.output_tokens += out
            self.total_tokens += inp + out
            stage_usage = self.by_stage.setdefault(stage, StageUsage())
            stage_usage.calls += 1
            stage_usage.input_tokens += inp
            stage_usage.output_tokens += out
