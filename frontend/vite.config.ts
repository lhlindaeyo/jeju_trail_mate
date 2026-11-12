import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /v1 and /health are proxied to the FastAPI backend during development (and by `vite preview`, which reuses
// these settings). `npm run share` serves the app through a Cloudflare quick tunnel (*.trycloudflare.com),
// whose host name has to be allowed explicitly.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    allowedHosts: [".trycloudflare.com"],
    proxy: {
      "/v1": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
  preview: {
    port: 5173,
    strictPort: true,
  },
});
