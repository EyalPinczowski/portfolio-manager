import type { ReactNode } from "react";

/** Glyphs for the coloured row tiles in Settings (24x24 stroke, decorative). */
function G({ children }: { children: ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" className="h-[18px] w-[18px]">
      {children}
    </svg>
  );
}

export const GlobeGlyph = () => <G><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.6 2.6 2.6 15.4 0 18M12 3c-2.6 2.6-2.6 15.4 0 18" /></G>;
export const SunGlyph = () => <G><circle cx="12" cy="12" r="4" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4" /></G>;
export const CoinGlyph = () => <G><circle cx="12" cy="12" r="9" /><path d="M14.5 9.2c-.6-.8-1.5-1.2-2.6-1.2-1.5 0-2.5.8-2.5 2 0 3 5.2 1.4 5.2 4.2 0 1.2-1.1 2-2.7 2-1.2 0-2.2-.5-2.8-1.4M12 6.5V8M12 16v1.5" /></G>;
export const HashGlyph = () => <G><path d="M9 4 7 20M17 4l-2 16M4.5 9h16M3.5 15h16" /></G>;
export const CalendarGlyph = () => <G><rect x="4" y="5.5" width="16" height="14.5" rx="2.5" /><path d="M4 10h16M8.5 3.5v4M15.5 3.5v4" /></G>;
export const BellGlyph = () => <G><path d="M6 16.5V11a6 6 0 1 1 12 0v5.5l1.5 2h-15L6 16.5Z" /><path d="M10 21h4" /></G>;
export const ClockGlyph = () => <G><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></G>;
export const MoonGlyph = () => <G><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z" /></G>;
export const SendGlyph = () => <G><path d="M21 4 3 11l6 2.5L11.5 20 21 4Z" /><path d="m9 13.5 5-4" /></G>;
export const SparkGlyph = () => <G><path d="M12 3v3M12 18v3M3 12h3M18 12h3" /><path d="m12 8 1.6 2.4L16 12l-2.4 1.6L12 16l-1.6-2.4L8 12l2.4-1.6L12 8Z" /></G>;
export const ShieldGlyph = () => <G><path d="M12 3 5 6v5.5c0 4.2 2.9 7.6 7 9 4.1-1.4 7-4.8 7-9V6l-7-3Z" /><path d="m9 12 2 2 4-4" /></G>;
export const DeviceGlyph = () => <G><rect x="7" y="3" width="10" height="18" rx="2.5" /><path d="M11 18h2" /></G>;
export const LockGlyph = () => <G><rect x="5" y="10.5" width="14" height="9.5" rx="2.5" /><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5M12 14.5v2" /></G>;
export const PulseGlyph = () => <G><path d="M3 12h4l2-6 4 12 2-6h6" /></G>;
export const KeyGlyph = () => <G><circle cx="8" cy="15" r="3.5" /><path d="m10.5 12.5 8-8M15.5 7.5l2 2M13.5 9.5l2 2" /></G>;
export const UserGlyph = () => <G><circle cx="12" cy="8.5" r="3.5" /><path d="M5 20c.8-3.6 3.5-5.5 7-5.5s6.2 1.9 7 5.5" /></G>;
export const DownloadGlyph = () => <G><path d="M12 4v11M7.5 11 12 15.5 16.5 11M5 20h14" /></G>;
export const LogoutGlyph = () => <G><path d="M10 4H6a1.5 1.5 0 0 0-1.5 1.5v13A1.5 1.5 0 0 0 6 20h4M15 8l4 4-4 4M19 12H9" /></G>;
export const TrashGlyph = () => <G><path d="M5 7h14M10 7V4.5h4V7M7 7l.8 12.5h8.4L17 7" /></G>;
export const BookGlyph = () => <G><path d="M5 5.5A1.5 1.5 0 0 1 6.5 4H19v14.5H6.5A1.5 1.5 0 0 0 5 20V5.5Z" /><path d="M5 20a1.5 1.5 0 0 0 1.5 0.5H19" /></G>;
