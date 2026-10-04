import { setRequestLocale } from "next-intl/server";
import { HoldingRoute } from "@/components/HoldingRoute";

// Static export has no dynamic [id] segment: the holding is chosen by `?id=` (read on the client).
export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  setRequestLocale((await params).locale);
  return <HoldingRoute />;
}
