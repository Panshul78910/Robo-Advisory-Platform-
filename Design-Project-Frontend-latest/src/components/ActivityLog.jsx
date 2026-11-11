// A.7 / C.6: the portfolio event trail. Corrections are new rows, never edits,
// so a rebalance and the revert that undid it both appear -- the revert links
// back to the event it reversed rather than deleting it.
export default function ActivityLog({ events, error }) {
  return (
    <section className="RoboAdvisor" data-testid="activity-log">
      <div className="RoboAdvisor-head">
        <h2>Activity</h2>
      </div>
      <div className="RoboAdvisor-body tight">
        {error ? (
          <div className="empty-state">{error}</div>
        ) : !events?.length ? (
          <div className="empty-state">No portfolio activity yet.</div>
        ) : (
          <table className="RoboAdvisor-table">
            <thead>
              <tr>
                <th className="num">#</th>
                <th>Event</th>
                <th>Session date</th>
                <th>Reverses</th>
                <th>Recorded</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.eventId} data-testid={`event-${e.eventType}`}>
                  <td className="num">{e.seq}</td>
                  <td>
                    <strong>{e.eventType}</strong>
                  </td>
                  <td>{e.sessionDate}</td>
                  <td>
                    {e.revertsEventId ? (
                      <code style={{ fontSize: 11 }}>{e.revertsEventId.slice(0, 8)}…</code>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>{e.createdAt ? new Date(e.createdAt).toLocaleString() : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
