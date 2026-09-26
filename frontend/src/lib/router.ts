import { useEffect, useState } from "react";

// Minimal hash router: #/  ·  #/documents/:id  ·  #/collections/:id  ·  #/transcript/:videoId

export type Route =
  | { name: "home" }
  | { name: "document"; id: number }
  | { name: "collection"; id: number }
  | { name: "transcript"; videoId: string; documentId?: number };

export function parseRoute(hash: string): Route {
  const [path, query = ""] = hash.replace(/^#/, "").split("?");
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "documents" && /^\d+$/.test(parts[1] ?? "")) {
    return { name: "document", id: Number(parts[1]) };
  }
  if (parts[0] === "collections" && /^\d+$/.test(parts[1] ?? "")) {
    return { name: "collection", id: Number(parts[1]) };
  }
  if (parts[0] === "transcript" && parts[1]) {
    const doc = new URLSearchParams(query).get("doc");
    return { name: "transcript", videoId: parts[1], documentId: doc ? Number(doc) : undefined };
  }
  return { name: "home" };
}

export function navigate(path: string): void {
  window.location.hash = path;
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onChange = () => {
      setRoute(parseRoute(window.location.hash));
      window.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
