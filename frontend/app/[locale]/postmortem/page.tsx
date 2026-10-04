import { setRequestLocale } from "next-intl/server";
import { PostmortemRoute } from "@/components/PostmortemRoute";

// Static export: portfolio and period come from the query string (read on the client).
export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  setRequestLocale((await params).locale);
  return <PostmortemRoute />;
}
