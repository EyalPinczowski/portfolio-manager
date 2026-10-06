"use client";
import { useSyncExternalStore } from "react";

/**
 * Welcome tour state: "seen" once per viewer (localStorage, try/catch, in-memory fallback like lib/guide.ts) plus a
 * session-only flag that re-opens the tour from Settings.
 */
export const TOUR_KEY = "pm.tour.v1";
interface TourState { seen: boolean; forced: boolean }
const DEFAULT: TourState = { seen: false, forced: false };

let state: TourState = DEFAULT;
let loaded = false;
const listeners = new Set<() => void>();

function load(): void {
  if (loaded) return;
  loaded = true;
  try { if (window.localStorage.getItem(TOUR_KEY)) state = { ...state, seen: true }; } catch { /* storage unavailable */ }
}
function set(next: TourState): void {
  state = next;
  listeners.forEach((l) => l());
}

export const tourActions = {
  /** Skip or finish: remember it. */
  markSeen: () => {
    load();
    try { window.localStorage.setItem(TOUR_KEY, "1"); } catch { /* ignore */ }
    set({ seen: true, forced: false });
  },
  /** Re-open from Settings. */
  reopen: () => { load(); set({ ...state, forced: true }); },
  /** Test helper. */
  reset: () => { loaded = true; state = DEFAULT; listeners.forEach((l) => l()); },
};

const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };
const snapshot = (): TourState => { load(); return state; };
const serverSnapshot = (): TourState => ({ seen: true, forced: false });

/** True when the tour should be on screen: first entrance (not seen yet) or re-opened. */
export function useTourOpen(): boolean {
  const s = useSyncExternalStore(subscribe, snapshot, serverSnapshot);
  return !s.seen || s.forced;
}
