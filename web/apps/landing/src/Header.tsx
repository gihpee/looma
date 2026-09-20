/** Шапка: announcement-бар с ценой из прайса, навигация с мега-меню на
 *  десктопе и drawer на телефоне. Ссылки в консоль — абсолютные: это другой
 *  поддомен. */
import { useEffect, useRef, useState } from "react";
import { ArrowRight, ChevronDown, ExternalLink, Menu } from "lucide-react";
import type { PublicPricing } from "@looma/api";
import { Drawer, IconButton, LinkButton, Mark, ThemeToggle, rubles, useDesktop } from "@looma/ui";
import { cheapestClass, cheapestModel, kop, timesCheaper, cheapestRival, fmtTimes } from "./pricing";

export const CONSOLE = `https://console.${location.hostname.replace(/^www\./, "")}`;
export const DOCS = "/docs";

const MENU = {
  compute: [
    { href: "#compute", title: "Ray-кластер", text: "Поднимите кластер на N узлов на несколько часов. Платите за GPU-час, пока он держит ресурс." },
    { href: "#fit", title: "Что здесь поедет, а что нет", text: "Честная таблица: независимые куски и конвейер по слоям — да, тензорный параллелизм — нет." },
    { href: "#prices", title: "Цены на кластер", text: "По классам карт, рядом с ценами AWS и Selectel." },
  ],
  intelligence: [
    { href: "#inference", title: "Инференс", text: "OpenAI-совместимый API и чат. Цена за токен, метрики каждого ответа." },
    { href: "#models", title: "Модели", text: "Каталог платформенных моделей — и любая своя с HuggingFace, без заявок." },
    { href: "#training", title: "Обучение", text: "LoRA на ваших данных: датасет, база, точность — и адаптер сразу в деплой." },
    { href: "#keys", title: "Ключи API", text: "base_url, curl и python — рядом с ключом." },
  ],
  network: [
    { href: "#owners", title: "Подключить машину", text: "Одна команда с ключом. Без входящих портов и настройки роутера." },
    { href: "#how", title: "Как устроено", text: "Три факта об архитектуре вместо обещаний." },
  ],
};

export function Announcement({ pricing }: { pricing?: PublicPricing | null }) {
  const gpu = cheapestClass(pricing);
  const model = cheapestModel(pricing);
  if (!gpu && !model) return null;
  const rival = gpu ? cheapestRival(gpu) : null;
  const times = gpu && rival ? timesCheaper(gpu, rival.key) : null;
  return (
    <div className="lp-ann">
      <span>
        {gpu && <>{gpu.name} от <b>{rubles(kop(gpu.rate_kopecks))}/час</b></>}
        {gpu && model && <span className="lu-hide-mobile"> · </span>}
        {model && <span className="lu-hide-mobile">инференс от <b>{rubles(kop(model.price_in))} за 1M токенов</b></span>}
        {times && times > 1.2 && <> — в {fmtTimes(times)} раза дешевле {pricing?.competitors?.[rival!.key] ?? rival!.key}</>}
      </span>
      <a href="#prices">Смотреть цены <ArrowRight size={14} /></a>
    </div>
  );
}

export function Header() {
  const desktop = useDesktop();
  const [mega, setMega] = useState(false);
  const [menu, setMenu] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!mega) return;
    const onDoc = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setMega(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMega(false); };
    addEventListener("mousedown", onDoc); addEventListener("keydown", onKey);
    return () => { removeEventListener("mousedown", onDoc); removeEventListener("keydown", onKey); };
  }, [mega]);

  return (
    <header className="lp-head" ref={ref}>
      <a href="/" className="lp-head__brand"><Mark size={26} />Looma Float</a>
      <nav className="lp-head__nav" aria-label="Разделы">
        <button type="button" aria-expanded={mega} aria-controls="lp-mega" onClick={() => setMega((v) => !v)}>Продукты <ChevronDown size={14} /></button>
        <a href="#how">Как устроено</a>
        <a href="#prices">Цены</a>
        <a href={DOCS}>Документация <ExternalLink size={13} /></a>
      </nav>
      <div className="lp-head__cta">
        <ThemeToggle />
        <a className="lu-btn lu-btn--ghost lp-login" href={CONSOLE}>Войти</a>
        <LinkButton kind="primary" href={CONSOLE}>Начать</LinkButton>
        <IconButton label="Меню" kind="ghost" className="lp-head__burger" onClick={() => setMenu(true)}><Menu size={22} /></IconButton>
      </div>

      {desktop && mega && (
        <div className="lp-mega" id="lp-mega" role="region" aria-label="Продукты">
          <div className="lp-mega__col">
            <div className="lp-mega__title"><span className="lu-logo">c</span><div><b>looma-compute</b><small>свой код на распределённых картах</small></div></div>
            {MENU.compute.map((m) => <a key={m.href} className="lp-mega__item" href={m.href} onClick={() => setMega(false)}><b>{m.title}</b><span>{m.text}</span></a>)}
          </div>
          <div className="lp-mega__col">
            <div className="lp-mega__title"><span className="lu-logo">i</span><div><b>looma-intelligence</b><small>инференс и обучение LLM</small></div></div>
            {MENU.intelligence.map((m) => <a key={m.href} className="lp-mega__item" href={m.href} onClick={() => setMega(false)}><b>{m.title}</b><span>{m.text}</span></a>)}
          </div>
          <div className="lp-mega__col">
            <div className="lp-mega__title"><span className="lu-logo" style={{ background: "var(--surface-2)", color: "var(--text-2)" }}>n</span><div><b style={{ fontFamily: "var(--font-sans)" }}>Сеть</b><small>домашние машины, одно исходящее соединение</small></div></div>
            {MENU.network.map((m) => <a key={m.href} className="lp-mega__item" href={m.href} onClick={() => setMega(false)}><b>{m.title}</b><span>{m.text}</span></a>)}
            <div className="lp-mega__docs">
              <span className="lu-label">Документация</span>
              <span>Быстрый старт: ключ → первый запрос → первый кластер за 10 минут.</span>
              <a href={DOCS}>Открыть →</a>
            </div>
          </div>
        </div>
      )}

      {menu && (
        <Drawer title="Меню" onClose={() => setMenu(false)} footer={
          <div className="lu-stack" style={{ width: "100%" }}>
            <a className="lu-btn lu-btn--secondary lu-btn--block lu-btn--lg" href={CONSOLE}>Войти</a>
            <a className="lu-btn lu-btn--primary lu-btn--block lu-btn--lg" href={CONSOLE}>Начать</a>
          </div>}>
          <nav className="lp-menu" onClick={() => setMenu(false)}>
            <div className="lp-menu__group">looma-compute</div>
            {MENU.compute.map((m) => <a key={m.href} href={m.href}>{m.title}<small>{m.text}</small></a>)}
            <div className="lp-menu__group">looma-intelligence</div>
            {MENU.intelligence.map((m) => <a key={m.href} href={m.href}>{m.title}<small>{m.text}</small></a>)}
            <div className="lp-menu__group lp-menu__group--plain">Сеть</div>
            {MENU.network.map((m) => <a key={m.href} href={m.href}>{m.title}<small>{m.text}</small></a>)}
            <a href="#prices">Цены</a>
            <a href={DOCS}>Документация</a>
          </nav>
        </Drawer>
      )}
    </header>
  );
}
