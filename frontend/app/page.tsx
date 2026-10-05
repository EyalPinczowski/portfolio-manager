import { routing } from "@/i18n/routing";
import { RootRedirect } from "@/components/RootRedirect";

// Static-export friendly root: a meta refresh to the default locale (works without JS) plus a client
// redirect that respects the browser language.
export default function RootPage() {
  const target = `/${routing.defaultLocale}/`;
  return (
    <html lang={routing.defaultLocale}>
      <head>
        <meta httpEquiv="refresh" content={`0;url=${target}`} />
        <meta name="robots" content="noindex" />
        <title>Holdwise</title>
      </head>
      <body>
        <RootRedirect />
        <noscript>
          {routing.locales.map((l) => (
            <p key={l}><a href={`/${l}/`}>{l}</a></p>
          ))}
        </noscript>
      </body>
    </html>
  );
}
