import { useRoute } from "./lib/router";
import HomePage from "./components/HomePage";
import DocumentPage from "./components/DocumentPage";
import CollectionPage from "./components/CollectionPage";
import TranscriptPage from "./components/TranscriptPage";

export default function App() {
  const route = useRoute();
  return (
    <div className="min-h-dvh">
      <header className="mx-auto flex max-w-5xl items-center justify-between px-5 py-5 sm:px-8">
        <a href="#/" className="text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
          YouTube <span className="text-accent">→</span> Document
        </a>
        {route.name !== "home" && (
          <a href="#/" className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
            Nuevo documento
          </a>
        )}
      </header>
      <main className="mx-auto max-w-5xl px-5 pb-24 sm:px-8">
        {route.name === "home" && <HomePage />}
        {route.name === "document" && <DocumentPage key={route.id} id={route.id} />}
        {route.name === "collection" && <CollectionPage key={route.id} id={route.id} />}
        {route.name === "transcript" && (
          <TranscriptPage key={route.videoId} videoId={route.videoId} documentId={route.documentId} />
        )}
      </main>
    </div>
  );
}
