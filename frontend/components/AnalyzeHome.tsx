"use client";
import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import useSWR from "swr";
import { api, type WatchlistItem } from "@/lib/api";
import { formatMoney, formatTime } from "@/lib/format";
import { useSearchHistory, useWatchlist } from "@/lib/hooks";
import { analyzeHref, analyzeSymbol } from "@/lib/routes";
import { Link, useRouter } from "@/i18n/navigation";
import { CloseIcon } from "./icons";

function SearchBox() {
  const t = useTranslations("analyze");
  const locale = useLocale();
  const router = useRouter();
  const [q, setQ] = useState("");
  const [dq, setDq] = useState("");
  const [bad, setBad] = useState(false);
  useEffect(() => {
    const id = setTimeout(() => setDq(q.trim()), 250);
    return () => clearTimeout(id);
  }, [q]);
  const { data, error, isLoading } = useSWR(dq ? ["sec-search", dq] : null, () => api.searchSecurities(dq), { revalidateOnFocus: false });
  const typed = analyzeSymbol(q);
  const go = (e: React.FormEvent) => {
    e.preventDefault();
    if (!q.trim()) return;
    if (!typed) { setBad(true); return; }
    router.push(analyzeHref(typed));
  };
  return (
    <section className="card space-y-3" aria-label={t("searchGo")}>
      <form onSubmit={go} className="space-y-2" role="search">
        <label htmlFor="analyze-q" className="label">{t("searchLabel")}</label>
        <div className="flex gap-2">
          <input
            id="analyze-q" className="input flex-1" value={q} placeholder={t("searchPlaceholder")} autoComplete="off" autoCapitalize="none" spellCheck={false}
            onChange={(e) => { setQ(e.target.value); setBad(false); }} aria-invalid={bad}
          />
          <button type="submit" className="btn-primary">{t("searchGo")}</button>
        </div>
        {bad && <p role="alert" className="text-sm text-loss">{t("invalidSymbol")}</p>}
      </form>
      {dq && (
        <div aria-live="polite" className="space-y-2">
          {error ? <p role="alert" className="text-sm text-loss">{t("searchError")}</p>
            : isLoading || !data ? <p role="status" className="text-sm text-muted">{t("searching")}</p>
            : data.length === 0 ? <p className="text-sm text-muted">{t("noMatches")}</p>
            : (
              <ul className="space-y-1" aria-label={t("searchGo")}>
                {data.map((h) => (
                  <li key={h.symbol}>
                    <Link href={analyzeHref(h.symbol)} className="flex min-h-11 items-center justify-between gap-2 rounded-xl border border-line px-3 py-2 hover:bg-surface-2" aria-label={t("open", { symbol: h.symbol })}>
                      <span><bdi dir="auto" className="font-medium">{locale === "he" ? h.name_he : h.name_en}</bdi></span>
                      <span className="chip-neutral" dir="ltr">{h.symbol}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          {typed && !isLoading && (
            <Link href={analyzeHref(typed)} className="btn-secondary">{t("analyzeTyped", { symbol: typed })}</Link>
          )}
        </div>
      )}
    </section>
  );
}

function Recent() {
  const t = useTranslations("analyze");
  const { data, error, mutate } = useSearchHistory();
  const [err, setErr] = useState(false);
  const run = async (fn: () => Promise<unknown>) => {
    setErr(false);
    try { await fn(); await mutate(); } catch { setErr(true); }
  };
  return (
    <section className="card space-y-2" aria-label={t("recentTitle")}>
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-heading">{t("recentTitle")}</h2>
        {data && data.length > 0 && <button type="button" className="btn-secondary" onClick={() => run(() => api.clearSearchHistory())}>{t("clearRecent")}</button>}
      </div>
      {error ? <p role="alert" className="text-loss">{t("error")}</p> : !data ? <p role="status" className="text-muted">{t("loading")}</p> : data.length === 0 ? <p className="text-sm text-muted">{t("recentNone")}</p> : (
        <ul className="divide-y divide-line">
          {data.map((h) => (
            <li key={h.symbol} className="flex items-center justify-between gap-2">
              <Link href={analyzeHref(h.symbol)} className="flex min-h-11 flex-1 items-center font-medium hover:underline" aria-label={t("open", { symbol: h.symbol })}><span dir="ltr">{h.symbol}</span></Link>
              <button type="button" aria-label={t("removeSearch", { symbol: h.symbol })} onClick={() => run(() => api.removeSearch(h.symbol))} className="inline-flex h-11 w-11 items-center justify-center rounded-full text-muted hover:bg-surface-2"><CloseIcon /></button>
            </li>
          ))}
        </ul>
      )}
      {err && <p role="alert" className="text-sm text-loss">{t("watchError")}</p>}
      <p className="text-caption text-muted">{t("recentNote")}</p>
    </section>
  );
}

/** The cached price and how fresh it is. A last close or an old quote is labelled, never shown as live. */
export function QuoteLine({ q }: { q: WatchlistItem["quote"] }) {
  const t = useTranslations("analyze");
  const locale = useLocale();
  if (!q) return <span className="text-muted">{t("noQuote")}</span>;
  return (
    <span className="text-sm">
      <span dir="ltr" className="tabular-nums font-medium">{formatMoney(q.price, q.currency, locale)}</span>{" "}
      <span className="text-caption text-muted">{t("asOf", { time: formatTime(q.as_of, locale) })}</span>{" "}
      {q.basis === "last_close" && <span className="chip-neutral">{t("lastClose")}</span>}{" "}
      <span className={q.is_fresh ? "chip-brand" : "chip-warn"}>{q.is_fresh ? t("fresh") : t("stale")}</span>
    </span>
  );
}

function Watchlist() {
  const t = useTranslations("analyze");
  const { data, error, mutate } = useWatchlist();
  const [err, setErr] = useState(false);
  const remove = async (symbol: string) => {
    setErr(false);
    try { await api.removeFromWatchlist(symbol); await mutate(); } catch { setErr(true); }
  };
  return (
    <section className="card space-y-2" aria-label={t("watchTitle")}>
      <h2 className="text-heading">{t("watchTitle")}</h2>
      {error ? <p role="alert" className="text-loss">{t("error")}</p> : !data ? <p role="status" className="text-muted">{t("loading")}</p> : data.length === 0 ? <p className="text-sm text-muted">{t("watchNone")}</p> : (
        <ul className="divide-y divide-line">
          {data.map((w) => (
            <li key={w.symbol} className="flex items-center justify-between gap-2 py-1">
              <Link href={analyzeHref(w.symbol)} className="flex min-h-11 flex-1 flex-col justify-center" aria-label={t("open", { symbol: w.symbol })}>
                <span className="font-medium"><span dir="ltr">{w.symbol}</span>{w.name && <> · <bdi dir="auto" className="text-muted">{w.name}</bdi></>}</span>
                <QuoteLine q={w.quote} />
              </Link>
              <button type="button" aria-label={t("removeWatch", { symbol: w.symbol })} onClick={() => remove(w.symbol)} className="inline-flex h-11 w-11 items-center justify-center rounded-full text-muted hover:bg-surface-2"><CloseIcon /></button>
            </li>
          ))}
        </ul>
      )}
      {err && <p role="alert" className="text-sm text-loss">{t("watchError")}</p>}
      <p className="text-caption text-muted">{t("watchNote")}</p>
    </section>
  );
}

export function AnalyzeHome() {
  const t = useTranslations("analyze");
  return (
    <>
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-sm text-muted">{t("intro")}</p>
      </div>
      <SearchBox />
      <div className="grid gap-4 md:grid-cols-2"><Recent /><Watchlist /></div>
      <p className="text-xs text-muted">{t("disclaimer")}</p>
    </>
  );
}
