/** Визард: шаги с полосой прогресса, RadioCard для выбора вариантов, футер со
 *  стоимостью. Стоимость всегда стоит слева от кнопок — до действия, а не
 *  после него. */
import { type ReactNode } from "react";
import { Check } from "lucide-react";
import { Badge, Button, cx } from "./primitives";

export interface Step { label: string; summary?: ReactNode }

export function Steps({ steps, current }: { steps: Step[]; current: number }) {
  return (
    <div className="lu-steps" style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }}
         role="list" aria-label={`Шаг ${current + 1} из ${steps.length}`}>
      {steps.map((s, i) => {
        const state = i < current ? "done" : i === current ? "active" : "todo";
        return (
          <div key={i} role="listitem" aria-current={state === "active" ? "step" : undefined}
               className={cx("lu-steps__item", `lu-steps__item--${state}`)}>
            <div className="lu-steps__label">
              <span className="lu-steps__n">{state === "done" ? <Check size={12} strokeWidth={3} /> : i + 1}</span>
              <span>{s.label}{state === "done" && s.summary && <span className="lu-steps__sum"> · {s.summary}</span>}</span>
            </div>
            <div className="lu-steps__bar" />
          </div>
        );
      })}
    </div>
  );
}

export function WizardFooter({ costLabel, cost, costNote, back, next, nextLabel = "Далее", backLabel = "Назад", nextDisabled, busy, sticky = true, nextKind = "primary" }: {
  costLabel?: ReactNode; cost?: ReactNode; costNote?: ReactNode;
  back?: () => void; next?: () => void; nextLabel?: ReactNode; backLabel?: ReactNode;
  nextDisabled?: boolean; busy?: boolean; sticky?: boolean; nextKind?: "primary" | "ink";
}) {
  return (
    <div className={cx("lu-wizfoot", sticky && "lu-wizfoot--sticky")}>
      <div className="lu-wizfoot__cost">
        {costLabel && <small>{costLabel}</small>}
        {cost && <b>{cost} {costNote && <span>· {costNote}</span>}</b>}
      </div>
      <div className="lu-wizfoot__actions">
        {back ? <Button kind="secondary" onClick={back} disabled={busy}>{backLabel}</Button> : <span />}
        {next && <Button kind={nextKind} onClick={next} disabled={nextDisabled || busy}>{busy ? "…" : nextLabel}</Button>}
      </div>
    </div>
  );
}

export function RadioCard({ name, value, checked, onChange, title, text, meta, icon, recommended, disabled, badge }: {
  name: string; value: string; checked: boolean; onChange: (v: string) => void;
  title: ReactNode; text?: ReactNode; meta?: ReactNode; icon?: ReactNode; recommended?: boolean; disabled?: boolean; badge?: ReactNode;
}) {
  return (
    <label className={cx("lu-radiocard", checked && "lu-radiocard--on", disabled && "lu-radiocard--disabled")}>
      <input type="radio" name={name} value={value} checked={checked} disabled={disabled} onChange={() => onChange(value)} />
      <span className="lu-radiocard__dot" aria-hidden="true" />
      {icon && <span className="lu-radiocard__icon">{icon}</span>}
      <span className="lu-radiocard__body">
        <span className="lu-radiocard__title">
          {title}
          {recommended && <Badge tone="info" size="sm">Рекомендуем</Badge>}
          {badge}
        </span>
        {text && <span className="lu-radiocard__text">{text}</span>}
        {meta && <span className="lu-radiocard__meta">{meta}</span>}
      </span>
    </label>
  );
}
