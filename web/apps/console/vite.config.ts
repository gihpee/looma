import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Одна из трёх сборок за web-nginx (см. web/nginx). Пути API проксируются
// ровно так же, как это делает nginx для этого хоста, чтобы правка фронта не
// требовала пересборки образа.
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // Вендор отдельно от приложения: React и роутер меняются реже, чем
        // экраны, и переживают деплой в кэше браузера.
        manualChunks: { vendor: ["react", "react-dom", "react-router-dom", "@tanstack/react-query"] },
      },
    },
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8000", "/v1": "http://127.0.0.1:8000" },
  },
});
