import type { ReactNode } from "react";

// The real <html>/<body> live in app/[locale]/layout.tsx (the lang and dir attributes depend on the locale).
export default function RootLayout({ children }: { children: ReactNode }) {
  return children;
}
