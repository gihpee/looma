/** Примитивы: кнопки, бейджи, карточки, поля. Всё — обычные HTML-элементы с
 *  классами из ui.css; никакой собственной логики раскладки. */
import {
  forwardRef, useId, useRef, useState,
  type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode,
  type SelectHTMLAttributes, type TextareaHTMLAttributes,
} from "react";
import { Check, Copy, Search as SearchIcon, Upload } from "lucide-react";

const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

/* ---------------------------------------------------------------- кнопки */
export type ButtonKind = "primary" | "ink" | "secondary" | "ghost" | "danger";
export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  kind?: ButtonKind; size?: "sm" | "md" | "lg"; glow?: boolean; block?: boolean; icon?: ReactNode;
}
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { kind = "secondary", size = "md", glow, block, icon, className, children, type = "button", ...rest }, ref,
) {
  return (
    <button ref={ref} type={type}
            className={cx("lu-btn", `lu-btn--${kind}`, size !== "md" && `lu-btn--${size}`,
                          glow && "lu-btn--glow", block && "lu-btn--block", className)} {...rest}>
      {icon}{children}
    </button>
  );
});

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string; kind?: ButtonKind; size?: "sm" | "md";
}
export function IconButton({ label, kind = "secondary", size = "md", className, children, type = "button", ...rest }: IconButtonProps) {
  return (
    <button type={type} aria-label={label} title={label}
            className={cx("lu-btn", "lu-iconbtn", `lu-btn--${kind}`, size === "sm" && "lu-btn--sm", className)} {...rest}>
      {children}
    </button>
  );
}

/** Ссылка, выглядящая как кнопка — для переходов, которые не меняют данные. */
export function LinkButton({ kind = "secondary", size = "md", glow, block, icon, className, children, ...rest }:
  ButtonProps & { href: string; target?: string; rel?: string }) {
  const { href, target, rel, onClick } = rest as { href: string; target?: string; rel?: string; onClick?: never };
  return (
    <a href={href} target={target} rel={rel} onClick={onClick}
       className={cx("lu-btn", `lu-btn--${kind}`, size !== "md" && `lu-btn--${size}`,
                     glow && "lu-btn--glow", block && "lu-btn--block", className)}>
      {icon}{children}
    </a>
  );
}

/* ---------------------------------------------------------------- бейджи */
export type Tone = "ok" | "warn" | "bad" | "dim" | "info" | "ink";
export function Badge({ tone = "dim", pulse, dot = true, size, children, className }: {
  tone?: Tone; pulse?: boolean; dot?: boolean; size?: "sm"; children: ReactNode; className?: string;
}) {
  return (
    <span className={cx("lu-badge", tone !== "dim" && `lu-badge--${tone}`, pulse && "lu-badge--pulse",
                        size === "sm" && "lu-badge--sm", className)}>
      {dot && tone !== "ink" && tone !== "info" && <i className="lu-badge__dot" />}
      {children}
    </span>
  );
}

/** Словарь состояний → тон. Слово состояния показывается как есть; про фазу
 *  («грузит веса») говорит ProgressPhase, а не бейдж. */
const TONE: Record<string, Tone> = {
  running: "ok", done: "ok", ready: "ok", active: "ok", alive: "ok",
  pending: "warn", provisioning: "warn", starting: "warn", loading: "warn",
  fetching: "warn", downloaded: "warn", waiting: "warn", stopping: "warn",
  failed: "bad", refused: "bad", error: "bad",
  cancelled: "dim", stopped: "dim", gone: "dim", idle: "dim", archived: "dim",
};
export const toneOf = (state: string): Tone => TONE[state] ?? "dim";
export const StateBadge = ({ value, label, pulse }: { value: string; label?: string; pulse?: boolean }) =>
  <Badge tone={toneOf(value)} pulse={pulse ?? (value === "running")}>{label ?? value}</Badge>;

/* --------------------------------------------------------------- карточки */
export function Card({ pad, raised, panel, dashed, className, children, ...rest }: {
  pad?: boolean | "lg"; raised?: boolean; panel?: boolean; dashed?: boolean; className?: string; children: ReactNode;
} & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cx("lu-card", pad === "lg" ? "lu-card--pad-lg" : pad && "lu-card--pad",
                       raised && "lu-card--raised", panel && "lu-card--panel", dashed && "lu-card--dashed", className)} {...rest}>
      {children}
    </div>
  );
}
export const CardHead = ({ children, className, style }: { children: ReactNode; className?: string; style?: React.CSSProperties }) =>
  <div className={cx("lu-card__head", className)} style={style}>{children}</div>;
export const CardBody = ({ children, className, style }: { children: ReactNode; className?: string; style?: React.CSSProperties }) =>
  <div className={cx("lu-card__body", className)} style={style}>{children}</div>;
export const CardFoot = ({ children, className }: { children: ReactNode; className?: string }) =>
  <div className={cx("lu-card__foot", className)}>{children}</div>;

export function Stat({ label, value, unit, sub, subTone }: {
  label: string; value: ReactNode; unit?: string; sub?: ReactNode; subTone?: "ok" | "bad";
}) {
  return (
    <div className="lu-card lu-stat">
      <div className="lu-label">{label}</div>
      <div className="lu-stat__value">{value}{unit && <small> {unit}</small>}</div>
      {sub && <div className={cx("lu-stat__sub", subTone && `lu-stat__sub--${subTone}`)}>{sub}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ поля */
export function Field({ label, hint, error, required, htmlFor, children }: {
  label: string; hint?: ReactNode; error?: string; required?: boolean; htmlFor?: string; children: ReactNode;
}) {
  return (
    <div className="lu-field">
      <label className="lu-field__label" htmlFor={htmlFor}>{label}{required && <span className="lu-req">*</span>}</label>
      {children}
      {error ? <span className="lu-field__error">{error}</span> : hint && <span className="lu-field__hint">{hint}</span>}
    </div>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { mono?: boolean }>(
  function Input({ mono, className, ...rest }, ref) {
    return <input ref={ref} className={cx("lu-input", mono && "lu-input--mono", className)} {...rest} />;
  });
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement> & { mono?: boolean }>(
  function Textarea({ mono, className, ...rest }, ref) {
    return <textarea ref={ref} className={cx("lu-textarea", mono && "lu-input--mono", className)} {...rest} />;
  });
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, ...rest }, ref) {
    return <select ref={ref} className={cx("lu-select", className)} {...rest} />;
  });

/** Инпут с приставкой/суффиксом: «120 | ₽». */
export function InputAffix({ suffix, prefix, ...rest }: InputHTMLAttributes<HTMLInputElement> & { suffix?: ReactNode; prefix?: ReactNode; mono?: boolean }) {
  return (
    <div className="lu-inputwrap">
      {prefix && <span className="lu-inputwrap__affix">{prefix}</span>}
      <Input {...rest} />
      {suffix && <span className="lu-inputwrap__affix">{suffix}</span>}
    </div>
  );
}

/** − n + . Значение всегда число в границах; текст, который нельзя разобрать,
 *  не ломает состояние — остаётся прежнее. */
export function NumberStepper({ value, onChange, min = 1, max = Infinity, step = 1, id, disabled }: {
  value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number; id?: string; disabled?: boolean;
}) {
  const clamp = (n: number) => Math.min(max, Math.max(min, n));
  return (
    <div className="lu-stepper">
      <button type="button" aria-label="Меньше" disabled={disabled || value <= min} onClick={() => onChange(clamp(value - step))}>−</button>
      <input id={id} type="text" inputMode="numeric" value={value} disabled={disabled}
             onChange={(e) => { const n = Number(e.target.value); if (Number.isFinite(n)) onChange(clamp(n)); }} />
      <button type="button" aria-label="Больше" disabled={disabled || value >= max} onClick={() => onChange(clamp(value + step))}>+</button>
    </div>
  );
}

export function Toggle({ checked, onChange, label, disabled }: {
  checked: boolean; onChange: (v: boolean) => void; label: ReactNode; disabled?: boolean;
}) {
  return (
    <label className="lu-toggle">
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="lu-toggle__track" />
      <span>{label}</span>
    </label>
  );
}

export function SearchBox({ value, onChange, placeholder = "Поиск", kbd, id }: {
  value: string; onChange: (v: string) => void; placeholder?: string; kbd?: string; id?: string;
}) {
  return (
    <label className="lu-search" htmlFor={id}>
      <SearchIcon size={15} />
      <input id={id} type="search" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
      {kbd && <kbd>{kbd}</kbd>}
    </label>
  );
}

/** Файловый вход, который не выглядит как файловый вход браузера. */
export function FilePick({ label, accept, onPick, id }: { label: string; accept?: string; onPick?: (f: File | null) => void; id?: string }) {
  const [name, setName] = useState("");
  return (
    <label className={cx("lu-file", name && "lu-file--filled")} htmlFor={id}>
      <input id={id} type="file" accept={accept} onChange={(e) => {
        const f = e.target.files?.[0] ?? null; setName(f?.name ?? ""); onPick?.(f);
      }} />
      <Upload size={15} />
      <span style={{ flexGrow: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name || label}</span>
    </label>
  );
}

/* ---------------------------------------------------------- переключатели */
export function Segmented<T extends string>({ value, onChange, options, fill, soft, ariaLabel }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode }[]; fill?: boolean; soft?: boolean; ariaLabel?: string;
}) {
  return (
    <div className={cx("lu-segment", fill && "lu-segment--fill", soft && "lu-segment--soft")} role="group" aria-label={ariaLabel}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  );
}
export function Tabs<T extends string>({ value, onChange, options }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode }[];
}) {
  return (
    <div className="lu-tabs" role="tablist">
      {options.map((o) => (
        <button key={o.value} type="button" role="tab" aria-selected={o.value === value} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------- отображение */
export function KeyValue({ rows }: { rows: { k: ReactNode; v: ReactNode }[] }) {
  return (
    <div className="lu-kv">
      {rows.map((r, i) => <div key={i} className="lu-kv__row"><span className="lu-kv__k">{r.k}</span><span className="lu-kv__v">{r.v}</span></div>)}
    </div>
  );
}

export function CodeBlock({ code, copy = true }: { code: string; copy?: boolean }) {
  const [done, setDone] = useState(false);
  const timer = useRef<number>();
  return (
    <div className="lu-code">
      {copy && (
        <IconButton label={done ? "Скопировано" : "Скопировать"} kind="ghost" size="sm" className="lu-code__copy"
                    style={{ color: "inherit" }}
                    onClick={() => {
                      navigator.clipboard?.writeText(code); setDone(true);
                      clearTimeout(timer.current); timer.current = window.setTimeout(() => setDone(false), 1500);
                    }}>
          {done ? <Check size={14} /> : <Copy size={14} />}
        </IconButton>
      )}
      {code}
    </div>
  );
}

export function Bar({ percent, tone }: { percent: number; tone?: "ok" | "bad" }) {
  return <div className={cx("lu-bar", tone && `lu-bar--${tone}`)}><i style={{ width: `${Math.max(0, Math.min(100, percent))}%` }} /></div>;
}

/** Бар + слово фазы. «running» — про процесс, а не про готовность отвечать:
 *  веса грузятся минутами, и всё это время состояние задачи одинаково. */
export function ProgressPhase({ phase, percent, right, tone }: { phase: ReactNode; percent: number; right?: ReactNode; tone?: "ok" | "bad" }) {
  return (
    <div className="lu-phase">
      <div className="lu-phase__row"><span>{phase}</span><span className="lu-mono">{right ?? `${Math.round(percent)}%`}</span></div>
      <Bar percent={percent} tone={tone} />
    </div>
  );
}

export function Empty({ title, children, action, icon }: { title: string; children?: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="lu-empty">
      {icon ?? <EmptyGlyph />}
      <div className="lu-empty__title">{title}</div>
      {children && <div className="lu-empty__text">{children}</div>}
      {action}
    </div>
  );
}
function EmptyGlyph() {
  return (
    <svg width="56" height="44" viewBox="0 0 56 44" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
      <rect x="6" y="6" width="44" height="32" rx="4" strokeOpacity=".5" /><path d="M6 16h44" strokeOpacity=".5" />
      <path d="M14 26h12M14 31h20" /><circle cx="42" cy="28" r="5" /><path d="M42 25v6M39 28h6" />
    </svg>
  );
}

export function Notice({ tone = "info", icon, children }: { tone?: "info" | "ok" | "warn" | "bad"; icon?: ReactNode; children: ReactNode }) {
  return <div className={cx("lu-notice", `lu-notice--${tone}`)}>{icon}<span>{children}</span></div>;
}
export const Chip = ({ children }: { children: ReactNode }) => <span className="lu-chip">{children}</span>;
export const Avatar = ({ name }: { name: string }) => <span className="lu-avatar" aria-hidden="true">{(name.trim()[0] ?? "?").toUpperCase()}</span>;

/** Логотип модели или вендора карты: картинка по url, а без неё — плашка с
 *  инициалами. Фронт ничего не хардкодит: url приходит с прайсом и каталогом,
 *  а ставит его админ (или он тянется с HuggingFace). */
export function Logo({ src, name, size }: { src?: string | null; name: string; size?: "lg" }) {
  const initials = name.replace(/[^A-Za-zА-Яа-я0-9]/g, " ").trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
  return (
    <span className={cx("lu-logo", size === "lg" && "lu-logo--lg")} aria-hidden="true">
      {src ? <img src={src} alt="" loading="lazy" /> : initials}
    </span>
  );
}

/** id для связки label ↔ input, когда вызывающему лень его придумывать. */
export const useFieldId = (given?: string) => { const auto = useId(); return given ?? auto; };
export { cx };
