/** Чат: playground поверх /v1/chat/completions под cookie сессии. Ответ
 *  стримится с метриками (ток/с, TTFT, токены, стоимость по прайсу), ход
 *  рассуждения `<think>` сворачивается, история — в браузере. */
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ChevronDown, ChevronRight, Copy, Menu, Plus, RotateCcw, SlidersHorizontal, Trash2, ArrowUp, Square } from "lucide-react";
import { ApiError, stream, useModels, type ModelInfo } from "@looma/api";
import { Button, Drawer, Field, IconButton, Logo, Modal, Notice, StateBadge, Textarea, Toggle, useDesktop, useToast, money } from "@looma/ui";
import { chatStore, type ChatMessage, type ChatThread } from "../chatStore";
import "./chat.css";

/** Разделить накопленный текст на рассуждение и ответ (тег может прийти по кускам). */
function splitThink(raw: string): { think: string; answer: string; thinking: boolean } {
  const open = raw.indexOf("<think>");
  if (open === -1) return { think: "", answer: raw, thinking: false };
  const close = raw.indexOf("</think>", open);
  if (close === -1) return { think: raw.slice(open + 7), answer: "", thinking: true };
  return { think: raw.slice(open + 7, close).trim(), answer: raw.slice(close + 8).replace(/^\s+/, ""), thinking: false };
}

interface Params { system: string; temperature: number; maxTokens: number; topP: number; showThink: boolean }
const DEFAULTS: Params = { system: "", temperature: 0.7, maxTokens: 1024, topP: 0.9, showThink: true };

export function Chat() {
  const desktop = useDesktop();
  const toast = useToast();
  const [search] = useSearchParams();
  const models = useModels();
  const list = models.data?.data ?? [];
  const [threads, setThreads] = useState<ChatThread[]>(() => chatStore.list());
  const [current, setCurrent] = useState<ChatThread | null>(() => chatStore.list()[0] ?? null);
  const [model, setModel] = useState<string>(search.get("model") ?? "");
  const [params, setParams] = useState<Params>(DEFAULTS);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");           // текущий стримящийся ответ
  const [live, setLive] = useState<{ tokens: number; tps: number; ttft: number } | null>(null);
  const [error, setError] = useState("");
  const [history, setHistory] = useState(false);
  const [settings, setSettings] = useState(false);
  const [openThink, setOpenThink] = useState<Record<number, boolean>>({});
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  // Модель: из адреса, из текущего чата, иначе первая отвечающая.
  useEffect(() => {
    if (model && list.some((m) => m.id === model)) return;
    const pick = current?.model && list.some((m) => m.id === current.model) ? current.model : list[0]?.id;
    if (pick) setModel(pick);
  }, [list, current, model]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [draft, current?.messages.length]);
  useEffect(() => () => abort.current?.abort(), []);

  const info: ModelInfo | undefined = list.find((m) => m.id === model);
  const priceOf = (tokensIn: number, tokensOut: number) =>
    info?.price_in !== undefined && info?.price_out !== undefined && info.price_in !== null && info.price_out !== null
      ? Math.round((tokensIn * info.price_in + tokensOut * info.price_out) / 1_000_000) : undefined;

  const refresh = () => setThreads(chatStore.list());
  const newThread = () => { const t = chatStore.create(model); refresh(); setCurrent(t); setError(""); };
  const removeThread = (id: string) => { chatStore.remove(id); refresh(); if (current?.id === id) setCurrent(chatStore.list()[0] ?? null); };

  const send = async (text?: string) => {
    const q = (text ?? prompt).trim();
    if (!q || busy || !model) return;
    let t = current ?? chatStore.create(model);
    if (!current) { refresh(); setCurrent(t); }
    const userMsg: ChatMessage = { role: "user", content: q, at: Date.now() };
    t = { ...t, model, title: t.messages.length ? t.title : q.slice(0, 60), messages: [...t.messages, userMsg] };
    chatStore.update(t); setCurrent(t); refresh();
    setPrompt(""); setDraft(""); setError(""); setBusy(true); setLive(null);
    abort.current?.abort();
    const ctl = new AbortController(); abort.current = ctl;
    const messages = [...(params.system ? [{ role: "system", content: params.system }] : []),
      ...t.messages.map((m) => ({ role: m.role, content: m.content }))];
    const t0 = performance.now(); let first = 0; let tokens = 0; let raw = ""; type Usage = { prompt_tokens?: number; completion_tokens?: number };
    let usage: Usage | null = null;
    try {
      for await (const line of stream("/v1/chat/completions", { model, messages, stream: true, temperature: params.temperature, max_tokens: params.maxTokens, top_p: params.topP, stream_options: { include_usage: true } }, ctl.signal)) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (payload === "[DONE]") break;
        let piece: { choices?: { delta?: { content?: string; reasoning_content?: string } }[]; usage?: Usage; error?: { message: string } };
        try { piece = JSON.parse(payload); } catch { continue; }
        if (piece.error) throw new Error(piece.error.message);
        if (piece.usage) usage = piece.usage;
        const d = piece.choices?.[0]?.delta;
        const chunk = (d?.reasoning_content ? `<think>${d.reasoning_content}` : "") + (d?.content ?? "");
        if (!chunk) continue;
        if (!first) first = performance.now();
        tokens += 1; raw += chunk; setDraft(raw);
        const dt = (performance.now() - first) / 1000;
        setLive({ tokens, tps: dt > 0.2 ? tokens / dt : 0, ttft: (first - t0) / 1000 });
      }
      const { think, answer } = splitThink(raw);
      const got: Usage | null = usage;
      const outTokens = got?.completion_tokens ?? tokens;
      const inTokens = got?.prompt_tokens ?? Math.round(messages.reduce((s, m) => s + m.content.length, 0) / 4);
      const dt = first ? (performance.now() - first) / 1000 : 0;
      const reply: ChatMessage = { role: "assistant", content: answer || raw, think: think || undefined, at: Date.now(), tokens: outTokens,
        tps: dt > 0 ? outTokens / dt : undefined, ttft: first ? (first - t0) / 1000 : undefined, cost: priceOf(inTokens, outTokens) };
      const done = { ...t, messages: [...t.messages, reply] };
      chatStore.update(done); setCurrent(done); refresh();
    } catch (e) {
      if (!(e instanceof DOMException)) setError(e instanceof ApiError || e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false); setDraft(""); setLive(null);
    }
  };
  const stop = () => abort.current?.abort();
  const retry = () => {
    if (!current || busy) return;
    const msgs = [...current.messages];
    const last = msgs[msgs.length - 1];
    if (last?.role === "assistant") msgs.pop();
    const q = msgs.pop();
    if (!q) return;
    const t = { ...current, messages: msgs }; chatStore.update(t); setCurrent(t);
    void send(q.content);
  };

  const live_ = splitThink(draft);
  const totals = useMemo(() => {
    const ms = current?.messages ?? [];
    return { tokens: ms.reduce((s, m) => s + (m.tokens ?? 0), 0), cost: ms.reduce((s, m) => s + (m.cost ?? 0), 0),
      tps: (() => { const xs = ms.map((m) => m.tps).filter((x): x is number => !!x); return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0; })() };
  }, [current]);


  const historyPane = (
    <div className="ch-history">
      <div className="ch-history__head"><b>История</b><Button size="sm" icon={<Plus size={13} />} onClick={newThread}>Новый</Button></div>
      <div className="ch-history__list">
        {threads.length === 0 && <div className="lu-muted" style={{ padding: 12, fontSize: 13 }}>Чатов пока нет.</div>}
        {threads.map((t) => (
          <div key={t.id} className={`ch-thread${current?.id === t.id ? " ch-thread--on" : ""}`}>
            <button type="button" onClick={() => { setCurrent(t); setHistory(false); setError(""); }}>
              <span>{t.title}</span><small>{t.model} · {t.messages.length}</small>
            </button>
            <IconButton label="Удалить" kind="ghost" size="sm" onClick={() => removeThread(t.id)}><Trash2 size={13} /></IconButton>
          </div>
        ))}
      </div>
      <div className="ch-history__foot">История хранится в этом браузере</div>
    </div>
  );
  const paramsPane = (
    <div className="ch-params">
      <div className="lu-row lu-row--between"><b>Параметры</b><Button kind="ghost" size="sm" onClick={() => setParams(DEFAULTS)}>сбросить</Button></div>
      <Field label="Системный промпт" htmlFor="sys"><Textarea id="sys" value={params.system} onChange={(e) => setParams({ ...params, system: e.target.value })} placeholder="Отвечай кратко, по-русски, без вводных фраз." /></Field>
      <Range label="Temperature" value={params.temperature} min={0} max={2} step={0.1} onChange={(v) => setParams({ ...params, temperature: v })} />
      <Range label="Max tokens" value={params.maxTokens} min={64} max={8192} step={64} onChange={(v) => setParams({ ...params, maxTokens: v })} />
      <Range label="Top-p" value={params.topP} min={0} max={1} step={0.05} onChange={(v) => setParams({ ...params, topP: v })} />
      <Toggle checked={params.showThink} onChange={(v) => setParams({ ...params, showThink: v })} label="Показывать ход рассуждения" />
      <div className="ch-totals">
        <div className="lu-label">Этот чат</div>
        <div className="lu-kv"><div className="lu-kv__row"><span className="lu-kv__k">Токенов</span><span className="lu-mono">{totals.tokens}</span></div>
          <div className="lu-kv__row"><span className="lu-kv__k">Стоимость</span><span className="lu-mono">{money(totals.cost)}</span></div>
          <div className="lu-kv__row"><span className="lu-kv__k">Средняя скорость</span><span className="lu-mono">{totals.tps ? `${totals.tps.toFixed(1)} ток/с` : "—"}</span></div></div>
      </div>
    </div>
  );

  return (
    <div className="ch">
      {desktop && historyPane}
      <div className="ch-main">
        <div className="ch-bar">
          {!desktop && <IconButton label="История" kind="ghost" onClick={() => setHistory(true)}><Menu size={20} /></IconButton>}
          <label className="ch-model">
            {info && <Logo src={info.logo_url} name={info.id} />}
            <select value={model} onChange={(e) => setModel(e.target.value)} aria-label="Модель">
              {list.length === 0 && <option value="">нет отвечающих моделей</option>}
              {list.map((m) => <option key={m.id} value={m.id}>{m.id}{m.mine ? " · ваша" : ""}</option>)}
            </select>
            <ChevronDown size={14} />
          </label>
          {info && <StateBadge value="running" label="отвечает" />}
          {info?.price_in != null && <span className="lu-muted lu-hide-mobile" style={{ fontSize: 12 }}>{Math.round(info.price_in / 100)} ₽ вход · {Math.round((info.price_out ?? 0) / 100)} ₽ выход за 1M</span>}
          <span className="lu-spacer" />
          {!desktop && <IconButton label="Параметры" kind="ghost" onClick={() => setSettings(true)}><SlidersHorizontal size={19} /></IconButton>}
          {!desktop && <IconButton label="Новый чат" kind="ghost" onClick={newThread}><Plus size={19} /></IconButton>}
        </div>

        <div className="ch-scroll">
          <div className="ch-messages">
            {!current?.messages.length && !busy && (
              <div className="ch-empty">
                <div className="lu-title">Спросите что-нибудь</div>
                <div className="lu-dim" style={{ fontSize: 14 }}>{info ? `Модель ${info.id} отвечает; параметры — справа.` : "Сейчас ни одна модель не отвечает — загляните позже или разверните свою."}</div>
              </div>
            )}
            {current?.messages.map((m, i) => (
              <div key={i} className={`ch-msg ch-msg--${m.role}`}>
                {m.role === "assistant" && info && <Logo src={info.logo_url} name={current.model} />}
                <div className="ch-msg__body">
                  {m.think && params.showThink && (
                    <div>
                      <Button kind="ghost" size="sm" icon={<ChevronRight size={13} style={{ transform: openThink[i] ? "rotate(90deg)" : undefined }} />} onClick={() => setOpenThink({ ...openThink, [i]: !openThink[i] })}>Ход рассуждения</Button>
                      {openThink[i] && <div className="ch-think">{m.think}</div>}
                    </div>
                  )}
                  <div className="ch-text">{m.content}</div>
                  {m.role === "assistant" && (
                    <div className="ch-meta">
                      {m.tps !== undefined && <span><b>{m.tps.toFixed(1)}</b> ток/с</span>}
                      {m.ttft !== undefined && <span>первый токен <b>{m.ttft.toFixed(2)} с</b></span>}
                      {m.tokens !== undefined && <span><b>{m.tokens}</b> токенов</span>}
                      {m.cost !== undefined && <span><b>{money(m.cost)}</b></span>}
                      <span className="lu-spacer" />
                      <IconButton label="Скопировать" kind="ghost" size="sm" onClick={() => { navigator.clipboard?.writeText(m.content); toast("ok", "скопировано"); }}><Copy size={13} /></IconButton>
                      {i === current.messages.length - 1 && <IconButton label="Повторить" kind="ghost" size="sm" onClick={retry}><RotateCcw size={13} /></IconButton>}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {busy && (
              <div className="ch-msg ch-msg--assistant">
                {info && <Logo src={info.logo_url} name={model} />}
                <div className="ch-msg__body">
                  {(live_.think || live_.thinking) && params.showThink && <div className="ch-think">{live_.think}{live_.thinking && <span className="ch-cursor" />}</div>}
                  {live_.thinking && !params.showThink && <div className="lu-muted" style={{ fontSize: 13 }}>Думает…</div>}
                  <div className="ch-text">{live_.answer}{!live_.thinking && <span className="ch-cursor" />}</div>
                  <div className="ch-meta"><span className="lu-row" style={{ gap: 6 }}><i className="lu-badge__dot" style={{ color: "var(--accent)" }} />генерирует{live && live.tps > 0 ? ` · ${live.tps.toFixed(1)} ток/с` : ""}</span>{live?.ttft ? <span>первый токен {live.ttft.toFixed(2)} с</span> : null}</div>
                </div>
              </div>
            )}
            {error && <Notice tone="bad">{error}</Notice>}
            <div ref={bottom} />
          </div>
        </div>

        <div className="ch-composer">
          <form onSubmit={(e) => { e.preventDefault(); void send(); }}>
            <label htmlFor="ch-input" className="lu-sr">Сообщение</label>
            <textarea id="ch-input" value={prompt} placeholder="Спросите что-нибудь…" disabled={!model} rows={1}
                      onChange={(e) => setPrompt(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }} />
            <div className="ch-composer__bar">
              <span className="lu-muted lu-hide-mobile" style={{ fontSize: 11 }}>Enter — отправить · Shift+Enter — перенос</span>
              <span className="lu-spacer" />
              {busy ? <IconButton label="Остановить" kind="secondary" onClick={stop}><Square size={14} /></IconButton>
                : <IconButton label="Отправить" kind="primary" type="submit" disabled={!prompt.trim() || !model}><ArrowUp size={18} /></IconButton>}
            </div>
          </form>
          {!desktop && <div className="lu-row lu-row--between lu-muted" style={{ fontSize: 11, padding: "6px 4px 0" }}><span>этот чат · {totals.tokens} ток · {money(totals.cost)}</span>{info?.price_in != null && <span>{Math.round(info.price_in / 100)} / {Math.round((info.price_out ?? 0) / 100)} ₽ за 1M</span>}</div>}
        </div>
      </div>
      {desktop && paramsPane}

      {history && <Drawer title="История" side="left" onClose={() => setHistory(false)}>{historyPane}</Drawer>}
      {settings && <Modal title="Параметры" onClose={() => setSettings(false)}>{paramsPane}</Modal>}
    </div>
  );
}

function Range({ label, value, min, max, step, onChange }: { label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void }) {
  const id = `r-${label}`;
  return (
    <div className="lu-field">
      <div className="lu-row lu-row--between"><label htmlFor={id} className="lu-field__label">{label}</label><span className="lu-mono lu-dim" style={{ fontSize: 12 }}>{value}</span></div>
      <input id={id} type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} style={{ width: "100%", accentColor: "var(--accent)" }} />
    </div>
  );
}
