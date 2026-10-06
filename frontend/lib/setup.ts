"use client";
import useSWR from "swr";
import { api, isMock, type Holding, type Portfolio } from "./api";
import { useGuideState } from "./guide";
import { useMe, usePortfolios } from "./hooks";

/** The required setup steps (Telegram is optional and never blocks). */
export type CoreStep = "disclaimer" | "portfolio" | "import" | "risk" | "horizon";

/** Facts the server knows, so the setup state holds on every device. */
export function useSetupFacts(fallback: Portfolio[] = []) {
  const { data: me, error: meErr } = useMe();
  const { data: live, error: pErr } = usePortfolios();
  const guide = useGuideState();
  const list = live ?? fallback;
  const ids = list.map((p) => p.id);
  const { data: lists, error: hErr } = useSWR(ids.length ? ["guide-holdings", ...ids] : null, async () =>
    Promise.all(ids.map(async (pid) => ({ pid, holdings: await api.holdings(pid) }))));
  const rows: { pid: number; h: Holding }[] = (lists ?? []).flatMap((l) => l.holdings.map((h) => ({ pid: l.pid, h })));
  const first = list[0];
  const done: Record<CoreStep, boolean> = {
    disclaimer: me?.disclaimer_accepted === true,
    portfolio: list.length > 0,
    import: rows.length > 0,
    risk: !!first && (!!first.risk_chosen_at || guide.riskChosen.includes(first.id)), // local list is only a harmless fallback
    horizon: rows.length > 0 && rows.every((r) => !!r.h.horizon),
  };
  const loading = !me || (!live && !pErr) || (ids.length > 0 && !lists && !hErr);
  const failed = !!meErr || !!pErr || !!hErr;
  return { list, rows, first, done, loading, failed, complete: Object.values(done).every(Boolean) };
}

export type SetupGateState = "loading" | "blocked" | "open";

/** Mock mode demo data is deliberately incomplete, so the gate is off there unless localStorage pm.mock = "gate". */
function bypassed(): boolean {
  if (!isMock()) return false;
  try { return window.localStorage.getItem("pm.mock") !== "gate"; } catch { return true; }
}

/** "blocked" until every required step is done. Load errors never lock the user out. */
export function useSetupGate(): SetupGateState {
  const f = useSetupFacts();
  if (bypassed() || f.failed) return "open";
  if (f.loading) return "loading";
  return f.complete ? "open" : "blocked";
}
