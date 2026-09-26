import { useEffect, useState } from "react";
import { api, type CollectionSummary, type DocumentSummary } from "../lib/api";
import { formatDate } from "../lib/format";
import StatusBadge from "./StatusBadge";
import Thumbnail from "./Thumbnail";

const LIMIT = 12;

type Entry = { kind: "document"; item: DocumentSummary } | { kind: "collection"; item: CollectionSummary };

const KIND_BADGE = "rounded px-1.5 py-0.5 text-[0.65rem] font-semibold uppercase tracking-wider";

export default function RecentDocuments() {
  const [entries, setEntries] = useState<Entry[] | null>(null);

  useEffect(() => {
    Promise.all([api.listDocuments(LIMIT).catch(() => []), api.listCollections(LIMIT).catch(() => [])]).then(
      ([docs, collections]) => {
        const merged: Entry[] = [
          ...docs.map((item) => ({ kind: "document" as const, item })),
          ...collections.map((item) => ({ kind: "collection" as const, item })),
        ];
        // ISO timestamps sort lexicographically.
        merged.sort((a, b) => (a.item.created_at < b.item.created_at ? 1 : a.item.created_at > b.item.created_at ? -1 : 0));
        setEntries(merged.slice(0, LIMIT));
      },
    );
  }, []);

  if (!entries || entries.length === 0) return null;

  return (
    <section className="mx-auto mt-24 max-w-2xl" aria-labelledby="recent-heading">
      <h2 id="recent-heading" className="text-sm font-semibold uppercase tracking-wider text-zinc-500">
        Documentos recientes
      </h2>
      <ul className="mt-4 divide-y divide-zinc-200 dark:divide-zinc-800">
        {entries.map((entry) =>
          entry.kind === "document" ? (
            <DocumentRow key={`d${entry.item.id}`} doc={entry.item} />
          ) : (
            <CollectionRow key={`c${entry.item.id}`} collection={entry.item} />
          ),
        )}
      </ul>
    </section>
  );
}

function DocumentRow({ doc }: { doc: DocumentSummary }) {
  return (
    <li>
      <a
        href={`#/documents/${doc.id}`}
        className="-mx-3 flex items-center gap-4 rounded-xl px-3 py-3 transition hover:bg-zinc-50 dark:hover:bg-zinc-900"
      >
        <Thumbnail src={doc.video.thumbnail} className="w-24 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{doc.video.title ?? doc.title ?? doc.video.video_id}</p>
          <p className="mt-0.5 text-sm text-zinc-500">
            <span className={`${KIND_BADGE} mr-1.5 bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400`}>Documento</span>
            {doc.document_type_label} · {doc.output_language.toUpperCase()} · {formatDate(doc.created_at)}
          </p>
        </div>
        <StatusBadge status={doc.status} />
      </a>
    </li>
  );
}

function CollectionRow({ collection: c }: { collection: CollectionSummary }) {
  const thumbs = c.videos.slice(0, 3);
  const title = (c.output_mode !== "individual" && c.title) || c.videos[0]?.video.title || `Colección de ${c.video_count} vídeos`;
  return (
    <li>
      <a
        href={`#/collections/${c.id}`}
        className="-mx-3 flex items-center gap-4 rounded-xl px-3 py-3 transition hover:bg-zinc-50 dark:hover:bg-zinc-900"
      >
        <div className="relative w-24 shrink-0" aria-hidden="true">
          {thumbs
            .slice(1)
            .reverse()
            .map((v, i) => (
              <div
                key={v.position}
                className="absolute inset-0 rounded-lg bg-zinc-200 ring-2 ring-white dark:bg-zinc-700 dark:ring-zinc-950"
                style={{ transform: `translate(${(thumbs.length - 1 - i) * 4}px, ${-(thumbs.length - 1 - i) * 4}px)` }}
              />
            ))}
          <Thumbnail src={thumbs[0]?.video.thumbnail ?? null} className="relative w-24 ring-2 ring-white dark:ring-zinc-950" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{title}</p>
          <p className="mt-0.5 text-sm text-zinc-500">
            <span className={`${KIND_BADGE} mr-1.5 bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300`}>Colección</span>
            {c.video_count} vídeos · {c.document_type_label} · {formatDate(c.created_at)}
          </p>
        </div>
        <StatusBadge status={c.status} />
      </a>
    </li>
  );
}
