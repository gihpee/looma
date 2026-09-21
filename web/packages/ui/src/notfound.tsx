/** 404 — одна на три поверхности. */
import { Mark } from "./mark";

export function NotFound({ home = "/", console: consoleUrl, title = "Страница не найдена" }: {
  home?: string; console?: string; title?: string;
}) {
  const path = typeof location !== "undefined" ? location.pathname : "";
  return (
    <main className="lu-404">
      <div className="lu-404__box">
        <Mark size={36} />
        <span className="lu-404__code">404</span>
        <h1>{title}</h1>
        <div className="lu-404__cta">
          <a className="lu-btn lu-btn--primary" href={home}>На главную</a>
          {consoleUrl && <a className="lu-btn lu-btn--secondary" href={consoleUrl}>В консоль</a>}
        </div>
        {path && <code className="lu-404__path">{path}</code>}
      </div>
    </main>
  );
}
