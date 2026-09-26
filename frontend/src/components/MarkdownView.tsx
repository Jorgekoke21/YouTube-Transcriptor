import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// "04:32", "04:32 – 05:10 ↗", or in consolidated documents "Video title · 04:32 ↗".
const TIMESTAMP_RE = /^(.+ · )?\d{1,2}:\d{2}(:\d{2})?( – \d{1,2}:\d{2}(:\d{2})?)?( ↗)?$/;

function textOf(children: React.ReactNode): string {
  if (typeof children === "string") return children;
  if (Array.isArray(children)) return children.map(textOf).join("");
  return "";
}

const components: Components = {
  a({ href, children }) {
    const label = textOf(children);
    const isTimestamp = !!href && /youtube\.com\/watch\?v=[^&]+&t=\d+s$/.test(href) && TIMESTAMP_RE.test(label.trim());
    if (isTimestamp) {
      return (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          title="Abrir el vídeo en este momento"
          className={`${label.includes(" · ") ? "" : "whitespace-nowrap "}rounded-md bg-zinc-100 px-1.5 py-0.5 font-mono text-[0.8em] font-medium text-zinc-700 no-underline transition hover:bg-red-50 hover:text-red-700 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-red-950 dark:hover:text-red-300`}
        >
          {children}
        </a>
      );
    }
    return (
      <a href={href} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    );
  },
  table({ children }) {
    return (
      <div className="overflow-x-auto">
        <table>{children}</table>
      </div>
    );
  },
};

export default function MarkdownView({ markdown }: { markdown: string }) {
  return (
    <article className="prose prose-zinc max-w-none dark:prose-invert prose-headings:tracking-tight prose-h1:text-3xl prose-h1:font-semibold prose-h2:mt-12 prose-h2:border-t prose-h2:border-zinc-200 prose-h2:pt-8 dark:prose-h2:border-zinc-800 prose-blockquote:font-normal prose-blockquote:not-italic prose-a:text-zinc-900 dark:prose-a:text-zinc-100">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {markdown}
      </ReactMarkdown>
    </article>
  );
}
