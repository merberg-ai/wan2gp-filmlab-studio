import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies the API and event stream to the backend on 19300.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:19300",
      "/ws": { target: "ws://127.0.0.1:19300", ws: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
