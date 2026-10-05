import { useId } from "react";

/** The Holdwise mark: an H whose tops and crossbar climb along one rising line (same art as public/icons/icon.svg). */
export function LogoMark({ className = "h-8 w-8" }: { className?: string }) {
  const id = useId();
  return (
    <svg viewBox="0 0 120 120" aria-hidden="true" focusable="false" className={className}>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#10b981" />
          <stop offset="1" stopColor="#065f46" />
        </linearGradient>
      </defs>
      <rect width="120" height="120" rx="28" fill={`url(#${id})`} />
      <g transform="translate(0 7)" fill="#fff" stroke="#fff" strokeWidth="3" strokeLinejoin="round">
        <polygon points="28,40 44,34 44,90 28,90" />
        <polygon points="76,22 92,16 92,90 76,90" />
        <polygon points="44,59 76,47 76,61 44,73" />
      </g>
    </svg>
  );
}

/** Brand name; "Holdwise" renders as "Hold" + green "wise". The text content stays exactly the name. */
export function Wordmark({ name }: { name: string }) {
  if (name !== "Holdwise") return <>{name}</>;
  return (
    <span>
      <span data-wordmark="">Hold</span>
      <span className="text-brand-text">wise</span>
    </span>
  );
}
