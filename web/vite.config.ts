import { resolve } from "node:path";
import { defineConfig } from "vite";

const root = import.meta.dirname;

export default defineConfig({
  root,
  publicDir: "public",
  server: {
    // `npm run dev` serves the operator console against the local console API (`rootpath ui`).
    proxy: { "/api": "http://localhost:7100" },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        index: resolve(root, "index.html"),
        console: resolve(root, "console.html"),
      },
    },
  },
});
