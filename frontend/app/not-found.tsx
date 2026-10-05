// The static export's 404.html. Self-contained <html>: the root layout renders only children, and the locale
// layout (which sets lang/dir) does not apply to unknown paths.
export default function NotFound() {
  /* Plain anchors on purpose: this page sits outside the locale router and may be served for any path. */
  /* eslint-disable @next/next/no-html-link-for-pages */
  return (
    <html lang="he" dir="rtl">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: 0, padding: "2rem 1rem", textAlign: "center" }}>
        <main>
          <h1>העמוד לא נמצא / Page not found</h1>
          <p>
            <a href="/he/">חזרה לדף הראשי</a>
            {" · "}
            <a href="/en/" lang="en" dir="ltr">Back to home</a>
          </p>
        </main>
      </body>
    </html>
  );
}
