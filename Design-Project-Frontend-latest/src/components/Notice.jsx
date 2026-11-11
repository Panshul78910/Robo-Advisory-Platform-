// Friendly, non-leaky error + empty states (B.6: user-facing error states).
export function ErrorNote({ error, onRetry }) {
  if (!error) return null;
  return (
    <div className="callout bad" role="alert">
      <div>{error.message || "Something went wrong."}</div>
      {error.correlationId && <div className="hint">Reference: {error.correlationId}</div>}
      {onRetry && (
        <button className="btn secondary" style={{ marginTop: 8 }} onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Loading({ what = "Loading" }) {
  return <div className="hint loading" role="status">{what}</div>;
}
