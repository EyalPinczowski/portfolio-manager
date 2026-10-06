import "@testing-library/jest-dom/vitest";

// The welcome tour opens on a first visit; most tests are not about it, so mark it seen. Its own tests clear this.
import { beforeEach } from "vitest";
beforeEach(() => { try { window.localStorage.setItem("pm.tour.v1", "1"); } catch { /* no storage */ } });
