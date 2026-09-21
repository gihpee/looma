/** Слои поверх страницы: модалка (на телефоне — bottom sheet), дровер,
 *  подтверждение, тосты. Escape закрывает, фокус остаётся внутри, скролл
 *  страницы под слоем замирает. */
import {
  createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { Button, IconButton, cx } from "./primitives";

function useEscape(onClose: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [onClose]);
}
function useLockScroll() {
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, []);
}
/** Слой — в body, а не в месте вызова. Иначе sticky-шапка с backdrop-filter
 *  становится для position: fixed «окном», и дровер открывается внутри
 *  шестидесяти пикселей шапки — то есть не открывается вовсе. */
function Layer({ children }: { children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  // Уход с анимацией. React убирает узел сразу, поэтому на время анимации
  // в body остаётся его копия с классом ухода; так закрывается всё — и по
  // крестику, и по клику в меню, и когда действие само закрыло модалку.
  useEffect(() => {
    const node = box.current;
    return () => {
      // В StrictMode React репетирует размонтирование, не убирая узел из
      // документа — тогда призрак не нужен.
      if (!node || node.isConnected) return;
      const ghost = node.cloneNode(true) as HTMLElement;
      ghost.classList.add("lu-layer--out");
      ghost.setAttribute("aria-hidden", "true");
      document.body.appendChild(ghost);
      const done = () => ghost.remove();
      ghost.addEventListener("animationend", done, { once: true });
      setTimeout(done, 400);
    };
  }, []);
  return createPortal(<div ref={box} className="lu-layer">{children}</div>, document.body);
}
/** Фокус — внутрь слоя при открытии и обратно при закрытии. */
function useFocusTrap(ref: React.RefObject<HTMLElement>) {
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const box = ref.current;
    const first = box?.querySelector<HTMLElement>("input, textarea, select, button:not([aria-label='Закрыть']), a[href]");
    (first ?? box)?.focus?.();
    const onTab = (e: KeyboardEvent) => {
      if (e.key !== "Tab" || !box) return;
      const items = [...box.querySelectorAll<HTMLElement>("a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])")];
      if (items.length === 0) return;
      const a = items[0], z = items[items.length - 1];
      if (e.shiftKey && document.activeElement === a) { z.focus(); e.preventDefault(); }
      else if (!e.shiftKey && document.activeElement === z) { a.focus(); e.preventDefault(); }
    };
    addEventListener("keydown", onTab);
    return () => { removeEventListener("keydown", onTab); before?.focus?.(); };
  }, [ref]);
}

export function Modal({ title, onClose, children, footer, size, subtitle }: {
  title: ReactNode; subtitle?: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; size?: "sm";
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEscape(onClose); useLockScroll(); useFocusTrap(ref);
  return (
    <Layer>
      <div className="lu-scrim" onClick={onClose} />
      <div ref={ref} className={cx("lu-modal", size === "sm" && "lu-modal--sm")} role="dialog" aria-modal="true" tabIndex={-1}>
        <div className="lu-modal__handle"><i /></div>
        <div className="lu-modal__head">
          <div className="lu-modal__title">{title}{subtitle && <div className="lu-muted" style={{ font: "12px var(--font-sans)", marginTop: 2 }}>{subtitle}</div>}</div>
          <IconButton label="Закрыть" kind="ghost" onClick={onClose}><X size={18} /></IconButton>
        </div>
        <div className="lu-modal__body">{children}</div>
        {footer && <div className="lu-modal__foot">{footer}</div>}
      </div>
    </Layer>
  );
}

export function Drawer({ title, onClose, children, side = "right", footer }: {
  title: ReactNode; onClose: () => void; children: ReactNode; side?: "right" | "left"; footer?: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEscape(onClose); useLockScroll(); useFocusTrap(ref);
  return (
    <Layer>
      <div className="lu-scrim" onClick={onClose} />
      <div ref={ref} className={cx("lu-drawer", side === "left" && "lu-drawer--left")} role="dialog" aria-modal="true" tabIndex={-1}>
        <div className="lu-modal__head">
          <div className="lu-modal__title">{title}</div>
          <IconButton label="Закрыть" kind="ghost" onClick={onClose}><X size={18} /></IconButton>
        </div>
        <div className="lu-modal__body">{children}</div>
        {footer && <div className="lu-modal__foot">{footer}</div>}
      </div>
    </Layer>
  );
}

/** Подтверждение для того, что нельзя отменить. */
export function Confirm({ title, body, action, onClose, onConfirm, busy }: {
  title: string; body: ReactNode; action: string; onClose: () => void; onConfirm: () => void; busy?: boolean;
}) {
  return (
    <Modal title={title} onClose={onClose} size="sm" footer={
      <>
        <span className="lu-spacer" />
        <Button kind="ghost" onClick={onClose}>Отмена</Button>
        <Button kind="danger" disabled={busy} onClick={() => { onConfirm(); }}>{action}</Button>
      </>
    }>
      <div className="lu-dim" style={{ fontSize: 14 }}>{body}</div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ тосты */
type ToastKind = "ok" | "bad" | "warn";
type Toast = { id: number; kind: ToastKind; text: string };
const ToastCtx = createContext<(kind: ToastKind, text: string) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function Toasts({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((kind: ToastKind, text: string) => {
    const id = Date.now() + Math.random();
    setItems((p) => [...p, { id, kind, text }]);
    // Ошибки держатся дольше: их читают, а не замечают краем глаза.
    setTimeout(() => setItems((p) => p.filter((t) => t.id !== id)), kind === "ok" ? 4000 : 9000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="lu-toasts" aria-live="polite">
        {items.map((t) => (
          <div className={cx("lu-toast", `lu-toast--${t.kind}`)} key={t.id} role="status">
            <div className="lu-toast__body">{t.text}</div>
            <IconButton label="Закрыть" kind="ghost" size="sm" onClick={() => setItems((p) => p.filter((x) => x.id !== t.id))}><X size={14} /></IconButton>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/** Действие с кнопки: занятость, тост об исходе, обновление данных. */
export function useAction(after?: () => void) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = useCallback(async <T,>(what: () => Promise<T>, ok?: string): Promise<T | undefined> => {
    setBusy(true);
    try {
      const out = await what();
      if (ok) toast("ok", ok);
      after?.();
      return out;
    } catch (e) {
      toast("bad", e instanceof Error ? e.message : String(e));
      return undefined;
    } finally {
      setBusy(false);
    }
  }, [after, toast]);
  return { busy, run };
}
