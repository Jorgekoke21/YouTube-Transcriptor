// Typed client for the FastAPI backend. All requests go through the Vite proxy (/api).

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "";

export type DocumentType =
  | "full_notes"
  | "summary"
  | "step_by_step"
  | "study_guide"
  | "trading"
  | "clean_transcript";

export type OutputLanguage = "es" | "en";

export type OutputMode = "individual" | "consolidated" | "both";

export type DocumentStatus =
  | "pending"
  | "fetching_transcript"
  | "processing"
  | "generating_files"
  | "completed"
  | "failed";

export interface ErrorInfo {
  code: string;
  message: string;
  /** Present only when the backend message is more specific than the code's generic text. */
  detail?: string | null;
}

export interface TranscriptInfo {
  language: string;
  language_code: string;
  is_generated: boolean;
  is_manual: boolean;
  kind: "manual" | "generated";
}

export interface VideoMetadata {
  video_id: string;
  url: string;
  title: string | null;
  channel: string | null;
  thumbnail: string | null;
}

export interface InspectionResult {
  valid_url: boolean;
  video: VideoMetadata;
  transcript_available: boolean;
  selected_transcript: TranscriptInfo | null;
  available_transcripts: TranscriptInfo[];
  transcript_error: ErrorInfo | null;
}

export interface VideoOut extends VideoMetadata {
  transcript_language: string | null;
  transcript_type: string | null;
}

export interface Progress {
  video_found?: boolean;
  transcript_found?: boolean;
  transcript_language?: string;
  transcript_type?: "manual" | "generated";
  word_count?: number;
  segment_count?: number;
  duration_seconds?: number;
  stages?: string[];
  stage?: string | null;
  stage_done?: number | null;
  stage_total?: number | null;
  cache_hit?: boolean;
}

export interface DocumentSummary {
  id: number;
  status: DocumentStatus;
  document_type: DocumentType;
  document_type_label: string;
  output_language: OutputLanguage;
  title: string | null;
  video: VideoOut;
  created_at: string;
  updated_at: string;
  reused_from_id: number | null;
  error: ErrorInfo | null;
}

export interface DocumentDetail extends DocumentSummary {
  progress: Progress;
  prompt_version: string | null;
  model: string | null;
  usage: Record<string, unknown> | null;
  markdown: string | null;
  downloads: Partial<Record<"markdown" | "docx" | "pdf" | "txt", string>>;
}

// ---- multi-URL batches

export interface BatchVideoInspection extends InspectionResult {
  /** Position among the unique videos, in paste order. */
  position: number;
  input: string;
  duration_seconds: number | null;
  error: ErrorInfo | null;
}

export interface BatchLine {
  position: number;
  input: string;
  video_id: string | null;
  duplicate_of: number | null;
  error: ErrorInfo | null;
}

export interface BatchInspectionResult {
  videos: BatchVideoInspection[];
  invalid: BatchLine[];
  duplicates: BatchLine[];
  valid_count: number;
  available_count: number;
  max_videos: number;
}

export type CollectionVideoStatus =
  | "pending"
  | "document_ready"
  | "transcript_ready"
  | "extracted"
  | "included"
  | "failed";

export interface CollectionVideo {
  /** Order in which the URL was given: metadata only (a collection is an unordered set). */
  position: number;
  input_url: string;
  video: VideoOut;
  status: CollectionVideoStatus;
  document_id: number | null;
  document_status: DocumentStatus | null;
  extraction_cached: boolean | null;
  error: ErrorInfo | null;
}

export interface CollectionProgress {
  phase?: "individual" | "transcripts" | "consolidated" | "done";
  videos_total?: number;
  videos_ready?: number;
  individual_total?: number;
  individual_done?: number;
  current_video?: number | null;
  stages?: string[];
  stage?: string | null;
  stage_done?: number | null;
  stage_total?: number | null;
  extraction_cache_hits?: number;
  cache_hit?: boolean;
}

export interface CollectionSummary {
  kind: "collection";
  id: number;
  /** Hash of the canonical set of videos: same videos ⇒ same identity, whatever the input order. */
  identity: string;
  status: DocumentStatus;
  output_mode: OutputMode;
  document_type: DocumentType;
  document_type_label: string;
  output_language: OutputLanguage;
  title: string | null;
  video_count: number;
  videos: CollectionVideo[];
  created_at: string;
  updated_at: string;
  reused_from_id: number | null;
  error: ErrorInfo | null;
}

export interface CollectionDetail extends CollectionSummary {
  progress: CollectionProgress;
  prompt_version: string | null;
  model: string | null;
  usage: Record<string, unknown> | null;
  markdown: string | null;
  downloads: Partial<Record<"markdown" | "docx" | "pdf" | "txt", string>>;
}

export interface TranscriptSegment {
  text: string;
  start: number;
  duration: number;
  end: number;
}

export interface TranscriptOut {
  video_id: string;
  url: string;
  title: string | null;
  channel: string | null;
  language: string;
  language_code: string;
  is_generated: boolean;
  duration_seconds: number;
  word_count: number;
  segments: TranscriptSegment[];
}

export class ApiError extends Error {
  code: string;
  status: number;
  detail: string | null;
  constructor(code: string, message: string, status: number, detail: string | null = null) {
    super(message);
    this.code = code;
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError("NETWORK_ERROR", "No se puede conectar con el servidor.", 0);
  }
  if (!response.ok) {
    let error: ErrorInfo = { code: "INTERNAL_ERROR", message: "" };
    try {
      const body = await response.json();
      if (body?.error?.code) error = body.error;
    } catch {
      // non-JSON error body (e.g. proxy error when the backend is down)
      if (response.status >= 500) error = { code: "NETWORK_ERROR", message: "" };
    }
    throw new ApiError(error.code, error.message, response.status, error.detail ?? null);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<{ status: string; ai_configured: boolean; ai_provider: string; model: string }>("/api/health"),
  inspect: (url: string, signal?: AbortSignal) =>
    request<InspectionResult>("/api/videos/inspect", { method: "POST", body: JSON.stringify({ url }), signal }),
  createDocument: (body: {
    url: string;
    document_type: DocumentType;
    output_language: OutputLanguage;
    force_regenerate?: boolean;
  }) => request<DocumentDetail>("/api/documents", { method: "POST", body: JSON.stringify(body) }),
  getDocument: (id: number) => request<DocumentDetail>(`/api/documents/${id}`),
  listDocuments: (limit = 12) => request<DocumentSummary[]>(`/api/documents?limit=${limit}`),
  getTranscript: (videoId: string) => request<TranscriptOut>(`/api/videos/${encodeURIComponent(videoId)}/transcript`),
  inspectBatch: (urls: string[], signal?: AbortSignal) =>
    request<BatchInspectionResult>("/api/videos/inspect-batch", { method: "POST", body: JSON.stringify({ urls }), signal }),
  createCollection: (body: {
    urls: string[];
    document_type: DocumentType;
    output_language: OutputLanguage;
    output_mode: OutputMode;
    force_regenerate?: boolean;
  }) => request<CollectionDetail>("/api/document-collections", { method: "POST", body: JSON.stringify(body) }),
  getCollection: (id: number) => request<CollectionDetail>(`/api/document-collections/${id}`),
  listCollections: (limit = 12) => request<CollectionSummary[]>(`/api/document-collections?limit=${limit}`),
  downloadUrl: (path: string) => `${API_BASE}${path}`,
};
