import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import "./globals.css";

// Self-hosted, latin subset (src/fonts/README.md): Atkinson Hyperlegible Next for every text,
// Bricolage Grotesque for headings and the wordmark.
const atkinson = localFont({
  src: "../fonts/atkinson-hyperlegible-next-latin.woff2",
  weight: "200 800",
  display: "swap",
  variable: "--font-atkinson",
});
const bricolage = localFont({
  src: "../fonts/bricolage-grotesque-latin.woff2",
  weight: "500 800",
  display: "swap",
  variable: "--font-bricolage",
});

export const metadata: Metadata = {
  // Link previews need absolute addresses: the dashboard's domain (wrangler.jsonc).
  metadataBase: new URL("https://vitrina.alinedlabs.co"),
  title: "Vitrina",
  description: "Tus fotos se vuelven anuncios, tus anuncios se vuelven ventas.",
  applicationName: "Vitrina",
  openGraph: { siteName: "Vitrina", locale: "es_CO", type: "website" },
  twitter: { card: "summary_large_image" },
};

// The browser bar takes the page ground; a meta tag needs the literal value of --color-canvas.
// eslint-disable-next-line no-restricted-syntax
export const viewport: Viewport = { themeColor: "#faf8f5" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es-CO" className={`${atkinson.variable} ${bricolage.variable}`}>
      <body>{children}</body>
    </html>
  );
}
