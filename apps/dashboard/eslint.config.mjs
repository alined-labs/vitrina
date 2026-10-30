import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// Pages use Vitrina's tokens and component classes (DESIGN.md), never Tailwind's default palette
// or a raw color: the theme defines none of them, so such a class would silently draw nothing.
const PALETTE =
  "(^|[\\s:])(bg|text|border|ring|divide|fill|stroke|outline|from|via|to|decoration|placeholder|accent|caret|shadow)-" +
  "((zinc|slate|gray|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]|(white|black)([^\\w-]|$))";
const HEX = "^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$";
const tokensOnly = {
  files: ["src/**/*.{ts,tsx}"],
  rules: {
    "no-restricted-syntax": [
      "error",
      { selector: `Literal[value=/${HEX}/]`, message: "Raw color: use a token from src/styles/tokens.css." },
      { selector: `Literal[value=/${PALETTE}/]`, message: "Tailwind palette class: use a Vitrina token or component class (DESIGN.md)." },
      { selector: `TemplateElement[value.raw=/${PALETTE}/]`, message: "Tailwind palette class: use a Vitrina token or component class (DESIGN.md)." },
    ],
  },
};

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  tokensOnly,
  globalIgnores([
    "node_modules/**",
    ".next/**",
    ".open-next/**",
    ".wrangler/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    "src/lib/api/schema.d.ts",
  ]),
]);

export default eslintConfig;
