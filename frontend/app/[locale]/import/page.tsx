import { setRequestLocale } from "next-intl/server";
import { ImportPage } from "@/components/ImportPage";

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  setRequestLocale((await params).locale);
  return <ImportPage />;
}
