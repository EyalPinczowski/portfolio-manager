import { setRequestLocale } from "next-intl/server";
import { ComingSoon } from "@/components/ComingSoon";

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  setRequestLocale((await params).locale);
  return <ComingSoon page="alerts" />;
}
