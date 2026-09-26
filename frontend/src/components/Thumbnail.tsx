import { useState } from "react";

// Thumbnails are optional metadata: a neutral placeholder is shown when missing or broken.
export default function Thumbnail({ src, className = "" }: { src: string | null; className?: string }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className={`aspect-video overflow-hidden rounded-lg bg-zinc-100 dark:bg-zinc-800 ${className}`}>
      {src && !failed ? (
        <img src={src} alt="" loading="lazy" className="h-full w-full object-cover" onError={() => setFailed(true)} />
      ) : (
        <div className="flex h-full w-full items-center justify-center text-zinc-400" aria-hidden="true">
          ▶
        </div>
      )}
    </div>
  );
}
