/** Живое демо: вопрос уходит в /api/demo, ответ стримится с той модели,
 *  которая сейчас отвечает в сети, и показывается с метриками. Блок
 *  `<think>…</think>` — ход рассуждения модели — сворачивается отдельно, а
 *  не выдаётся за ответ. */
import { useEffect, useRef, useState } from "react";
import { ArrowUp, ChevronRight } from "lucide-react";
import { ApiError, stream, useGet } from "@looma/api";
import { Button, IconButton, Notice } from "@looma/ui";

interface DemoInfo { model: string; nodes: number; limit: number }

/** Разделить сырой поток текста на рассуждение и ответ. Тег может прийти
 *  по кускам, поэтому режем по накопленному тексту, а не по чанкам. */
export function splitThink(raw: string): { think: string; answer: string; thinking: boolean } {
  const open = raw.indexOf("<think>");
  if (open === -1) return { think: "", answer: raw, thinking: false };
  const close = raw.indexOf("</think>", open);
  if (close === -1) return { think: raw.slice(open + 7), answer: "", thinking: true };
  return { think: raw.slice(open + 7, close).trim(), answer: raw.slice(close + 8).replace(/^\s+/, ""), thinking: false };
}

export function Demo() {
  const info = useGet<DemoInfo>("/api/demo", 30_000, { retry: false });
  const [prompt, setPrompt] = useState("");
  const [raw, setRaw] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showThink, setShowThink] = useState(false);
  const [stats, setStats] = useState<{ tokens: number; ttft: number; tps: number } | null>(null);
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);

  const ask = async () => {
    const q = prompt.trim();
    if (!q || busy) return;
    abort.current?.abort();
    const ctl = new AbortController(); abort.current = ctl;
    setBusy(true); setRaw(""); setError(""); setStats(null); setShowThink(false);
    const t0 = performance.now(); let first = 0; let tokens = 0; let text = "";
    try {
      for await (const line of stream("/api/demo", { prompt: q }, ctl.signal)) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (payload === "[DONE]") break;
        let piece: { choices?: { delta?: { content?: string; reasoning_content?: string } }[]; error?: { message: string } };
        try { piece = JSON.parse(payload); } catch { continue; }
        if (piece.error) throw new Error(piece.error.message);
        const d = piece.choices?.[0]?.delta;
        const chunk = (d?.reasoning_content ? `<think>${d.reasoning_content}` : "") + (d?.content ?? "");
        if (!chunk) continue;
        if (!first) first = performance.now();
        tokens += 1; text += chunk; setRaw(text);
        const dt = (performance.now() - first) / 1000;
        setStats({ tokens, ttft: (first - t0) / 1000, tps: dt > 0.2 ? tokens / dt : 0 });
      }
    } catch (e) {
      if (!(e instanceof DOMException)) setError(e instanceof ApiError || e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const { think, answer, thinking } = splitThink(raw);
  const ready = !!info.data?.model;

  return (
    <div className="lp-demo__box">
      <form className="lp-demo__form" onSubmit={(e) => { e.preventDefault(); void ask(); }}>
        <label htmlFor="demo-q" style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>Вопрос модели</label>
        <input id="demo-q" value={prompt} onChange={(e) => setPrompt(e.target.value)} disabled={!ready || busy}
               placeholder={ready ? "Спросите что-нибудь…" : "сейчас ни одна модель не отвечает"} maxLength={500} />
        <IconButton label="Спросить" kind="primary" type="submit" disabled={!ready || busy || !prompt.trim()}><ArrowUp size={18} /></IconButton>
      </form>
      {info.data && (
        <div className="lu-muted" style={{ fontSize: 12 }}>
          {ready ? <>{info.data.model} · разрезана по {info.data.nodes} узлам сети · до {info.data.limit} токенов на ответ</> : "Демо появится, когда в сети поднимется модель."}
        </div>
      )}
      {error && <Notice tone="bad">{error}</Notice>}
      {(think || thinking) && (
        <div>
          <Button kind="ghost" size="sm" onClick={() => setShowThink((v) => !v)} icon={<ChevronRight size={13} style={{ transform: showThink ? "rotate(90deg)" : undefined }} />}>
            {thinking ? "Думает…" : "Ход рассуждения"}
          </Button>
          {(showThink || thinking) && <div className="lp-demo__think">{think}{thinking && <span className="lp-cursor" />}</div>}
        </div>
      )}
      {(answer || (busy && !thinking)) && <div className="lp-demo__answer">{answer}{busy && !thinking && <span className="lp-cursor" />}</div>}
      {stats && (
        <div className="lp-demo__stats">
          <div className="lp-demo__stat"><small>Скорость</small><b>{stats.tps.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}<i>ток/с</i></b></div>
          <div className="lp-demo__stat"><small>Первый токен</small><b>{stats.ttft.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}<i>с</i></b></div>
          <div className="lp-demo__stat"><small>Выдано</small><b>{stats.tokens}<i>ток</i></b></div>
        </div>
      )}
    </div>
  );
}
