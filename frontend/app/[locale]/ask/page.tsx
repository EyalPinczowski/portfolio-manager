import { setRequestLocale } from "next-intl/server";
import { AskPage } from "@/components/AskPage";

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  setRequestLocale((await params).locale);
  return <AskPage />;
}
