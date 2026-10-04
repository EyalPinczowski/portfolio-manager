"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { mutate } from "swr";
import { api, ApiError } from "@/lib/api";

/** First-run form: a new account has no portfolio, and holdings are always imported into one. */
export function CreatePortfolio({ onCreated }: { onCreated?: (id: number) => void }) {
  const t = useTranslations("createPortfolio");
  const [name, setName] = useState("");
  const [currency, setCurrency] = useState<"ILS" | "USD">("ILS");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true); setError(false);
    try {
      const p = await api.createPortfolio({ name: name.trim(), base_currency: currency });
      await mutate(() => true);
      onCreated?.(p.id);
    } catch (err) {
      setError(true);
      if (!(err instanceof ApiError)) console.error("create portfolio failed");
    } finally { setBusy(false); }
  };

  return (
    <form onSubmit={submit} className="card space-y-3" aria-label={t("title")}>
      <h2 className="text-lg font-bold">{t("title")}</h2>
      <p className="text-sm text-muted">{t("intro")}</p>
      <div>
        <label htmlFor="cp-name" className="label">{t("name")}</label>
        <input id="cp-name" className="input" required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div>
        <label htmlFor="cp-currency" className="label">{t("currency")}</label>
        <select id="cp-currency" className="input" value={currency} onChange={(e) => setCurrency(e.target.value as "ILS" | "USD")}>
          <option value="ILS">ILS</option>
          <option value="USD">USD</option>
        </select>
      </div>
      {error && <p role="alert" className="text-sm font-medium text-loss">{t("error")}</p>}
      <button type="submit" className="btn-primary" disabled={busy || !name.trim()}>{t("create")}</button>
    </form>
  );
}
