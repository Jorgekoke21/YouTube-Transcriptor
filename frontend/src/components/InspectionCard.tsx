import type { InspectionResult } from "../lib/api";
import { humanMessage } from "../lib/errors";
import { transcriptLabel } from "../lib/format";
import Thumbnail from "./Thumbnail";

export default function InspectionCard({ result }: { result: InspectionResult }) {
  const { video, transcript_available, selected_transcript, transcript_error, available_transcripts } = result;
  return (
    <div className="flex gap-4 rounded-2xl border border-zinc-200 p-3 dark:border-zinc-800 sm:gap-5 sm:p-4">
      <Thumbnail src={video.thumbnail} className="w-32 shrink-0 sm:w-44" />
      <div className="min-w-0 flex-1 py-0.5">
        <p className="line-clamp-2 font-medium leading-snug">{video.title ?? video.video_id}</p>
        {video.channel && <p className="mt-0.5 truncate text-sm text-zinc-500">{video.channel}</p>}
        {transcript_available && selected_transcript ? (
          <div className="mt-3 space-y-0.5 text-sm">
            <p className="font-medium text-emerald-700 dark:text-emerald-400">
              <span aria-hidden="true">✓ </span>Transcripción disponible
            </p>
            <p className="text-zinc-600 dark:text-zinc-400">
              Idioma: {transcriptLabel(selected_transcript)}
              {available_transcripts.length > 1 && (
                <span className="text-zinc-400"> · {available_transcripts.length} pistas disponibles</span>
              )}
            </p>
          </div>
        ) : (
          <div className="mt-3 space-y-0.5 text-sm">
            <p className="font-medium text-red-600 dark:text-red-400">
              <span aria-hidden="true">✕ </span>Transcripción no disponible
            </p>
            <p className="text-zinc-600 dark:text-zinc-400">{humanMessage(transcript_error?.code ?? "TRANSCRIPT_NOT_AVAILABLE")}</p>
          </div>
        )}
      </div>
    </div>
  );
}
