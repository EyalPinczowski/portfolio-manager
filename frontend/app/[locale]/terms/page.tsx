import { setRequestLocale } from "next-intl/server";
import { TermsPage } from "@/components/TermsPage";

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  setRequestLocale((await params).locale);
  return <TermsPage />;
}
