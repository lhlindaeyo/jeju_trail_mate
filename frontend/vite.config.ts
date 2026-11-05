import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /v1 and /health are proxied to the FastAPI backend during development.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/v1": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
});
