# Fonts

Both are self-hosted variable fonts under the SIL Open Font License 1.1 (the `-OFL.txt` beside
each), latin subset only: it covers Spanish (á é í ó ú ñ ü ¿ ¡ « » · …).

| File | Family | Source | Changes |
| --- | --- | --- | --- |
| `atkinson-hyperlegible-next-latin.woff2` | Atkinson Hyperlegible Next, wght 200–800 | Google Fonts, `fonts.gstatic.com/s/atkinsonhyperlegiblenext/v7`, latin | none |
| `bricolage-grotesque-latin.woff2` | Bricolage Grotesque, opsz 12–96, wght 500–800 | Google Fonts, `fonts.gstatic.com/s/bricolagegrotesque/v9`, latin | width axis pinned to 100 and weight limited to 500–800 with fontTools `varLib.instancer` (131 KB → 71 KB) |

`src/app/layout.tsx` loads them with `next/font/local`, which adds metric-matched fallbacks.
Both have tabular figures (`tnum`), used for tables and prices.
