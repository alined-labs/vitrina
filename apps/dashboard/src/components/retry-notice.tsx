/** A failure the seller should know about, with the way to ask again. */
export function RetryNotice({
  tone = "warning",
  action = "Intentar ahora",
  onRetry,
  children,
}: {
  tone?: "warning" | "danger";
  action?: string;
  onRetry: () => void;
  children: React.ReactNode;
}) {
  return (
    <div role="alert" className={`notice notice--${tone} space-y-2`}>
      <p>{children}</p>
      <button type="button" onClick={onRetry} className="btn btn--secondary btn--sm">
        {action}
      </button>
    </div>
  );
}
