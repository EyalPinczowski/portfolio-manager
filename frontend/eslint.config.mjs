import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts", "coverage/**", "public/**", "scripts/**", "e2e/.site/**", "e2e/.results/**", "e2e/serve.mjs", "lib/api-schema.d.ts"]),
]);
