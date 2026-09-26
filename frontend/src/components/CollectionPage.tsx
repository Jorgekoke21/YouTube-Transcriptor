import { useEffect, useRef, useState } from "react";
import { api, type CollectionDetail, type CollectionVideo } from "../lib/api";
import { describeError, humanMessage } from "../lib/errors";
import { OUTPUT_MODES, formatNumber } from "../lib/format";
import { navigate } from "../lib/router";
import ErrorBox from "./ErrorBox";
import MarkdownView from "./MarkdownView";
import { StepList, finalize, type Step, type StepState } from "./ProgressSteps";
import StatusBadge from "./StatusBadge";
import Thumbnail from "./Thumbnail";

const TERMINAL = new Set(["completed", "failed"]);
const POLL_MS = 1500;

const STAGE_LABELS: Record<string, { label: string; unit?: string }> = {
  extracting: { label: "Analizando cada vídeo", unit: "vídeo" },
  consolidating: { label: "Consolidando fuentes", unit: undefined },
  writing: { label: "Redactando el documento consolidado", unit: "sección" },
  verifying: { label: "Verificando fidelidad y procedencia", unit: "sección" },
};

/** Steps derived only from real backend state (status + progress). */
function buildCollectionSteps(c: CollectionDetail): Step[] {
  const p = c.progress ?? {};
  const status = c.status;
  const steps: Step[] = [];
  const wantsIndividual = c.output_mode !== "consolidated";
  const wantsConsolidated = c.output_mode !== "individual";
  const pastIndividual = p.phase !== undefined && p.phase !== "individual";

  if (wantsIndividual) {
    const total = p.individual_total ?? c.videos.filter((v) => v.document_id !== null).length;
    const done = p.individual_done ?? 0;
    const state: StepState =
      status === "completed" || pastIndividual ? "done" : p.phase === "individual" ? "active" : "pending";
    steps.push({ key: "individual", label: "Documentos individuales", detail: `${done} de ${total}`, state });
  }
  if (!wantsConsolidated) return finalize(steps, status);

  const transcriptsDone = status === "completed" || (p.phase === "consolidated" && status !== "fetching_transcript");
  steps.push({
    key: "transcripts",
    label: transcriptsDone ? "Transcripciones listas" : "Obteniendo transcripciones…",
    detail: typeof p.videos_ready === "number" ? `${p.videos_ready} de ${c.video_count}` : undefined,
    state: transcriptsDone ? "done" : status === "fetching_transcript" ? "active" : "pending",
  });

  if (p.cache_hit) {
    steps.push({ key: "cache", label: "Documento consolidado reutilizado", detail: "mismos vídeos, transcripciones y configuración", state: "done" });
    return finalize(steps, status);
  }

  const stages = p.stages ?? ["extracting", "consolidating", "writing", "verifying"];
  const pastAI = status === "generating_files" || status === "completed" || p.stage === "files";
  const current = p.stage ? stages.indexOf(p.stage) : -1;
  stages.forEach((stage, i) => {
    const meta = STAGE_LABELS[stage] ?? { label: stage };
    let state: StepState = "pending";
    if (pastAI) state = "done";
    else if (status === "processing" && p.phase === "consolidated" && current >= 0)
      state = i < current ? "done" : i === current ? "active" : "pending";
    let detail: string | undefined;
    if (state === "active" && meta.unit && p.stage_total) detail = `${meta.unit} ${p.stage_done ?? 0} de ${p.stage_total}`;
    if (stage === "extracting" && p.extraction_cache_hits)
      detail = [detail, `${p.extraction_cache_hits} reutilizados de caché`].filter(Boolean).join(" · ");
    if (stage === "consolidating" && state === "active") detail = "conceptos, coincidencias y diferencias";
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

const VIDEO_STATUS: Record<string, string> = {
  pending: "En cola",
  document_ready: "Documento listo",
  transcript_ready: "Transcripción lista",
  extracted: "Analizado",
  included: "Incluido",
  failed: "No procesado",
};

export default function CollectionPage({ id }: { id: number }) {
  const [collection, setCollection] = useState<CollectionDetail | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [regenerating, setRegenerating] = useState(false);
  const documentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const load = async () => {
      try {
        const data = await api.getCollection(id);
        if (cancelled) return;
        setCollection(data);
        setLoadError(null);
        if (!TERMINAL.has(data.status)) timer = window.setTimeout(load, POLL_MS);
      } catch (error) {
        if (cancelled) return;
        setLoadError(error);
        timer = window.setTimeout(load, POLL_MS * 3);
      }
    };
    load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [id]);

  if (!collection) {
    return (
      <div className="mx-auto max-w-2xl pt-16">
        {loadError ? <ErrorBox {...describeError(loadError)} /> : <p className="text-zinc-500">Cargando…</p>}
      </div>
    );
  }

  const c = collection;
  const completed = c.status === "completed";
  const failed = c.status === "failed";
  const consolidated = c.output_mode !== "individual";
  const modeLabel = OUTPUT_MODES.find((m) => m.value === c.output_mode)?.label ?? c.output_mode;
  const title = (consolidated && c.title) || `Colección de ${c.video_count} vídeos`;

  async function regenerate() {
    setRegenerating(true);
    try {
      const created = await api.createCollection({
        urls: c.videos.map((v) => v.input_url),
        document_type: c.document_type,
        output_language: c.output_language,
        output_mode: c.output_mode,
        force_regenerate: true,
      });
      navigate(`/collections/${created.id}`);
    } catch {
      setRegenerating(false);
    }
  }

  return (
    <div className="pt-6 sm:pt-12">
      <section className="mx-auto max-w-3xl">
        <p className="text-xs font-semibold uppercase tracking-wider text-accent">Colección</p>
        <h1 className="mt-1 text-2xl font-semibold leading-tight tracking-tight sm:text-3xl">{title}</h1>
        <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-zinc-500">Vídeos</dt>
          <dd>{formatNumber(c.video_count)}</dd>
          <dt className="text-zinc-500">Salida</dt>
          <dd>{modeLabel}</dd>
          <dt className="text-zinc-500">Tipo</dt>
          <dd>
            {c.document_type_label} · {c.output_language.toUpperCase()}
          </dd>
          <dt className="text-zinc-500">Estado</dt>
          <dd>
            {completed ? (
              <span className="font-medium text-emerald-700 dark:text-emerald-400">✓ Completado</span>
            ) : failed ? (
              <span className="font-medium text-red-600 dark:text-red-400">✕ No se pudo completar</span>
            ) : (
              <span className="font-medium text-amber-600 dark:text-amber-400">● Procesando…</span>
            )}
          </dd>
        </dl>

        {!completed && (
          <div className="mt-8 rounded-2xl border border-zinc-200 p-6 dark:border-zinc-800">
            <StepList steps={buildCollectionSteps(c)} />
            {failed && c.error && (
              <div className="mt-6">
                <ErrorBox title={humanMessage(c.error.code)} detail={c.error.detail ?? null} />
              </div>
            )}
          </div>
        )}

        {completed && consolidated && (
          <nav aria-label="Acciones del documento consolidado" className="mt-8 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => documentRef.current?.scrollIntoView({ behavior: "smooth" })}
              className={`${BASE} ${PRIMARY}`}
            >
              LEER DOCUMENTO
            </button>
            {c.downloads.docx && <DownloadLink href={c.downloads.docx}>DESCARGAR DOCX</DownloadLink>}
            {c.downloads.pdf && <DownloadLink href={c.downloads.pdf}>DESCARGAR PDF</DownloadLink>}
            {c.downloads.markdown && <DownloadLink href={c.downloads.markdown}>DESCARGAR MARKDOWN</DownloadLink>}
            {c.downloads.txt && <DownloadLink href={c.downloads.txt}>DESCARGAR TXT</DownloadLink>}
          </nav>
        )}
        {completed && c.progress.cache_hit && (
          <p className="mt-4 text-sm text-zinc-500">
            Documento consolidado reutilizado de una generación anterior con los mismos vídeos y configuración.{" "}
            <button
              onClick={regenerate}
              disabled={regenerating}
              className="font-medium text-zinc-900 underline underline-offset-4 disabled:opacity-50 dark:text-zinc-100"
            >
              {regenerating ? "Regenerando…" : "Regenerar sin caché"}
            </button>
          </p>
        )}

        <section className="mt-10" aria-labelledby="sources-heading">
          <h2 id="sources-heading" className="text-sm font-semibold uppercase tracking-wider text-zinc-500">
            Fuentes
          </h2>
          <ol className="mt-3 divide-y divide-zinc-200 dark:divide-zinc-800">
            {/* Backend order: first appearance in the consolidated document (or canonical), never the paste order. */}
            {c.videos.map((v, i) => (
              <SourceRow key={v.video.video_id} number={i + 1} video={v} showDocument={c.output_mode !== "consolidated"} />
            ))}
          </ol>
        </section>
      </section>

      {completed && c.markdown && (
        <div ref={documentRef} className="mx-auto mt-14 max-w-3xl scroll-mt-6 border-t border-zinc-200 pt-10 dark:border-zinc-800">
          <MarkdownView markdown={c.markdown} />
        </div>
      )}
    </div>
  );
}

function SourceRow({ video: v, number, showDocument }: { video: CollectionVideo; number: number; showDocument: boolean }) {
  const failed = v.status === "failed";
  return (
    <li className="flex gap-4 py-3">
      <Thumbnail src={v.video.thumbnail} className="w-24 shrink-0 sm:w-28" />
      <div className="min-w-0 flex-1 text-sm">
        <p className="line-clamp-2 font-medium leading-snug">
          <span className="text-zinc-400">{number}. </span>
          {v.video.title ?? v.video.video_id}
        </p>
        <p className="mt-0.5 truncate text-zinc-500">
          {[v.video.channel, VIDEO_STATUS[v.status] ?? v.status, v.extraction_cached ? "análisis reutilizado de caché" : null]
            .filter(Boolean)
            .join(" · ")}
        </p>
        {failed && v.error && <p className="mt-1 text-red-600 dark:text-red-400">{humanMessage(v.error.code)}</p>}
        <p className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
          {showDocument && v.document_id !== null && (
            <a href={`#/documents/${v.document_id}`} className="inline-flex items-center gap-2 font-medium underline underline-offset-4">
              Ver documento {v.document_status && <StatusBadge status={v.document_status} />}
            </a>
          )}
          <a href={`#/transcript/${v.video.video_id}`} className="text-zinc-600 underline underline-offset-4 dark:text-zinc-400">
            Transcripción
          </a>
          <a href={v.video.url} target="_blank" rel="noopener noreferrer" className="text-zinc-600 underline underline-offset-4 dark:text-zinc-400">
            YouTube ↗
          </a>
        </p>
      </div>
    </li>
  );
}

const BASE = "inline-flex items-center rounded-xl px-4 py-2.5 text-xs font-semibold tracking-wide transition focus-visible:outline-2";
const PRIMARY = "bg-zinc-900 text-white hover:bg-zinc-700 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-200";
const SECONDARY =
  "border border-zinc-300 text-zinc-800 hover:border-zinc-900 hover:text-zinc-950 dark:border-zinc-700 dark:text-zinc-200 dark:hover:border-zinc-300";

function DownloadLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={api.downloadUrl(href)} download="" className={`${BASE} ${SECONDARY}`}>
      {children}
    </a>
  );
}
