"use client";
import { useEffect, useId, useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { api, type SecurityHit } from "@/lib/api";

const MIN_CHARS = 2;
const DEBOUNCE_MS = 300;

/**
 * A symbol box with a search dropdown (combobox). Typing 2+ characters asks `/securities/search?remote=1`, which
 * lists known securities first and then listings the app does not know yet (tagged "new"). Picking one calls
 * `onPick`; plain typing still works because the box is an ordinary text input driven by `value`/`onChange`.
 * The search only helps the user choose a ticker: nothing here reaches a score.
 */
export function SymbolSearch({ value, onChange, onPick, onCommit, id, label, className = "input w-28", invalid, maxLength = 20 }: {
  value: string;
  onChange: (v: string) => void;
  onPick: (hit: SecurityHit) => void;
  /** The input lost focus (not when a result was picked). */
  onCommit?: () => void;
  id?: string;
  /** aria-label when there is no visible <label htmlFor={id}>. */
  label?: string;
  className?: string;
  invalid?: boolean;
  maxLength?: number;
}) {
  const t = useTranslations("symbolSearch");
  const locale = useLocale();
  const uid = useId();
  const listId = `${uid}-list`;
  const [hits, setHits] = useState<SecurityHit[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [active, setActive] = useState(-1);
  /** Search only what the user typed here, never a value that was filled in by a pick or by the server. */
  const [typed, setTyped] = useState(false);
  const seq = useRef(0);
  const q = value.trim();

  useEffect(() => {
    if (!typed || q.length < MIN_CHARS) { seq.current++; return; } // a too-short box shows no list (see `show`)
    const mine = ++seq.current;
    const timer = setTimeout(() => {
      setBusy(true);
      api.searchSecurities(q, true)
        .then((r) => { if (mine === seq.current) { setHits(r.slice(0, 12)); setActive(-1); } })
        .catch(() => { if (mine === seq.current) setHits([]); })
        .finally(() => { if (mine === seq.current) setBusy(false); });
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [q, typed]);

  const show = open && typed && q.length >= MIN_CHARS && (busy || hits.length > 0);
  const pick = (h: SecurityHit) => {
    seq.current++; setTyped(false); setOpen(false); setHits([]); setBusy(false); setActive(-1);
    onPick(h);
  };
  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" && hits.length > 0) { e.preventDefault(); setOpen(true); setActive((a) => (a + 1) % hits.length); }
    else if (e.key === "ArrowUp" && hits.length > 0) { e.preventDefault(); setOpen(true); setActive((a) => (a <= 0 ? hits.length - 1 : a - 1)); }
    else if (e.key === "Enter" && show && active >= 0 && hits[active]) { e.preventDefault(); pick(hits[active]); }
    else if (e.key === "Escape" && show) { e.preventDefault(); e.stopPropagation(); setOpen(false); }
  };
  const nameOf = (h: SecurityHit) => (locale === "he" && h.name_he ? h.name_he : h.name_en);
  const venue = (h: SecurityHit) => h.exchange ?? h.market;

  return (
    <div className="relative">
      <input
        id={id} className={className} dir="ltr" role="combobox" aria-expanded={show} aria-controls={listId} aria-autocomplete="list"
        aria-activedescendant={show && active >= 0 ? `${uid}-o${active}` : undefined}
        aria-label={label} aria-invalid={invalid || undefined} placeholder={t("placeholder")}
        autoComplete="off" autoCapitalize="characters" spellCheck={false} maxLength={maxLength} value={value}
        onChange={(e) => { setTyped(true); setOpen(true); onChange(e.target.value); }}
        onFocus={() => setOpen(true)}
        onBlur={() => { setOpen(false); onCommit?.(); }}
        onKeyDown={onKey}
      />
      {/* The list is always mounted so `aria-controls` points at something; it is only visible while open. */}
      <ul
        id={listId} role="listbox" aria-label={t("listLabel")} hidden={!show}
        className="absolute start-0 z-30 mt-1 max-h-72 w-72 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-xl border border-line bg-surface p-1 shadow-lg"
      >
        {busy && hits.length === 0 && <li role="presentation" className="px-3 py-2 text-sm text-muted">{t("searching")}</li>}
        {hits.map((h, i) => (
          <li
            key={h.symbol} id={`${uid}-o${i}`} role="option" aria-selected={i === active}
            className={`flex min-h-11 cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm ${i === active ? "bg-brand-soft" : "hover:bg-surface-2"}`}
            // mousedown (before the input's blur) so the pick lands; works for touch, which synthesises it
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => pick(h)}
            onMouseEnter={() => setActive(i)}
          >
            <span className="min-w-0">
              <bdi dir="auto" className="block truncate font-medium">{nameOf(h)}</bdi>
              <span className="block text-xs text-muted" dir="ltr">{venue(h)}{h.currency ? ` · ${h.currency}` : ""}</span>
            </span>
            <span className="flex shrink-0 items-center gap-1">
              {h.source === "new" && <span className="chip-neutral" title={t("newHint")}>{t("new")}</span>}
              <span className="chip-neutral" dir="ltr">{h.symbol}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
