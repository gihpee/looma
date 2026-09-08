import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Порт свой, не 5173: рядом на той же машине крутится админка, и один занятый
// порт превращался бы в «панель показывает не то».
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5175,
    strictPort: true,
    // Панель берёт оформление из админки, а та лежит соседней папкой. Копия
    // означала бы две правды о цветах, расходящиеся с первой же правкой.
    fs: { allow: [".", "../web"] },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
