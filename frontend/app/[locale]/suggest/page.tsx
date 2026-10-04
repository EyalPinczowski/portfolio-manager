import { setRequestLocale } from "next-intl/server";
import { SuggestPage } from "@/components/SuggestPage";

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  setRequestLocale((await params).locale);
  return <SuggestPage />;
}
