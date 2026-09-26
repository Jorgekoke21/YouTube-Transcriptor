import { useEffect, useRef, useState } from "react";
import { api, type DocumentDetail } from "../lib/api";
import { describeError, humanMessage } from "../lib/errors";
import { navigate } from "../lib/router";
import ErrorBox from "./ErrorBox";
import MarkdownView from "./MarkdownView";
import ProgressSteps from "./ProgressSteps";
import Thumbnail from "./Thumbnail";

const TERMINAL = new Set(["completed", "failed"]);
const POLL_MS = 1500;

export default function DocumentPage({ id }: { id: number }) {
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [regenerating, setRegenerating] = useState(false);
  const documentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const load = async () => {
      try {
        const data = await api.getDocument(id);
        if (cancelled) return;
        setDoc(data);
        setLoadError(null);
        if (!TERMINAL.has(data.status)) timer = window.setTimeout(load, POLL_MS);
      } catch (error) {
        if (cancelled) return;
        setLoadError(error);
        // Keep polling through transient network errors while processing.
        timer = window.setTimeout(load, POLL_MS * 3);
      }
    };
    load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [id]);

  if (!doc) {
    return (
      <div className="mx-auto max-w-2xl pt-16">
        {loadError ? <ErrorBox {...describeError(loadError)} /> : <p className="text-zinc-500">Cargando…</p>}
      </div>
    );
  }

  const video = doc.video;
  const completed = doc.status === "completed";
  const failed = doc.status === "failed";

  async function regenerate() {
    setRegenerating(true);
    try {
      const created = await api.createDocument({
        url: video.url,
        document_type: doc!.document_type,
        output_language: doc!.output_language,
        force_regenerate: true,
      });
      navigate(`/documents/${created.id}`);
    } catch {
      setRegenerating(false);
    }
  }

  return (
    <div className="pt-6 sm:pt-12">
      <section className="mx-auto max-w-3xl">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-start">
          <Thumbnail src={video.thumbnail} className="w-full sm:w-56 sm:shrink-0" />
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold leading-tight tracking-tight sm:text-3xl">
              {video.title ?? doc.title ?? video.video_id}
            </h1>
            {video.channel && <p className="mt-1 text-zinc-500">{video.channel}</p>}
            <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-zinc-500">Tipo</dt>
              <dd>{doc.document_type_label}</dd>
              <dt className="text-zinc-500">Estado</dt>
              <dd>
                {completed ? (
                  <span className="font-medium text-emerald-700 dark:text-emerald-400">✓ Documento generado</span>
                ) : failed ? (
                  <span className="font-medium text-red-600 dark:text-red-400">✕ No se pudo generar</span>
                ) : (
                  <span className="font-medium text-amber-600 dark:text-amber-400">● Procesando…</span>
                )}
              </dd>
            </dl>
          </div>
        </div>

        {!completed && (
          <div className="mt-10 rounded-2xl border border-zinc-200 p-6 dark:border-zinc-800">
            <ProgressSteps doc={doc} />
            {failed && doc.error && (
              <div className="mt-6">
                <FailureMessage code={doc.error.code} detail={doc.error.detail ?? null} />
                <a href="#/" className="mt-4 inline-block text-sm font-medium underline underline-offset-4">
                  Probar con otro vídeo
                </a>
              </div>
            )}
          </div>
        )}

        {completed && (
          <nav aria-label="Acciones del documento" className="mt-8 flex flex-wrap gap-2">
            <ActionButton primary onClick={() => documentRef.current?.scrollIntoView({ behavior: "smooth" })}>
              LEER DOCUMENTO
            </ActionButton>
            {doc.downloads.docx && <ActionLink href={api.downloadUrl(doc.downloads.docx)} download>DESCARGAR DOCX</ActionLink>}
            {doc.downloads.pdf && <ActionLink href={api.downloadUrl(doc.downloads.pdf)} download>DESCARGAR PDF</ActionLink>}
            {doc.downloads.markdown && <ActionLink href={api.downloadUrl(doc.downloads.markdown)} download>DESCARGAR MARKDOWN</ActionLink>}
            {doc.downloads.txt && <ActionLink href={api.downloadUrl(doc.downloads.txt)} download>DESCARGAR TXT</ActionLink>}
            <ActionLink href={`#/transcript/${video.video_id}?doc=${doc.id}`}>VER TRANSCRIPCIÓN ORIGINAL</ActionLink>
            <ActionLink href={video.url} external>ABRIR EN YOUTUBE ↗</ActionLink>
          </nav>
        )}
        {completed && doc.progress.cache_hit && (
          <p className="mt-4 text-sm text-zinc-500">
            Reutilizado de una generación anterior con la misma configuración.{" "}
            <button onClick={regenerate} disabled={regenerating} className="font-medium text-zinc-900 underline underline-offset-4 disabled:opacity-50 dark:text-zinc-100">
              {regenerating ? "Regenerando…" : "Regenerar sin caché"}
            </button>
          </p>
        )}
      </section>

      {completed && doc.markdown && (
        <div ref={documentRef} className="mx-auto mt-14 max-w-3xl scroll-mt-6 border-t border-zinc-200 pt-10 dark:border-zinc-800">
          <MarkdownView markdown={doc.markdown} />
        </div>
      )}
    </div>
  );
}

function FailureMessage({ code, detail }: { code: string; detail: string | null }) {
  if (code === "TRANSCRIPT_NOT_AVAILABLE" || code === "TRANSCRIPT_DISABLED") {
    return (
      <div role="alert" className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 dark:border-red-900/60 dark:bg-red-950/40">
        <p className="font-semibold text-red-800 dark:text-red-300">No se ha encontrado una transcripción disponible para este vídeo.</p>
        <p className="font-semibold text-red-800 dark:text-red-300">Este vídeo no puede procesarse.</p>
      </div>
    );
  }
  return <ErrorBox title={humanMessage(code)} detail={detail} />;
}

const BASE =
  "inline-flex items-center rounded-xl px-4 py-2.5 text-xs font-semibold tracking-wide transition focus-visible:outline-2";
const PRIMARY = "bg-zinc-900 text-white hover:bg-zinc-700 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-200";
const SECONDARY =
  "border border-zinc-300 text-zinc-800 hover:border-zinc-900 hover:text-zinc-950 dark:border-zinc-700 dark:text-zinc-200 dark:hover:border-zinc-300";

function ActionButton({ children, onClick, primary }: { children: React.ReactNode; onClick: () => void; primary?: boolean }) {
  return (
    <button type="button" onClick={onClick} className={`${BASE} ${primary ? PRIMARY : SECONDARY}`}>
      {children}
    </button>
  );
}

function ActionLink({ children, href, download, external }: { children: React.ReactNode; href: string; download?: boolean; external?: boolean }) {
  return (
    <a
      href={href}
      className={`${BASE} ${SECONDARY}`}
      {...(download ? { download: "" } : {})}
      {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
    >
      {children}
    </a>
  );
}
