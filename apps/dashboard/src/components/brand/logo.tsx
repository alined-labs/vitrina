/**
 * Vitrina's mark: an arched shop window, lit, holding a V (DESIGN.md › Logo). The geometry is a
 * 32 grid; the colors are tokens, so the mark never needs a second file for a theme.
 */
const FRAME = "M4 28V14a12 12 0 0 1 24 0v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z";
const GLASS = "M7.5 26.5V14a8.5 8.5 0 0 1 17 0v12.5Z";
const V = "M11 14.5 16 23l5-8.5";

/** The ground the logo sits on: the night band needs the lighter frame. */
type Ground = "light" | "night";

function frameColor(ground: Ground) {
  return ground === "night" ? "var(--color-mark-frame-on-night)" : "var(--color-mark-frame)";
}

/** The mark alone. Decorative: the name is always beside it, in the lockup or the link. */
export function LogoMark({ size = 32, ground = "light" }: { size?: number; ground?: Ground }) {
  const frame = frameColor(ground);
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden="true" focusable="false" className="shrink-0">
      <path d={FRAME} fill={frame} />
      <path d={GLASS} fill="var(--color-mark-glass)" />
      <path d={V} fill="none" stroke={frame} strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Mark and wordmark: the name's cap height is half the mark, 7/32 of it apart. */
export function Logo({ size = 32, ground = "light" }: { size?: number; ground?: Ground }) {
  return (
    <span
      className={`inline-flex items-center font-display font-extrabold leading-none tracking-normal ${
        ground === "night" ? "text-fg-inverse" : "text-fg"
      }`}
      style={{ gap: (size * 7) / 32, fontSize: (size * 0.5) / 0.66 }}
    >
      <LogoMark size={size} ground={ground} />
      Vitrina
    </span>
  );
}

/**
 * The only loading indicator: the window lights up and the V is drawn, in a loop. Without a label
 * it is decorative, for a place whose text already announces the wait.
 */
export function Loader({ size = 24, label }: { size?: number; label?: string }) {
  const name = label ? { role: "status", "aria-label": label } : { "aria-hidden": true };
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} className="shrink-0" {...name}>
      <path d={FRAME} fill="var(--color-mark-frame)" />
      <path className="loader__glass" d={GLASS} fill="var(--color-mark-glass)" />
      <path
        className="loader__v"
        d={V}
        fill="none"
        stroke="var(--color-mark-frame)"
        strokeWidth="3.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
