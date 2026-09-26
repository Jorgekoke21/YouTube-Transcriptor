import type { BatchInspectionResult } from "../lib/api";
import { humanMessage } from "../lib/errors";
import { formatTimestamp, transcriptLabel } from "../lib/format";
import Thumbnail from "./Thumbnail";

interface Props {
  result: BatchInspectionResult;
  selected: Set<string>;
  onToggle: (videoId: string) => void;
  onSetAll: (selectAll: boolean) => void;
}

/** One row per analysed video: each one is independent (its error never hides the others). */
export default function BatchInspectionList({ result, selected, onToggle, onSetAll }: Props) {
  const { videos, invalid, duplicates, available_count } = result;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
        <p className="text-zinc-600 dark:text-zinc-400">
          {available_count} de {videos.length} vídeos con transcripción · <strong>{selected.size} seleccionados</strong>
        </p>
        {available_count > 0 && (
          <p className="flex gap-3">
            <button type="button" onClick={() => onSetAll(true)} className="font-medium underline underline-offset-4">
              Seleccionar todos
            </button>
            <button type="button" onClick={() => onSetAll(false)} className="font-medium underline underline-offset-4">
              Ninguno
            </button>
          </p>
        )}
      </div>

      <ul className="space-y-2">
        {videos.map((v) => {
          const id = v.video.video_id;
          const usable = v.transcript_available;
          const checked = usable && selected.has(id);
          return (
            <li key={id}>
              <label
                className={`flex gap-3 rounded-2xl border p-3 transition sm:gap-4 ${
                  checked
                    ? "border-zinc-900 dark:border-zinc-300"
                    : "border-zinc-200 dark:border-zinc-800"
                } ${usable ? "cursor-pointer" : "cursor-not-allowed opacity-80"}`}
              >
                <input
                  type="checkbox"
                  className="mt-1 h-4 w-4 shrink-0 accent-zinc-900 dark:accent-zinc-100"
                  checked={checked}
                  disabled={!usable}
                  onChange={() => onToggle(id)}
                  aria-label={`Procesar ${v.video.title ?? id}`}
                />
                <Thumbnail src={v.video.thumbnail} className="w-24 shrink-0 sm:w-32" />
                <div className="min-w-0 flex-1 text-sm">
                  <p className="line-clamp-2 font-medium leading-snug">
                    <span className="text-zinc-400">{v.position + 1}. </span>
                    {v.video.title ?? id}
                  </p>
                  <p className="mt-0.5 truncate text-zinc-500">
                    {[v.video.channel, v.duration_seconds ? formatTimestamp(v.duration_seconds) : null]
                      .filter(Boolean)
                      .join(" · ") || v.input}
                  </p>
                  {usable && v.selected_transcript ? (
                    <p className="mt-1.5 text-emerald-700 dark:text-emerald-400">
                      <span aria-hidden="true">✓ </span>Transcripción disponible ·{" "}
                      <span className="text-zinc-600 dark:text-zinc-400">{transcriptLabel(v.selected_transcript)}</span>
                    </p>
                  ) : (
                    <p className="mt-1.5 text-red-600 dark:text-red-400">
                      <span aria-hidden="true">✕ </span>
                      {humanMessage(v.error?.code ?? v.transcript_error?.code ?? "TRANSCRIPT_NOT_AVAILABLE")}
                    </p>
                  )}
                </div>
              </label>
            </li>
          );
        })}
      </ul>

      {(invalid.length > 0 || duplicates.length > 0) && (
        <div className="rounded-2xl bg-zinc-50 px-4 py-3 text-sm text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400">
          {duplicates.length > 0 && (
            <p>
              {duplicates.length === 1 ? "1 URL repetida se ha ignorado" : `${duplicates.length} URLs repetidas se han ignorado`}{" "}
              (mismo vídeo).
            </p>
          )}
          {invalid.length > 0 && (
            <>
              <p>{invalid.length === 1 ? "1 línea no es" : `${invalid.length} líneas no son`} una URL de vídeo de YouTube:</p>
              <ul className="mt-1 list-inside list-disc">
                {invalid.map((line) => (
                  <li key={line.position} className="truncate font-mono text-xs">
                    {line.input}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}
