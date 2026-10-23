import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/mock-asset/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/mock-asset/api": {
        target: "http://127.0.0.1:8000",
        rewrite: (path) => path.replace(/^\/mock-asset/, ""),
      },
    },
  },
});
