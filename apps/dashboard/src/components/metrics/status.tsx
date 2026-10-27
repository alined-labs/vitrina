/** State shown as icon + label, never colour alone; the colours are the reserved status steps. */
export type Status = "good" | "warning" | "critical" | "neutral";

const COLOR: Record<Status, string> = {
  good: "#0ca30c",
  warning: "#fab219",
  critical: "#d03b3b",
  neutral: "#71717a",
};

const ICON: Record<Status, string> = {
  good: "M3.5 8.5l3 3 6-7",
  warning: "M8 3.5v5.5M8 12v.5",
  critical: "M4 4l8 8M12 4l-8 8",
  neutral: "M4 8h8",
};

export function StatusIcon({ status }: { status: Status }) {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" className="shrink-0">
      <circle cx="8" cy="8" r="8" fill={COLOR[status]} />
      <path d={ICON[status]} stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
    </svg>
  );
}

export function StatusLabel({ status, children }: { status: Status; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-zinc-700">
      <StatusIcon status={status} />
      {children}
    </span>
  );
}
