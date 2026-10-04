import { setRequestLocale } from "next-intl/server";
import { XrayPage } from "@/components/XrayPage";

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  setRequestLocale((await params).locale);
  return <XrayPage />;
}
