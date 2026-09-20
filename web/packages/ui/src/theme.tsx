/** Тема: светлая по умолчанию, тёмная — по выбору.
 *
 *  Атрибут data-theme на <html> ставится ещё в index.html до первой отрисовки
 *  (иначе страница мигает). Здесь — только чтение и переключение; источник
 *  правды один: localStorage.looma_theme, а системная тема — лишь подсказка
 *  для первого визита. */
import { useCallback, useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

export type Theme = "light" | "dark";
const KEY = "looma_theme";

function current(): Theme {
  if (typeof document === "undefined") return "light";
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(current);
  const set = useCallback((t: Theme) => {
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem(KEY, t); } catch { /* приватный режим */ }
    setTheme(t);
  }, []);
  useEffect(() => {
    // Другая вкладка переключила тему — подхватываем, не дожидаясь перезагрузки.
    const on = (e: StorageEvent) => {
      if (e.key === KEY && (e.newValue === "dark" || e.newValue === "light")) {
        document.documentElement.dataset.theme = e.newValue;
        setTheme(e.newValue);
      }
    };
    addEventListener("storage", on);
    return () => removeEventListener("storage", on);
  }, []);
  return [theme, set];
}

export function ThemeToggle({ className = "" }: { className?: string }) {
  const [theme, set] = useTheme();
  const dark = theme === "dark";
  return (
    <button type="button" className={`lu-btn lu-btn--secondary lu-iconbtn ${className}`}
            aria-label={dark ? "Светлая тема" : "Тёмная тема"} aria-pressed={dark}
            onClick={() => set(dark ? "light" : "dark")}>
      {dark ? <Sun size={17} /> : <Moon size={17} />}
    </button>
  );
}

/** Телефон ли это. Ширина, а не userAgent: список устройств устаревает молча,
 *  а поворот экрана меняет ответ — поэтому подписка обязательна. */
export function useMedia(query: string) {
  const [ok, setOk] = useState(() => typeof matchMedia === "function" && matchMedia(query).matches);
  useEffect(() => {
    const mq = matchMedia(query);
    const on = () => setOk(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return ok;
}
export const usePhone = () => useMedia("(max-width: 767px)");
export const useDesktop = () => useMedia("(min-width: 1024px)");

export const calmMotion = () =>
  typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
