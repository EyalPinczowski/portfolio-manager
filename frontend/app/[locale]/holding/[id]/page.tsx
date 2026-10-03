import { notFound } from "next/navigation";
import { setRequestLocale } from "next-intl/server";
import { HoldingPage } from "@/components/HoldingPage";

export default async function Page({ params }: { params: Promise<{ locale: string; id: string }> }) {
  const { locale, id } = await params;
  setRequestLocale(locale);
  if (!/^\d+$/.test(id)) notFound();
  return <HoldingPage id={Number(id)} />;
}
