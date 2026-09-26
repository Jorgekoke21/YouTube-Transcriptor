import type { DocumentType, OutputMode, TranscriptInfo } from "./api";

export const DOCUMENT_TYPES: { value: DocumentType; label: string; hint: string }[] = [
  { value: "full_notes", label: "Apuntes completos", hint: "Todas las ideas relevantes, reorganizadas para estudiar." },
  { value: "summary", label: "Resumen", hint: "Idea central, puntos importantes y conclusiones." },
  { value: "step_by_step", label: "Guía paso a paso", hint: "Procedimientos convertidos en pasos ordenados." },
  { value: "study_guide", label: "Manual de estudio", hint: "Conceptos, reglas, checklist y preguntas de repaso." },
  { value: "trading", label: "Trading", hint: "Setup, entrada, gestión… solo lo que se dice en el vídeo." },
  { value: "clean_transcript", label: "Transcripción limpia", hint: "La transcripción con puntuación y párrafos." },
];

export function formatTimestamp(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

export function youtubeAt(videoId: string, seconds: number): string {
  return `https://www.youtube.com/watch?v=${videoId}&t=${Math.max(0, Math.floor(seconds))}s`;
}

export function transcriptLabel(t: Pick<TranscriptInfo, "language" | "is_generated">): string {
  return `${t.language} · ${t.is_generated ? "automática" : "manual"}`;
}

export function formatNumber(n: number): string {
  return new Intl.NumberFormat("es-ES").format(n);
}

export function formatDate(iso: string): string {
  const date = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat("es-ES", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export const OUTPUT_MODES: { value: OutputMode; label: string; hint: string }[] = [
  { value: "individual", label: "Documentos individuales", hint: "Un documento independiente por cada vídeo." },
  {
    value: "consolidated",
    label: "Documento consolidado",
    hint: "Un único documento que integra los vídeos, con coincidencias y diferencias entre fuentes.",
  },
  { value: "both", label: "Ambos", hint: "Los documentos individuales y además el consolidado." },
];

const ID_RE = /^[A-Za-z0-9_-]{11}$/;

/** The YouTube video id of a URL, or null. Light client-side check: the backend validates for real. */
export function extractVideoId(value: string): string | null {
  const v = value.trim();
  if (!v || /\s/.test(v)) return null;
  try {
    const url = new URL(v.includes("://") ? v : `https://${v}`);
    const host = url.hostname.toLowerCase();
    let id: string | null = null;
    if (host === "youtu.be" || host === "www.youtu.be") id = url.pathname.split("/")[1] ?? null;
    else if (!/(^|\.)youtube(-nocookie)?\.com$/.test(host)) return null;
    else {
      const parts = url.pathname.split("/").filter(Boolean);
      if (parts[0] === "watch" || parts.length === 0) id = url.searchParams.get("v");
      else if (["shorts", "live", "embed", "v", "e"].includes(parts[0] ?? "")) id = parts[1] ?? null;
    }
    return id && ID_RE.test(id) ? id : null;
  } catch {
    return null;
  }
}

export function looksLikeYouTubeUrl(value: string): boolean {
  return extractVideoId(value) !== null;
}

export interface ParsedUrls {
  /** Non-empty tokens in paste order (URLs may be separated by newlines, spaces, commas, or tabs). */
  lines: string[];
  /** First token of each distinct video, in paste order. */
  unique: { input: string; videoId: string }[];
  invalid: string[];
  duplicates: number;
}

/** Split pasted text into URLs: any run of whitespace or commas separates entries, so URLs work whether
 * they're one per line or pasted inline with spaces between them. Dedupe by video id, keep paste order. */
export function parseUrlLines(text: string): ParsedUrls {
  const lines = text
    .split(/[\s,]+/)
    .map((l) => l.trim())
    .filter(Boolean);
  const seen = new Set<string>();
  const unique: ParsedUrls["unique"] = [];
  const invalid: string[] = [];
  let duplicates = 0;
  for (const line of lines) {
    const videoId = extractVideoId(line);
    if (!videoId) invalid.push(line);
    else if (seen.has(videoId)) duplicates += 1;
    else {
      seen.add(videoId);
      unique.push({ input: line, videoId });
    }
  }
  return { lines, unique, invalid, duplicates };
}
