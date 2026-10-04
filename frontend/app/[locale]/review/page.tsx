import { setRequestLocale } from "next-intl/server";
import { ReviewPage } from "@/components/ReviewPage";

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  setRequestLocale((await params).locale);
  return <ReviewPage />;
}
