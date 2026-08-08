import { defineConfig } from "vite";

// The frontend is fully decoupled from the backend; during dev it proxies
// API + WebSocket calls to the FastAPI server on :8000.
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
