"""Application error codes.

Every expected failure is raised as an `AppError` carrying a stable machine code.
The API layer turns it into a JSON response `{"error": {"code", "message"}}`
and the frontend maps the code to a human message. Stack traces never reach
the client.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_YOUTUBE_URL = "INVALID_YOUTUBE_URL"
    VIDEO_NOT_FOUND = "VIDEO_NOT_FOUND"
    TRANSCRIPT_NOT_AVAILABLE = "TRANSCRIPT_NOT_AVAILABLE"
    TRANSCRIPT_DISABLED = "TRANSCRIPT_DISABLED"
    TRANSCRIPT_FETCH_FAILED = "TRANSCRIPT_FETCH_FAILED"
    AI_NOT_CONFIGURED = "AI_NOT_CONFIGURED"
    AI_PROCESSING_FAILED = "AI_PROCESSING_FAILED"
    DOCUMENT_GENERATION_FAILED = "DOCUMENT_GENERATION_FAILED"
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
    DOCUMENT_NOT_READY = "DOCUMENT_NOT_READY"
    COLLECTION_NOT_FOUND = "COLLECTION_NOT_FOUND"
    BATCH_TOO_LARGE = "BATCH_TOO_LARGE"
    NOT_ENOUGH_VIDEOS = "NOT_ENOUGH_VIDEOS"
    INVALID_REQUEST = "INVALID_REQUEST"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_YOUTUBE_URL: 400,
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.BATCH_TOO_LARGE: 400,
    ErrorCode.NOT_ENOUGH_VIDEOS: 422,
    ErrorCode.VIDEO_NOT_FOUND: 404,
    ErrorCode.COLLECTION_NOT_FOUND: 404,
    ErrorCode.DOCUMENT_NOT_FOUND: 404,
    ErrorCode.TRANSCRIPT_NOT_AVAILABLE: 422,
    ErrorCode.TRANSCRIPT_DISABLED: 422,
    ErrorCode.DOCUMENT_NOT_READY: 409,
    ErrorCode.TRANSCRIPT_FETCH_FAILED: 502,
    ErrorCode.AI_PROCESSING_FAILED: 502,
    ErrorCode.AI_NOT_CONFIGURED: 503,
    ErrorCode.DOCUMENT_GENERATION_FAILED: 500,
    ErrorCode.INTERNAL_ERROR: 500,
}

_DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.INVALID_YOUTUBE_URL: "La URL no es una URL válida de un vídeo de YouTube.",
    ErrorCode.VIDEO_NOT_FOUND: "No se ha encontrado el vídeo o no está disponible.",
    ErrorCode.TRANSCRIPT_NOT_AVAILABLE: "No se ha encontrado una transcripción disponible para este vídeo.",
    ErrorCode.TRANSCRIPT_DISABLED: "Las transcripciones están desactivadas para este vídeo.",
    ErrorCode.TRANSCRIPT_FETCH_FAILED: "No se ha podido recuperar la transcripción del vídeo.",
    ErrorCode.AI_NOT_CONFIGURED: "El procesamiento con IA no está configurado (falta OPENAI_API_KEY).",
    ErrorCode.AI_PROCESSING_FAILED: "Ha fallado el procesamiento del contenido con IA.",
    ErrorCode.DOCUMENT_GENERATION_FAILED: "Ha fallado la generación de los archivos del documento.",
    ErrorCode.DOCUMENT_NOT_FOUND: "No se ha encontrado el documento.",
    ErrorCode.DOCUMENT_NOT_READY: "El documento todavía no está terminado.",
    ErrorCode.COLLECTION_NOT_FOUND: "No se ha encontrado la colección.",
    ErrorCode.BATCH_TOO_LARGE: "Se han enviado demasiados vídeos en un mismo lote.",
    ErrorCode.NOT_ENOUGH_VIDEOS: "Un documento consolidado necesita al menos dos vídeos con transcripción.",
    ErrorCode.INVALID_REQUEST: "Solicitud no válida.",
    ErrorCode.INTERNAL_ERROR: "Error interno inesperado.",
}


class AppError(Exception):
    def __init__(self, code: ErrorCode, message: str | None = None) -> None:
        self.code = code
        self.message = message or _DEFAULT_MESSAGES[code]
        super().__init__(f"{code}: {self.message}")

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS.get(self.code, 500)

    def to_dict(self) -> dict[str, str | None]:
        return error_dict(self.code.value, self.message)


def error_dict(code: str, message: str) -> dict[str, str | None]:
    """`detail` repeats the message only when it is more specific than the code's generic text."""
    try:
        default = _DEFAULT_MESSAGES[ErrorCode(code)]
    except ValueError:
        default = None
    return {"code": code, "message": message, "detail": message if message and message != default else None}
