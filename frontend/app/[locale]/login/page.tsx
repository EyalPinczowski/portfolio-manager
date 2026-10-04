import { setRequestLocale } from "next-intl/server";
import { AuthForm } from "@/components/AuthForm";

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  setRequestLocale((await params).locale);
  return <AuthForm mode="login" />;
}
