/** 404 — одна на три поверхности. Честно говорит, что страницы нет (или ещё
 *  нет: документация пишется), и предлагает две дороги, а не пустой экран. */
import { Mark } from "./mark";

export function NotFound({ home = "/", console: consoleUrl, title, text }: {
  home?: string; console?: string; title?: string; text?: string;
}) {
  const path = typeof location !== "undefined" ? location.pathname : "";
  const docs = path.startsWith("/docs");
  return (
    <main className="lu-404">
      <div className="lu-404__box">
        <Mark size={36} />
        <span className="lu-404__code">404</span>
        <h1>{title ?? (docs ? "Документация ещё пишется" : "Такой страницы нет")}</h1>
        <p>{text ?? (docs
          ? "Пока её заменяют подсказки в самой консоли: у ключей — curl и python, у кластера — команда подключения, у обучения — оценка памяти до запуска. Вопросы — на support@loomafloat.ru."
          : "Адрес мог измениться или никогда не существовал. Проверьте ссылку или начните с главной.")}</p>
        <div className="lu-404__cta">
          <a className="lu-btn lu-btn--primary" href={home}>На главную</a>
          {consoleUrl && <a className="lu-btn lu-btn--secondary" href={consoleUrl}>В консоль</a>}
        </div>
        {path && <code className="lu-404__path">{path}</code>}
      </div>
    </main>
  );
}
