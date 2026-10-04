/** Applies the saved theme. "system" removes the override so the phone's setting decides (prefers-color-scheme). */
export function applyTheme(theme: "system" | "light" | "dark" | undefined): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.dataset.theme = theme;
  else delete root.dataset.theme;
}
