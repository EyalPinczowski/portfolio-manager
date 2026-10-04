import { setRequestLocale } from "next-intl/server";
import { DividendsPage } from "@/components/DividendsPage";

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  setRequestLocale((await params).locale);
  return <DividendsPage />;
}
