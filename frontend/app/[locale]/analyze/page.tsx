import { setRequestLocale } from "next-intl/server";
import { AnalyzeRoute } from "@/components/AnalyzeRoute";

// Static export: the symbol is chosen by `?symbol=` (read on the client).
export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  setRequestLocale((await params).locale);
  return <AnalyzeRoute />;
}
