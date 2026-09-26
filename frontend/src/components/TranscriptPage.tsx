import { useEffect, useMemo, useState } from "react";
import { api, type TranscriptOut, type TranscriptSegment } from "../lib/api";
import { describeError } from "../lib/errors";
import { formatNumber, formatTimestamp, transcriptLabel, youtubeAt } from "../lib/format";
import ErrorBox from "./ErrorBox";

const BLOCK_SECONDS = 20;

/** Groups consecutive segments into ~20 s blocks so the transcript reads as paragraphs. */
function groupSegments(segments: TranscriptSegment[]): { start: number; text: string }[] {
  const blocks: { start: number; text: string }[] = [];
  for (const seg of segments) {
    const last = blocks[blocks.length - 1];
    if (last && seg.start - last.start < BLOCK_SECONDS) last.text += ` ${seg.text}`;
    else blocks.push({ start: seg.start, text: seg.text });
  }
  return blocks;
}

export default function TranscriptPage({ videoId, documentId }: { videoId: string; documentId?: number }) {
  const [data, setData] = useState<TranscriptOut | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.getTranscript(videoId).then(setData).catch(setError);
  }, [videoId]);

  const blocks = useMemo(() => (data ? groupSegments(data.segments) : []), [data]);

  return (
    <div className="mx-auto max-w-3xl pt-6 sm:pt-12">
      {documentId && (
        <a href={`#/documents/${documentId}`} className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
          ← Volver al documento
        </a>
      )}
      <p className="mt-4 text-sm font-semibold uppercase tracking-wider text-zinc-500">Transcripción original</p>
      {error !== null && <div className="mt-6"><ErrorBox {...describeError(error)} /></div>}
      {!data && error === null && <p className="mt-6 text-zinc-500">Cargando…</p>}
      {data && (
        <>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight sm:text-3xl">{data.title ?? data.video_id}</h1>
          <p className="mt-2 text-sm text-zinc-500">
            {data.channel && <>{data.channel} · </>}
            {transcriptLabel(data)} · {formatNumber(data.word_count)} palabras · {formatTimestamp(data.duration_seconds)}
          </p>
          <ol className="mt-10 space-y-5">
            {blocks.map((block) => (
              <li key={block.start} className="grid grid-cols-[4.5rem_1fr] gap-3 sm:grid-cols-[5.5rem_1fr]">
                <a
                  href={youtubeAt(data.video_id, block.start)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="self-start whitespace-nowrap font-mono text-sm text-zinc-500 hover:text-red-700 dark:hover:text-red-300"
                  title="Abrir el vídeo en este momento"
                >
                  [{formatTimestamp(block.start)}]
                </a>
                <p className="leading-relaxed">{block.text}</p>
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}
