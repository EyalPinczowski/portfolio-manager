"use client";
import { useSyncExternalStore } from "react";

/**
 * First-run guide state. Only a per-viewer convenience: "dismissed", "started" and which portfolios the user picked
 * a risk level for in the guide. localStorage is wrapped in try/catch; an in-memory copy keeps it working when
 * storage is blocked (it then lasts until the page is reloaded).
 */
export interface GuideState {
  dismissed: boolean;
  /** The guide has been shown once; it stays until finished or dismissed, even after holdings exist. */
  started: boolean;
  /** Portfolio ids whose risk level the user chose inside the guide (the server always stores a default). */
  riskChosen: number[];
}
export const GUIDE_KEY = "pm.guide.v1";
const DEFAULT: GuideState = { dismissed: false, started: false, riskChosen: [] };

let state: GuideState = DEFAULT;
let loaded = false;
const listeners = new Set<() => void>();

function load(): void {
  if (loaded) return;
  loaded = true;
  try {
    const raw = window.localStorage.getItem(GUIDE_KEY);
    if (raw) {
      const p = JSON.parse(raw) as Partial<GuideState>;
      state = {
        dismissed: p.dismissed === true,
        started: p.started === true,
        riskChosen: Array.isArray(p.riskChosen) ? p.riskChosen.filter((x): x is number => typeof x === "number") : [],
      };
    }
  } catch { /* storage unavailable or corrupt: use defaults */ }
}

function set(next: GuideState): void {
  state = next;
  try { window.localStorage.setItem(GUIDE_KEY, JSON.stringify(next)); } catch { /* ignore */ }
  listeners.forEach((l) => l());
}

export const guideActions = {
  dismiss: () => { load(); set({ ...state, dismissed: true }); },
  /** Re-open from Settings. */
  reopen: () => { load(); set({ ...state, dismissed: false, started: true }); },
  markStarted: () => { load(); if (!state.started) set({ ...state, started: true }); },
  markRiskChosen: (pid: number) => { load(); if (!state.riskChosen.includes(pid)) set({ ...state, riskChosen: [...state.riskChosen, pid] }); },
  /** Test helper. */
  reset: () => { loaded = true; state = DEFAULT; listeners.forEach((l) => l()); },
};

const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };
const snapshot = (): GuideState => { load(); return state; };
const serverSnapshot = (): GuideState => DEFAULT;

export function useGuideState(): GuideState {
  return useSyncExternalStore(subscribe, snapshot, serverSnapshot);
}
