---
version: 0.1.0
name: Vitrina
description: The design language of Vitrina, a WhatsApp storefront for Colombian sellers. A lit shop window is the mark (an arched window in Caribe teal, its glass lit in mango, holding a V); warm sand neutrals carry the pages; Atkinson Hyperlegible Next sets every text so no price is misread, Bricolage Grotesque sets headings and the name; buttons are pills; motion is quiet.

colors:
  primary: "#0a6170"
  primary-hover: "#0b4f5b"
  on-primary: "#ffffff"
  primary-soft: "#eef9fb"
  primary-fg: "#0b4f5b"
  focus: "#0b7487"
  canvas: "#faf8f5"
  surface: "#ffffff"
  sunken: "#f3f0eb"
  sunken-strong: "#e7e2da"
  night: "#062c33"
  fg: "#201c18"
  fg-muted: "#5e564c"
  fg-subtle: "#736a5f"
  fg-inverse: "#ffffff"
  fg-inverse-muted: "#d6f1f5"
  line: "#e7e2da"
  control: "#968c7f"
  control-hover: "#5e564c"
  mark-frame: "#0a6170"
  mark-frame-on-night: "#2ba9be"
  mark-glass: "#ffae1a"
  success: "#0ca30c"
  success-fg: "#146c3a"
  success-bg: "#e6f4ec"
  warning: "#fab219"
  warning-fg: "#8a5b00"
  warning-bg: "#fff4d9"
  danger: "#d03b3b"
  danger-fg: "#a32424"
  danger-bg: "#fcebea"
  info-fg: "#0b4f5b"
  info-bg: "#eef9fb"

typography:
  display: { fontFamily: Bricolage Grotesque, fontSize: 30px, fontWeight: 600, lineHeight: 36px }
  h1: { fontFamily: Bricolage Grotesque, fontSize: 24px, fontWeight: 600, lineHeight: 32px }
  h2: { fontFamily: Bricolage Grotesque, fontSize: 18px, fontWeight: 600, lineHeight: 28px }
  body: { fontFamily: Atkinson Hyperlegible Next, fontSize: 16px, fontWeight: 400, lineHeight: 24px }
  body-sm: { fontFamily: Atkinson Hyperlegible Next, fontSize: 14px, fontWeight: 400, lineHeight: 20px }
  caption: { fontFamily: Atkinson Hyperlegible Next, fontSize: 12px, fontWeight: 400, lineHeight: 16px }
  label: { fontFamily: Atkinson Hyperlegible Next, fontSize: 14px, fontWeight: 600, lineHeight: 16px }
  wordmark: { fontFamily: Bricolage Grotesque, fontWeight: 800, capHeight: 0.5 × mark }

rounded: { sm: 6px, md: 10px, lg: 16px, xl: 20px, pill: 999px }
spacing: { base: 4px, scale: Tailwind's }

components:
  button-primary: { class: btn btn--primary, backgroundColor: "{colors.primary}", textColor: "{colors.on-primary}", rounded: "{rounded.pill}", height: 40px }
  button-secondary: { class: btn btn--secondary, backgroundColor: "{colors.surface}", borderColor: "{colors.control}", rounded: "{rounded.pill}" }
  button-inverse: { class: btn btn--inverse, backgroundColor: "{colors.surface}", textColor: "{colors.night}", use: on the night band }
  input: { class: input, borderColor: "{colors.control}", rounded: "{rounded.md}", height: 40px }
  card: { class: card, backgroundColor: "{colors.surface}", borderColor: "{colors.line}", rounded: "{rounded.lg}" }
  empty-state: { class: empty, border: dashed "{colors.control}" }
  notice: { class: notice notice--danger|warning|success|info }
  badge: { class: badge badge--success|warning|danger|info|code, rounded: "{rounded.pill}" }
  chip: { class: chip, on: aria-pressed or aria-selected }
  list-item: { class: list-item, on: aria-current }
  nav-link: { class: nav-link, on: aria-current="page" }
  night-band: { class: band-night, backgroundColor: "{colors.night}", rounded: "{rounded.xl}" }
  loader: { component: Loader, the mark animated }
---

# Vitrina design

`src/styles/tokens.css` and `src/styles/components.css` are the source; this file explains them. Change a value in both places.

## Overview

*Vitrina* is a shop window. The identity is a window lit at dusk: a colonial arch in Caribe teal, its glass lit in mango, the V of Vitrina inside it. The V is also the check every generated scene passes before anyone sees it. Around the window: warm sand for the pages, teal for what the seller can do, and status colors only for status.

The products are the color. A page frames the seller's photos and stays quiet around them.

## Tokens: three layers

1. **Primitives** (`:root`, `--caribe-*`, `--mango-*`, `--arena-*`, status steps): raw values. Nothing outside `tokens.css` reads them.
2. **Semantic** (Tailwind `@theme`): what a color is for, as utilities: `bg-canvas`, `bg-surface`, `bg-sunken`, `text-fg`, `text-fg-muted`, `text-fg-subtle`, `border-line`, `border-control`, `bg-primary`, `text-primary-fg`, `bg-danger-bg`, `text-danger-fg`… `--color-*: initial` removes Tailwind's palette, so `bg-zinc-100` draws nothing.
3. **Component** (`:root`, `--btn-*`, `--input-*`, `--card-*`…): sizes and radii the classes in `components.css` read.

ESLint (`eslint.config.mjs`) rejects a raw hex color or a Tailwind palette class in `src/`.

## Color

| Pair | Contrast | Use |
| --- | --- | --- |
| `fg` on `canvas` / `sunken` | 16.0 / 14.9 | Text |
| `fg-muted` on `surface` / `canvas` / `sunken` | 7.2 / 6.8 / 6.4 | Descriptions, labels |
| `fg-subtle` on `surface` / `canvas` / `sunken` | 5.3 / 5.0 / 4.7 | Metadata, placeholders: the floor |
| `on-primary` on `primary` | 7.1 | Primary buttons, chips that are on |
| `primary` as text on `canvas` | 6.7 | Links in brand color |
| `primary-fg` on `primary-soft` | 8.6 | The open row, the current page, info |
| `success-fg` / `warning-fg` / `danger-fg` on their `-bg` | 5.7 / 5.4 / 6.4 | Notices and badges |
| `fg-inverse` / `fg-inverse-muted` on `night` | 14.8 / 12.5 | The night band |
| `control` against `surface` / `canvas` | 3.3 / 3.1 | Field and secondary-button borders (≥ 3:1) |
| `focus` against `canvas` | 5.1 | The focus ring |

- **Mango is light, not paint.** It fills the glass of the mark and the covers. It is never a UI fill or text color: it sits next to the warning amber.
- **Status colors are for status**, always with words (a notice's sentence, a badge's label, an icon and a label in metrics). The warning icon is drawn in ink: white on amber is 1.8:1.
- Light theme only for now (`color-scheme: light`). A dark theme overrides only the semantic layer.

## Typography

- **Atkinson Hyperlegible Next** (variable 200–800) for all text. It was drawn for low-vision readers; its 0, O, 1, l and I never look alike, and Vitrina's promise is that a price is never wrong. `font-variant-numeric: tabular-nums` in tables and on `.tabular`.
- **Bricolage Grotesque** (variable, opsz 12–96, wght 500–800) for `h1`–`h3` (set in the base layer), the night band's title and the wordmark at 800.
- Both are self-hosted, latin subset (Spanish included), with their OFL licences: `src/fonts/README.md`.
- Sentence case. Headings use `text-wrap: balance`, paragraphs `pretty`.

## Shape, layout and motion

- Buttons, badges and chips are pills: they echo the arch. Fields 10px, cards 16px, the night band 20px.
- The arch itself is kept for the mark, the loader and covers. Product photos are never cropped by it: images are shown whole.
- Spacing is Tailwind's 4px scale. Pages sit in `max-w-4xl` to `max-w-6xl` columns.
- Motion: 120 / 200 / 320 ms, ease-out `cubic-bezier(.22,1,.36,1)`, no bounce; a pressed button scales to .98. `prefers-reduced-motion` zeroes the durations and stops the loader.

## Components

| Class | What |
| --- | --- |
| `btn` + `btn--primary` / `--secondary` / `--ghost` / `--inverse`, `--sm` / `--lg`, `--block` | Actions. One primary per view; disabled at 45 % opacity |
| `input` (+ `input--sm`) | Text fields, selects, text areas, with a visible label |
| `card`, `card--link`, `card--chosen`, `empty` | Surfaces; a dashed `empty` box for "nothing here yet" |
| `notice` + `--danger` / `--warning` / `--success` / `--info` | A sentence that needs the seller's attention; errors carry `role="alert"` |
| `badge` + `--success` / `--warning` / `--danger` / `--info` / `--code` | Static labels; `--code` for ad codes such as `#A7` |
| `chip` | Filters and tabs; `aria-pressed` or `aria-selected` turns it on |
| `list-item`, `nav-link` | Rows of a master list and the panel navigation; `aria-current` marks the open one |
| `progress` | A bar with `role="progressbar"` and its value |
| `band-night` | The one dark band, for the public page's call to action |
| `Logo`, `LogoMark`, `Loader` (`src/components/brand/logo.tsx`) | The lockup, the mark, and the mark animated as the only loading indicator |

## Logo

- **Construction** (32 grid): the frame is an arch 24 wide from y 2 to 30 (radius 12, bottom corners 2); the glass is inset 3.5; the V runs (11, 14.5) → (16, 23) → (21, 14.5) at stroke 3.4, round. In the lockup the name's cap height is half the mark, 7/32 of it away, set in Bricolage Grotesque 800.
- **Grounds**: the frame is `mark-frame` on light grounds and `mark-frame-on-night` on dark ones; the glass is always mango.
- **App files** (`src/app/`): `icon.svg` switches its frame with the color scheme; `favicon.ico` (16, 32, 48), `apple-icon.png` (opaque, 180), `opengraph-image.png` and `twitter-image.png` (1200 × 630) for link previews.
- Never recolor, outline, rotate or stretch the mark, and never set the name in another typeface.

## Voice (es-CO)

Talk to the seller as *tú*, warm and short. Say what happens and what to do next. Sentence case, no exclamation marks, `·` between parts. Numbers only when they are real. No invented customers, reviews or results (constitution Art. IV.2). Generated images always carry «Imagen generada con IA».

## Before shipping a page

- [ ] Only tokens and component classes; `pnpm run check` passes (it includes the lint rule).
- [ ] Every target at least 24 × 24 px; focus visible on everything.
- [ ] Text at 4.5:1 and borders that identify a control at 3:1 (table above).
- [ ] Every field has a visible label; errors are sentences with `role="alert"`.
- [ ] Meaning never by color alone.
- [ ] Reduced motion honored; only the loader moves on its own.
- [ ] No horizontal scroll at 375 px; checked at 390, 768 and 1440 in the production build.
