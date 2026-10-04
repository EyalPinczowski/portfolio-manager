import { setRequestLocale } from "next-intl/server";
import { TrackRecordPage } from "@/components/TrackRecordPage";

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  setRequestLocale((await params).locale);
  return <TrackRecordPage />;
}
