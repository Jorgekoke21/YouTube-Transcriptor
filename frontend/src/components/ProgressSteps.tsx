import type { DocumentDetail } from "../lib/api";
import { formatNumber, transcriptLabel } from "../lib/format";

export type StepState = "done" | "active" | "pending" | "failed";
export interface Step {
  key: string;
  label: string;
  detail?: string;
  state: StepState;
}

const STAGE_LABELS: Record<string, { label: string; unit?: string }> = {
  extracting: { label: "Analizando contenido", unit: "fragmento" },
  outlining: { label: "Organizando la estructura" },
  writing: { label: "Generando documento", unit: "sección" },
  verifying: { label: "Verificando fidelidad", unit: "sección" },
  cleaning: { label: "Limpiando la transcripción", unit: "fragmento" },
};

/** Steps derived only from real backend state (status + progress). No fake percentages. */
export function buildSteps(doc: DocumentDetail): Step[] {
  const p = doc.progress ?? {};
  const status = doc.status;
  const steps: Step[] = [];

  steps.push({
    key: "video",
    label: p.video_found ? "Vídeo encontrado" : "Buscando el vídeo…",
    state: p.video_found ? "done" : status === "pending" ? "pending" : "active",
  });
  steps.push({
    key: "transcript",
    label: p.transcript_found ? "Transcripción encontrada" : "Obteniendo la transcripción…",
    detail: p.transcript_found && p.transcript_language
      ? transcriptLabel({ language: p.transcript_language, is_generated: p.transcript_type === "generated" })
      : undefined,
    state: p.transcript_found ? "done" : p.video_found ? "active" : "pending",
  });
  if (p.transcript_found && typeof p.word_count === "number") {
    steps.push({ key: "words", label: `${formatNumber(p.word_count)} palabras`, state: "done" });
  }

  if (p.cache_hit) {
    steps.push({ key: "cache", label: "Documento reutilizado", detail: "misma transcripción, tipo, idioma y versión de prompts", state: "done" });
    return finalize(steps, status);
  }

  const stages = p.stages ?? (doc.document_type === "clean_transcript" ? ["cleaning"] : ["extracting", "outlining", "writing", "verifying"]);
  const pastAI = status === "generating_files" || status === "completed" || p.stage === "files";
  const current = p.stage ? stages.indexOf(p.stage) : -1;
  stages.forEach((stage, i) => {
    const meta = STAGE_LABELS[stage] ?? { label: stage };
    let state: StepState = "pending";
    if (pastAI) state = "done";
    else if (status === "processing" && current >= 0) state = i < current ? "done" : i === current ? "active" : "pending";
    else if (status === "processing" && current < 0 && i === 0) state = "active";
    const detail =
      state === "active" && meta.unit && p.stage_total
        ? `${meta.unit} ${p.stage_done ?? 0} de ${p.stage_total}`
        : undefined;
    steps.push({ key: stage, label: meta.label, detail, state });
  });

  steps.push({
    key: "files",
    label: "Preparando archivos",
    detail: status === "generating_files" ? "Markdown, DOCX, PDF y TXT" : undefined,
    state: status === "completed" ? "done" : status === "generating_files" ? "active" : "pending",
  });
  return finalize(steps, status);
}

// Wording for the step where processing stopped ("✕ Vídeo encontrado" would contradict itself).
const FAILED_LABELS: Record<string, string> = {
  video: "Vídeo no encontrado",
  transcript: "Transcripción no disponible",
};

export function finalize(steps: Step[], status: DocumentDetail["status"]): Step[] {
  if (status === "failed") {
    const idx = steps.findIndex((s) => s.state !== "done");
    if (idx >= 0) {
      const step = steps[idx];
      steps[idx] = { ...step, label: FAILED_LABELS[step.key] ?? step.label, state: "failed" };
    }
    return steps.filter((s) => s.state !== "pending");
  }
  return steps;
}

const ICONS: Record<StepState, string> = { done: "✓", active: "●", pending: "○", failed: "✕" };
const TONES: Record<StepState, string> = {
  done: "text-zinc-900 dark:text-zinc-100",
  active: "text-zinc-900 dark:text-zinc-100 font-medium",
  pending: "text-zinc-400 dark:text-zinc-600",
  failed: "text-red-600 dark:text-red-400 font-medium",
};
const ICON_TONES: Record<StepState, string> = {
  done: "text-emerald-600 dark:text-emerald-400",
  active: "text-amber-500 animate-pulse motion-reduce:animate-none",
  pending: "text-zinc-300 dark:text-zinc-700",
  failed: "text-red-600 dark:text-red-400",
};

export default function ProgressSteps({ doc }: { doc: DocumentDetail }) {
  return <StepList steps={buildSteps(doc)} />;
}

export function StepList({ steps }: { steps: Step[] }) {
  return (
    <ol className="space-y-2.5" aria-label="Estado del procesamiento">
      {steps.map((step) => (
        <li key={step.key} className={`flex items-baseline gap-3 ${TONES[step.state]}`} aria-current={step.state === "active" ? "step" : undefined}>
          <span className={`w-4 shrink-0 text-center ${ICON_TONES[step.state]}`} aria-hidden="true">
            {ICONS[step.state]}
          </span>
          <span>
            {step.label}
            {step.detail && <span className="font-normal text-zinc-500"> · {step.detail}</span>}
            <span className="sr-only">
              {step.state === "done" ? " (completado)" : step.state === "active" ? " (en curso)" : step.state === "failed" ? " (error)" : " (pendiente)"}
            </span>
          </span>
        </li>
      ))}
    </ol>
  );
}
