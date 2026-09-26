export default function ErrorBox({ title, detail }: { title: string; detail?: string | null }) {
  return (
    <div role="alert" className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm dark:border-red-900/60 dark:bg-red-950/40">
      <p className="font-medium text-red-800 dark:text-red-300">{title}</p>
      {detail && <p className="mt-1 text-red-700/80 dark:text-red-300/70">{detail}</p>}
    </div>
  );
}
