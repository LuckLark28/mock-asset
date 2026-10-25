import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

function normalizeBase(value: string | undefined) {
  const raw = (value || "/").trim() || "/";
  return raw.endsWith("/") ? raw : `${raw}/`;
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const base = normalizeBase(env.VITE_BASE_PATH);
  const prefix = base.replace(/\/$/, "");
  const apiPrefix = `${prefix}/api`;

  return {
    base,
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        [apiPrefix]: {
          target: "http://127.0.0.1:8000",
          rewrite: (path) => (prefix ? path.replace(prefix, "") : path),
        },
      },
    },
  };
});
