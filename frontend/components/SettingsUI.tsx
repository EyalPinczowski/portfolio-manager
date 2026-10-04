"use client";
import { useEffect, useRef, type ReactNode } from "react";
import { Link } from "@/i18n/navigation";
import { CheckIcon, ChevronIcon } from "./icons";

/**
 * Phone-style settings primitives (iOS/Android Settings look): grouped inset lists, 48px rows with a coloured icon tile,
 * muted trailing value and a chevron (mirrored in RTL), toggle switches, check lists and a bottom sheet.
 * Colours come from the theme tokens; the icon tiles are fixed saturated colours with a white glyph in both themes.
 */

export type Tone = "blue" | "green" | "orange" | "red" | "purple" | "teal" | "pink" | "gray" | "indigo";
const TONES: Record<Tone, string> = {
  blue: "bg-blue-600", green: "bg-emerald-600", orange: "bg-orange-600", red: "bg-red-600", purple: "bg-purple-600",
  teal: "bg-teal-600", pink: "bg-pink-600", gray: "bg-slate-500", indigo: "bg-indigo-600",
};

export function RowIcon({ tone = "gray", children }: { tone?: Tone; children: ReactNode }) {
  return <span aria-hidden="true" className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-white ${TONES[tone]}`}>{children}</span>;
}

const ROW = "flex min-h-12 w-full items-center gap-3 px-4 py-2 text-start text-base";

/** A rounded inset section with optional small header above and grey explanatory footer below. */
export function SettingsGroup({ title, footer, label, children, role }: { title?: string; footer?: ReactNode; label?: string; children: ReactNode; role?: string }) {
  const name = label ?? title;
  return (
    <section aria-label={name} className="space-y-1.5">
      {title && <h2 className="px-4 text-caption font-semibold uppercase tracking-wide text-muted">{title}</h2>}
      <ul role={role} aria-label={role ? name : undefined} className="divide-y divide-line overflow-hidden rounded-2xl border border-line bg-surface">{children}</ul>
      {footer && <p className="px-4 text-caption text-muted">{footer}</p>}
    </section>
  );
}

/** A free-form list item inside a group (forms, messages). */
export function GroupItem({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <li className={`px-4 py-3 ${className}`}>{children}</li>;
}

type RowProps = {
  label: string; value?: string; icon?: ReactNode; tone?: Tone;
  href?: string; onClick?: () => void; destructive?: boolean; disabled?: boolean; chevron?: boolean;
};

/** One tappable row: a link (drill-down, shows a chevron), a button (action) or plain text when neither is given. */
export function SettingsRow({ label, value, icon, tone, href, onClick, destructive, disabled, chevron }: RowProps) {
  const body = (
    <>
      {icon && <RowIcon tone={tone}>{icon}</RowIcon>}
      <span className={`min-w-0 flex-1 ${destructive ? "font-medium text-loss" : ""}`}>{label}</span>
      {value && <span className="max-w-[50%] truncate text-muted">{value}</span>}
      {(href || chevron) && <ChevronIcon className="h-4 w-4 shrink-0 text-muted" />}
    </>
  );
  if (href) return <li><Link href={href} className={`${ROW} hover:bg-surface-2 active:bg-surface-2`}>{body}</Link></li>;
  if (onClick) return <li><button type="button" onClick={onClick} disabled={disabled} className={`${ROW} hover:bg-surface-2 active:bg-surface-2 disabled:opacity-50`}>{body}</button></li>;
  return <li className={ROW}>{body}</li>;
}

/** A row with an inline on/off switch (saves at once). */
export function ToggleRow({ label, checked, onChange, icon, tone, disabled }: { label: string; checked: boolean; onChange: (v: boolean) => void; icon?: ReactNode; tone?: Tone; disabled?: boolean }) {
  return (
    <li>
      <button type="button" role="switch" aria-checked={checked} disabled={disabled} onClick={() => onChange(!checked)} className={`${ROW} hover:bg-surface-2 disabled:opacity-50`}>
        {icon && <RowIcon tone={tone}>{icon}</RowIcon>}
        <span className="min-w-0 flex-1">{label}</span>
        <span aria-hidden="true" className={`relative h-7 w-12 shrink-0 rounded-full transition-colors ${checked ? "bg-emerald-600" : "bg-line"}`}>
          <span className={`absolute start-0.5 top-0.5 h-6 w-6 rounded-full bg-white shadow transition-transform ${checked ? "translate-x-5 rtl:-translate-x-5" : ""}`} />
        </span>
      </button>
    </li>
  );
}

/** A row with a label on one side and a control (input, select) on the other. The control must carry `id={htmlFor}`. */
export function FieldRow({ label, htmlFor, help, children }: { label: string; htmlFor: string; help?: string; children: ReactNode }) {
  return (
    <li className="px-4 py-2">
      <div className="flex min-h-11 items-center justify-between gap-3">
        <label htmlFor={htmlFor} className="min-w-0 flex-1 text-base">{label}</label>
        <div className="shrink-0">{children}</div>
      </div>
      {help && <p className="pb-1 text-caption text-muted">{help}</p>}
    </li>
  );
}

/** Class for inputs and selects placed inside a FieldRow. */
export const FIELD = "min-h-11 max-w-[11rem] rounded-lg border border-line bg-surface-2 px-3 text-end text-base text-fg";

function CheckRow({ role, checked, label, hint, onClick, disabled }: { role: "radio" | "checkbox"; checked: boolean; label: string; hint?: string; onClick: () => void; disabled?: boolean }) {
  return (
    <li role="none">
      <button type="button" role={role} aria-checked={checked} disabled={disabled} onClick={onClick} className={`${ROW} hover:bg-surface-2 disabled:opacity-50`}>
        <span className="min-w-0 flex-1">
          <span className="block">{label}</span>
          {hint && <span className="block text-caption text-muted">{hint}</span>}
        </span>
        <span className="flex h-5 w-5 shrink-0 items-center justify-center text-brand-text">{checked && <CheckIcon className="h-5 w-5" />}</span>
      </button>
    </li>
  );
}

type Opt<T extends string> = { value: T; label: string; hint?: string };

/** A single-choice list with a checkmark on the chosen row. `value` null means nothing is chosen yet (no defaults). */
export function CheckList<T extends string>({ title, footer, label, value, options, onPick, disabled }: {
  title?: string; footer?: ReactNode; label: string; value: T | null; options: Opt<T>[]; onPick: (v: T) => void; disabled?: boolean;
}) {
  return (
    <SettingsGroup title={title} footer={footer} label={label} role="radiogroup">
      {options.map((o) => <CheckRow key={o.value} role="radio" checked={value === o.value} label={o.label} hint={o.hint} disabled={disabled} onClick={() => onPick(o.value)} />)}
    </SettingsGroup>
  );
}

/** A multi-choice list (checkmarks); several rows can be on. */
export function MultiCheckList<T extends string>({ title, footer, label, values, options, onToggle }: {
  title?: string; footer?: ReactNode; label: string; values: T[]; options: Opt<T>[]; onToggle: (v: T) => void;
}) {
  return (
    <SettingsGroup title={title} footer={footer} label={label} role="group">
      {options.map((o) => <CheckRow key={o.value} role="checkbox" checked={values.includes(o.value)} label={o.label} hint={o.hint} onClick={() => onToggle(o.value)} />)}
    </SettingsGroup>
  );
}

/** A bottom sheet (dialog). Escape or a tap on the dimmed area closes it; focus moves into it. */
export function Sheet({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 sm:items-center" onClick={onClose} data-testid="sheet-backdrop">
      <div
        ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}
        className="settings-sheet max-h-[90dvh] w-full max-w-md space-y-3 overflow-y-auto rounded-t-3xl border border-line bg-surface p-5 pb-[calc(1.25rem+var(--safe-b))] sm:rounded-3xl"
      >
        <h2 className="text-heading">{title}</h2>
        {children}
      </div>
    </div>
  );
}

/** A confirm sheet for destructive actions: red confirm button first, a plain Cancel under it. */
export function ConfirmSheet({ title, body, confirmLabel, cancelLabel, onConfirm, onCancel, busy, confirmDisabled, children }: {
  title: string; body?: string; confirmLabel: string; cancelLabel: string; onConfirm: () => void; onCancel: () => void; busy?: boolean; confirmDisabled?: boolean; children?: ReactNode;
}) {
  return (
    <Sheet title={title} onClose={onCancel}>
      {body && <p className="text-sm text-muted">{body}</p>}
      {children}
      <div className="flex flex-col gap-2">
        <button type="button" className="btn-danger" disabled={busy || confirmDisabled} onClick={onConfirm}>{confirmLabel}</button>
        <button type="button" className="btn-secondary" onClick={onCancel}>{cancelLabel}</button>
      </div>
    </Sheet>
  );
}
