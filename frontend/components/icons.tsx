import type { ReactNode } from "react";

/** Small inline icon set (24x24, stroke). Decorative: always aria-hidden; the label is next to the icon. */
function Svg({ children, className = "h-6 w-6" }: { children: ReactNode; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      {children}
    </svg>
  );
}
type P = { className?: string };

export const HomeIcon = (p: P) => <Svg {...p}><path d="M3 11.5 12 4l9 7.5" /><path d="M5.5 10v9.5h13V10" /><path d="M10 19.5v-5h4v5" /></Svg>;
export const PortfolioIcon = (p: P) => <Svg {...p}><path d="M12 3a9 9 0 1 0 9 9h-9V3Z" /><path d="M15.5 3.5A9 9 0 0 1 20.5 8.5H15.5V3.5Z" /></Svg>;
export const AnalyzeIcon = (p: P) => <Svg {...p}><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4.5 4.5" /><path d="M8.5 12.5 10 11l1.5 1.5L13.5 10" /></Svg>;
export const BellIcon = (p: P) => <Svg {...p}><path d="M6 16.5V11a6 6 0 1 1 12 0v5.5l1.5 2h-15L6 16.5Z" /><path d="M10 21h4" /></Svg>;
export const SettingsIcon = (p: P) => <Svg {...p}><circle cx="12" cy="12" r="3" /><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M18.7 5.3l-2.1 2.1M7.4 16.6l-2.1 2.1" /></Svg>;
export const PlusIcon = (p: P) => <Svg {...p}><path d="M12 5v14M5 12h14" /></Svg>;
export const CloseIcon = (p: P) => <Svg {...p}><path d="M6 6l12 12M18 6 6 18" /></Svg>;
export const ReviewIcon = (p: P) => <Svg {...p}><path d="M9 4h6l1 2h3v14H5V6h3l1-2Z" /><path d="m9 13 2 2 4-4" /></Svg>;
export const SuggestIcon = (p: P) => <Svg {...p}><path d="M12 3v3M12 18v3M3 12h3M18 12h3" /><path d="m12 8 1.6 2.4L16 12l-2.4 1.6L12 16l-1.6-2.4L8 12l2.4-1.6L12 8Z" /></Svg>;
export const CameraIcon = (p: P) => <Svg {...p}><path d="M4 8h3l1.5-2h7L17 8h3v11H4V8Z" /><circle cx="12" cy="13" r="3.2" /></Svg>;
export const CheckIcon = (p: P) => <Svg {...p}><path d="m5 12.5 4.5 4.5L19 7.5" /></Svg>;
/** Directional: flips in right-to-left layouts. */
export const ChevronIcon = ({ className = "h-5 w-5" }: P) => <Svg className={`${className} rtl:-scale-x-100`}><path d="m9 6 6 6-6 6" /></Svg>;
