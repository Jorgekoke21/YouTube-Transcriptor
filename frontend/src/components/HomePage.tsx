import { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  api,
  type BatchInspectionResult,
  type DocumentType,
  type InspectionResult,
  type OutputLanguage,
  type OutputMode,
} from "../lib/api";
import { describeError } from "../lib/errors";
import { DOCUMENT_TYPES, OUTPUT_MODES, looksLikeYouTubeUrl, parseUrlLines } from "../lib/format";
import { navigate } from "../lib/router";
import BatchInspectionList from "./BatchInspectionList";
import InspectionCard from "./InspectionCard";
import RecentDocuments from "./RecentDocuments";
import ErrorBox from "./ErrorBox";

type InspectState =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "done"; result: InspectionResult }
  | { state: "error"; error: unknown };

type BatchState =
  | { state: "idle" }
  | { state: "loading"; key: string }
  | { state: "done"; key: string; result: BatchInspectionResult }
  | { state: "error"; key: string; error: unknown };

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

export default function HomePage() {
  const [text, setText] = useState("");
  const [documentType, setDocumentType] = useState<DocumentType>("full_notes");
  const [language, setLanguage] = useState<OutputLanguage>("es");
  const [outputMode, setOutputMode] = useState<OutputMode>("consolidated");
  const [inspection, setInspection] = useState<InspectState>({ state: "idle" });
  const [batch, setBatch] = useState<BatchState>({ state: "idle" });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<unknown>(null);
  const abortRef = useRef<AbortController | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const ids = { url: useId(), type: useId(), lang: useId(), mode: useId() };

  // One line → the original single-URL flow. Several lines → batch mode.
  const parsed = useMemo(() => parseUrlLines(text), [text]);
  const isBatch = parsed.lines.length > 1;
  const url = isBatch ? "" : (parsed.lines[0] ?? "");
  const batchKey = parsed.lines.join("\n");
  const batchResult = batch.state === "done" && batch.key === batchKey ? batch.result : null;

  // Single URL: inspect shortly after the user pastes/types it (unchanged behaviour).
  useEffect(() => {
    abortRef.current?.abort();
    setSubmitError(null);
    const value = url.trim();
    if (!value || !looksLikeYouTubeUrl(value)) {
      setInspection({ state: "idle" });
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setInspection({ state: "loading" });
    const timer = window.setTimeout(() => {
      api
        .inspect(value, controller.signal)
        .then((result) => setInspection({ state: "done", result }))
        .catch((error) => {
          if (!controller.signal.aborted) setInspection({ state: "error", error });
        });
    }, 350);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [url]);

  const invalidUrl = !isBatch && url.length > 0 && !looksLikeYouTubeUrl(url);
  const canGenerateSingle =
    !isBatch && inspection.state === "done" && inspection.result.transcript_available && !submitting;

  const consolidatedWanted = outputMode !== "individual";
  const cleanNotConsolidable = consolidatedWanted && documentType === "clean_transcript";
  const minSelected = consolidatedWanted ? 2 : 1;
  const canGenerateBatch = !!batchResult && selected.size >= minSelected && !cleanNotConsolidable && !submitting;

  async function analyze() {
    const key = batchKey;
    setSubmitError(null);
    setBatch({ state: "loading", key });
    try {
      const result = await api.inspectBatch(parsed.lines);
      setSelected(new Set(result.videos.filter((v) => v.transcript_available).map((v) => v.video.video_id)));
      setBatch({ state: "done", key, result });
    } catch (error) {
      setBatch({ state: "error", key, error });
    }
  }

  async function generateSingle() {
    const doc = await api.createDocument({ url: url.trim(), document_type: documentType, output_language: language });
    navigate(`/documents/${doc.id}`);
  }

  async function generateBatch() {
    if (!batchResult) return;
    // Selected videos, in the order they were pasted.
    const urls = batchResult.videos.filter((v) => selected.has(v.video.video_id)).map((v) => v.input);
    const collection = await api.createCollection({
      urls,
      document_type: documentType,
      output_language: language,
      output_mode: outputMode,
    });
    navigate(`/collections/${collection.id}`);
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (isBatch && !batchResult) {
      if (batch.state !== "loading" && parsed.unique.length > 0) await analyze();
      return;
    }
    if (isBatch ? !canGenerateBatch : !canGenerateSingle) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await (isBatch ? generateBatch() : generateSingle());
    } catch (error) {
      setSubmitError(error);
      setSubmitting(false);
    }
  }

  function toggle(videoId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(videoId)) next.delete(videoId);
      else next.add(videoId);
      return next;
    });
  }

  function setAll(selectAll: boolean) {
    setSelected(
      selectAll && batchResult
        ? new Set(batchResult.videos.filter((v) => v.transcript_available).map((v) => v.video.video_id))
        : new Set(),
    );
  }

  const selectedHint = DOCUMENT_TYPES.find((t) => t.value === documentType)?.hint;
  const rows = Math.min(Math.max(text.split("\n").length, 1), 10);

  let buttonLabel = "GENERAR DOCUMENTO";
  if (isBatch && !batchResult) buttonLabel = batch.state === "loading" ? "ANALIZANDO VÍDEOS…" : "ANALIZAR VÍDEOS";
  else if (isBatch)
    buttonLabel =
      outputMode === "individual"
        ? `GENERAR ${plural(selected.size, "DOCUMENTO", "DOCUMENTOS")}`
        : outputMode === "both"
          ? "GENERAR DOCUMENTOS Y CONSOLIDADO"
          : "GENERAR DOCUMENTO CONSOLIDADO";
  if (submitting) buttonLabel = "INICIANDO…";
  const buttonDisabled = isBatch
    ? batchResult
      ? !canGenerateBatch
      : parsed.unique.length === 0 || batch.state === "loading"
    : !canGenerateSingle;

  return (
    <div className="pt-10 sm:pt-20">
      <section className="mx-auto max-w-2xl">
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
          YouTube <span className="text-accent">→</span> Document
        </h1>
        <p className="mt-4 text-lg text-zinc-600 dark:text-zinc-400">
          Convierte vídeos de YouTube en documentos estructurados utilizando su transcripción.
        </p>

        <form ref={formRef} onSubmit={onSubmit} className="mt-10 space-y-6" noValidate>
          <div>
            <label htmlFor={ids.url} className="sr-only">
              URL del vídeo de YouTube (o varias, una por línea)
            </label>
            <textarea
              id={ids.url}
              rows={rows}
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              autoFocus
              placeholder="Pega una URL de YouTube (o varias, una por línea)"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault();
                  formRef.current?.requestSubmit();
                }
              }}
              aria-invalid={invalidUrl || undefined}
              aria-describedby={invalidUrl ? `${ids.url}-err` : isBatch ? `${ids.url}-count` : undefined}
              className="block w-full resize-none rounded-2xl border border-zinc-300 bg-white px-5 py-4 text-lg leading-snug shadow-sm placeholder:text-zinc-400 focus:border-zinc-900 focus:outline-none focus:ring-4 focus:ring-zinc-900/10 dark:border-zinc-700 dark:bg-zinc-900 dark:focus:border-zinc-300 dark:focus:ring-white/10"
            />
            {invalidUrl && (
              <p id={`${ids.url}-err`} className="mt-2 text-sm text-red-600 dark:text-red-400">
                Introduce una URL de un vídeo de YouTube (watch, youtu.be, shorts o live).
              </p>
            )}
            {isBatch && (
              <p id={`${ids.url}-count`} className="mt-2 text-sm text-zinc-600 dark:text-zinc-400" aria-live="polite">
                <strong className="font-medium text-zinc-900 dark:text-zinc-100">
                  {plural(parsed.unique.length, "URL válida detectada", "URLs válidas detectadas")}
                </strong>
                {parsed.duplicates > 0 && ` · ${plural(parsed.duplicates, "repetida", "repetidas")}`}
                {parsed.invalid.length > 0 && ` · ${plural(parsed.invalid.length, "no válida", "no válidas")}`}
              </p>
            )}
          </div>

          <div aria-live="polite">
            {!isBatch && inspection.state === "loading" && (
              <p className="text-sm text-zinc-500">Comprobando el vídeo y sus transcripciones…</p>
            )}
            {!isBatch && inspection.state === "done" && <InspectionCard result={inspection.result} />}
            {!isBatch && inspection.state === "error" && <ErrorBox {...describeError(inspection.error)} />}

            {isBatch && batch.state === "loading" && batch.key === batchKey && (
              <p className="text-sm text-zinc-500">
                Analizando {plural(parsed.unique.length, "vídeo", "vídeos")} y sus transcripciones…
              </p>
            )}
            {isBatch && batch.state === "error" && batch.key === batchKey && <ErrorBox {...describeError(batch.error)} />}
            {batchResult && (
              <BatchInspectionList result={batchResult} selected={selected} onToggle={toggle} onSetAll={setAll} />
            )}
          </div>

          {batchResult && (
            <fieldset>
              <legend className="mb-1.5 block text-sm font-medium">Salida</legend>
              <div className="grid gap-2 sm:grid-cols-3">
                {OUTPUT_MODES.map((m) => (
                  <label
                    key={m.value}
                    className={`cursor-pointer rounded-xl border px-3 py-2.5 text-sm transition ${
                      outputMode === m.value
                        ? "border-zinc-900 bg-zinc-50 dark:border-zinc-300 dark:bg-zinc-900"
                        : "border-zinc-300 dark:border-zinc-700"
                    }`}
                  >
                    <input
                      type="radio"
                      name={ids.mode}
                      value={m.value}
                      checked={outputMode === m.value}
                      onChange={() => setOutputMode(m.value)}
                      className="sr-only"
                    />
                    <span className="block font-medium">{m.label}</span>
                    <span className="mt-0.5 block text-zinc-500">{m.hint}</span>
                  </label>
                ))}
              </div>
              {consolidatedWanted && selected.size < 2 && (
                <p className="mt-2 text-sm text-amber-700 dark:text-amber-400">
                  El documento consolidado necesita al menos dos vídeos seleccionados.
                </p>
              )}
              {cleanNotConsolidable && (
                <p className="mt-2 text-sm text-amber-700 dark:text-amber-400">
                  La transcripción limpia solo está disponible como documentos individuales.
                </p>
              )}
            </fieldset>
          )}

          <div className="grid gap-4 sm:grid-cols-[2fr_1fr]">
            <div>
              <label htmlFor={ids.type} className="mb-1.5 block text-sm font-medium">
                Tipo de documento
              </label>
              <select
                id={ids.type}
                value={documentType}
                onChange={(e) => setDocumentType(e.target.value as DocumentType)}
                className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2.5 dark:border-zinc-700 dark:bg-zinc-900"
              >
                {DOCUMENT_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
              <p className="mt-1.5 text-sm text-zinc-500">{selectedHint}</p>
            </div>
            <div>
              <label htmlFor={ids.lang} className="mb-1.5 block text-sm font-medium">
                Idioma del documento
              </label>
              <select
                id={ids.lang}
                value={language}
                onChange={(e) => setLanguage(e.target.value as OutputLanguage)}
                className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2.5 dark:border-zinc-700 dark:bg-zinc-900"
              >
                <option value="es">Español</option>
                <option value="en">English</option>
              </select>
            </div>
          </div>

          {submitError !== null && <ErrorBox {...describeError(submitError)} />}

          <button
            type="submit"
            disabled={buttonDisabled}
            className="w-full rounded-2xl bg-zinc-900 px-6 py-4 text-sm font-semibold tracking-wide text-white transition hover:bg-zinc-700 disabled:cursor-not-allowed disabled:bg-zinc-300 disabled:text-zinc-500 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-200 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500"
          >
            {buttonLabel}
          </button>
        </form>
      </section>

      <RecentDocuments />
    </div>
  );
}
