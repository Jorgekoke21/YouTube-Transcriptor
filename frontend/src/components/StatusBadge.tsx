import type { DocumentStatus } from "../lib/api";

const LABELS: Record<DocumentStatus, string> = {
  pending: "En cola",
  fetching_transcript: "Obteniendo transcripción",
  processing: "Procesando",
  generating_files: "Generando archivos",
  completed: "Completado",
  failed: "Error",
};

export default function StatusBadge({ status }: { status: DocumentStatus }) {
  const tone =
    status === "completed"
      ? "text-emerald-700 dark:text-emerald-400"
      : status === "failed"
        ? "text-red-600 dark:text-red-400"
        : "text-amber-600 dark:text-amber-400";
  const icon = status === "completed" ? "✓" : status === "failed" ? "✕" : "●";
  return (
    <span className={`shrink-0 text-xs font-medium ${tone}`}>
      <span aria-hidden="true">{icon} </span>
      {LABELS[status]}
    </span>
  );
}
