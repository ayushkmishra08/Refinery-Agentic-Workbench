import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";

/**
 * The workstation is a static bundle; everything it knows comes from the workbench API.
 * `/api` is proxied in development so the browser makes same-origin requests and no CORS
 * preflight sits between a question and its answer.
 */
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_WORKBENCH_URL ?? "http://127.0.0.1:8077",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
