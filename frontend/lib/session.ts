import { mutate } from "swr";

export const PORTFOLIO_CHOICE_KEY = "pm.portfolio";

/**
 * Forget everything that belongs to the signed-in user: every SWR cache entry and the stored portfolio choice.
 * Called on login, logout and account deletion so one user never sees another's data (shared device).
 */
export async function clearClientState(): Promise<void> {
  try { window.localStorage.removeItem(PORTFOLIO_CHOICE_KEY); } catch { /* storage unavailable */ }
  await mutate(() => true, undefined, { revalidate: false });
}
