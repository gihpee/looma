import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

/** Что агент рассказал о себе. Поля необязательны намеренно: панель обновляется
 *  реже агента, и лишнее поле не должно её ломать. */
interface Status {
  running?: boolean;
  /** Введён ли ключ. Считает панель по файлу: без ключа агента ещё нет, и
   *  спросить об этом некого. */
  key_present?: boolean;
  /** Остановлен ли узел владельцем машины. Тоже по файлу — см. key_present. */
  paused?: boolean;
  why?: string;
  node_id?: string;
  agent_version?: string;
  device?: string;
  gpu_name?: string;
  tasks_running?: number;
  accepts_tasks?: boolean;
  refusal?: string;
  updated_at?: number;
}

// Раз в две секунды: узел меняется медленно, а панель почти всегда закрыта.
const EVERY_MS = 2000;

export function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    const tick = async () => {
      try {
        const got = await invoke<Status>("node_status");
        if (live) { setStatus(got); setError(""); }
      } catch (exc) {
        if (live) setError(String(exc));
      }
    };
    tick();
    const timer = setInterval(tick, EVERY_MS);
    return () => { live = false; clearInterval(timer); };
  }, []);

  // Свежесть считается здесь, а не в агенте: агент, который перестал писать,
  // оставил бы последнее значение навсегда — и панель показывала бы работающий
  // узел, которого нет.
  const paused = Boolean(status?.paused);
  const stale = status?.updated_at
    ? Date.now() / 1000 - status.updated_at > 30
    : false;
  const live = Boolean(status?.running) && !stale && !paused;

  const toggle = async () => {
    setBusy(true);
    try {
      await invoke("set_paused", { paused: !paused });
      // Ждём не ответа, а следующего опроса: агент уходит и возвращается сам,
      // и показывать «готово» раньше, чем это случилось, — врать.
      setStatus((was) => (was ? { ...was, paused: !paused } : was));
    } catch (exc) {
      setError(String(exc));
    } finally {
      setBusy(false);
    }
  };

  // Пока ключа нет, всё остальное показывать нечего: узел не подключён никуда
  // и по определению ничего не делает.
  if (status && status.key_present === false) {
    return <JoinForm onSaved={() => setStatus(null)} />;
  }

  return (
    <div className="panel" data-tauri-drag-region="deep">
      <header data-tauri-drag-region="deep">
        <span className={`dot ${live ? "on" : paused ? "paused" : "off"}`} />
        <b>{live ? "Узел работает"
             : paused ? "Узел остановлен"
             : "Узел не работает"}</b>
      </header>

      {error && <p className="note bad">{error}</p>}
      {!error && status?.why && <p className="note">{status.why}</p>}
      {!error && stale && (
        <p className="note bad">агент замолчал больше полуминуты назад</p>
      )}

      {status?.node_id && (
        <section data-tauri-drag-region="false">
          <Row k="узел" v={<code>{status.node_id}</code>} />
          <Row k="версия" v={status.agent_version || "—"} />
          <Row k="железо" v={status.gpu_name
            ? `${status.gpu_name} (${status.device})` : "—"} />
          <Row k="задач сейчас" v={String(status.tasks_running ?? 0)} />
          {status.accepts_tasks === false && (
            <Row k="не берёт работу" v={<span className="bad">
              {status.refusal || "причина не названа"}</span>} />
          )}
        </section>
      )}

      {/* Машина одолжена, а не отдана: владелец вправе забрать её в любую
          минуту, не разбираясь с демонами и паролями. Ключ остаётся на месте,
          поэтому обратно — то же одно нажатие. */}
      <button className="wide" disabled={busy} onClick={toggle}>
        {busy ? "минуту…" : paused ? "Запустить узел" : "Остановить узел"}
      </button>
      <p className="note">
        {paused
          ? "Работу не берёт. Ключ сохранён — включить можно в любой момент"
          : "Текущие задачи будут доведены до конца, новые узел не возьмёт"}
      </p>

      <AgentLog />

      {/* Заработок живёт у оркестратора, а не у агента: узел знает, что он
          посчитал, но не знает, во что это оценили. Отдельный разговор. */}
      <footer className="note">Баланс и выплаты — следующим шагом</footer>
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="row">
      <span>{k}</span>
      <span>{v}</span>
    </div>
  );
}


/** Первый экран: ключ узла и ничего больше.
 *
 *  Одна строка — всё, что нужно от провайдера: в ней и адрес оркестратора, и
 *  секрет этого узла. Спрашивать что-то ещё значило бы спрашивать то, что
 *  можно вывести.
 */
function JoinForm({ onSaved }: { onSaved: () => void }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await invoke("save_key", { key });
      onSaved();
    } catch (exc) {
      setError(String(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel" data-tauri-drag-region="deep">
      <header data-tauri-drag-region="deep"><b>Подключить узел</b></header>
      <p className="note">
        Вставьте ключ узла из панели Looma. В нём уже есть всё остальное —
        адрес и секрет этой машины.
      </p>
      <textarea
        className="mono" rows={4} value={key} spellCheck={false}
        placeholder="looma_…"
        onChange={(e) => setKey(e.target.value)}
        onKeyDown={(e) => {
          // Enter отправляет, перенос строки в ключе не нужен — он одной строкой.
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void save(); }
        }} />
      {error && <p className="note bad">{error}</p>}
      <button disabled={busy || !key.trim()} onClick={save}>
        {busy ? "сохраняем…" : "Подключить"}
      </button>
      <footer className="note">
        Ключ останется на этой машине и уйдёт только оркестратору.
      </footer>
    </div>
  );
}


/** Хвост лога агента прямо в панели.

 *  Владелец машины не открывает терминал и не обязан знать, что лог лежит в
 *  /Library/Logs. Когда узел не поднимается, это единственное место, где
 *  написано почему, — и до сих пор оно было доступно только через консоль.
 */
function AgentLog() {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    let live = true;
    const read = async () => {
      try {
        const got = await invoke<string>("agent_log", { lines: 200 });
        if (live) setText(got);
      } catch (exc) {
        if (live) setText(String(exc));
      }
    };
    read();
    // Пока открыт — обновляем: смотрят на него как раз тогда, когда что-то
    // происходит прямо сейчас.
    const timer = setInterval(read, 3000);
    return () => { live = false; clearInterval(timer); };
  }, [open]);

  return (
    <section data-tauri-drag-region="false">
      <button className="link" onClick={() => setOpen(!open)}>
        {open ? "скрыть журнал" : "показать журнал"}
      </button>
      {open && <pre className="log">{text || "читаем…"}</pre>}
    </section>
  );
}
