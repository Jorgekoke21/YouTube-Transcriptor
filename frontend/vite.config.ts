import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Ports are read from the repository-level .env (BACKEND_PORT / FRONTEND_PORT).
// Only these two values are read here; the OpenAI key is never exposed to the frontend.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "..", "");
  const backendPort = env.BACKEND_PORT || "8000";
  const frontendPort = Number(env.FRONTEND_PORT || 5173);
  return {
    plugins: [react(), tailwindcss()],
    envDir: "..",
    envPrefix: "VITE_",
    server: {
      port: frontendPort,
      proxy: {
        "/api": { target: `http://127.0.0.1:${backendPort}`, changeOrigin: true },
      },
    },
  };
});
