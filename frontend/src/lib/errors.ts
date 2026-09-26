import { ApiError } from "./api";

// Backend error codes → human messages. Stack traces never reach the UI.
const MESSAGES: Record<string, string> = {
  INVALID_YOUTUBE_URL: "La URL no parece un enlace válido a un vídeo de YouTube.",
  VIDEO_NOT_FOUND: "No se ha encontrado el vídeo. Comprueba que la URL sea correcta y que el vídeo sea público.",
  TRANSCRIPT_NOT_AVAILABLE:
    "No se ha encontrado una transcripción disponible para este vídeo. Este vídeo no puede procesarse.",
  TRANSCRIPT_DISABLED: "Este vídeo no dispone de una transcripción accesible y no puede procesarse.",
  TRANSCRIPT_FETCH_FAILED:
    "No se ha podido recuperar la transcripción desde YouTube. Inténtalo de nuevo dentro de unos minutos.",
  AI_NOT_CONFIGURED:
    "El procesamiento con IA no está configurado. Añade OPENAI_API_KEY al archivo .env y reinicia el backend.",
  AI_PROCESSING_FAILED: "Ha fallado el procesamiento del contenido con IA. Inténtalo de nuevo.",
  DOCUMENT_GENERATION_FAILED: "Se procesó el contenido, pero falló la generación de los archivos.",
  DOCUMENT_NOT_FOUND: "No se ha encontrado el documento.",
  DOCUMENT_NOT_READY: "El documento todavía no está terminado.",
  COLLECTION_NOT_FOUND: "No se ha encontrado la colección.",
  BATCH_TOO_LARGE: "Has incluido demasiados vídeos en un mismo lote.",
  NOT_ENOUGH_VIDEOS: "Un documento consolidado necesita al menos dos vídeos con transcripción disponible.",
  INVALID_REQUEST: "La solicitud no es válida.",
  NETWORK_ERROR: "No se puede conectar con el servidor. ¿Está el backend en marcha?",
  INTERNAL_ERROR: "Se ha producido un error inesperado.",
};

export function humanMessage(code: string | undefined | null): string {
  return (code && MESSAGES[code]) || MESSAGES.INTERNAL_ERROR;
}

/** Human message plus the backend detail, only when the backend adds specific information. */
export function describeError(error: unknown): { title: string; detail: string | null } {
  if (error instanceof ApiError) return { title: humanMessage(error.code), detail: error.detail };
  return { title: MESSAGES.INTERNAL_ERROR, detail: null };
}
