import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API = "http://127.0.0.1:8000";

// Mini App живёт по адресу /app/ на том же домене, что и API.
// При разработке туннель cloudflared смотрит на этот dev-сервер,
// а он проксирует API, вебхуки ботов и /health в бэкенд.
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    allowedHosts: [".trycloudflare.com"],
    proxy: {
      "/api": API,
      "/tg": API,
      "/health": API,
    },
  },
});
