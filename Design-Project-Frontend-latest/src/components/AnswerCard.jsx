import ConfidenceBadge from "./ConfidenceBadge";
import { SourceList } from "./DisclosureAndSources";
import SafeText from "./SafeText";
import { ErrorNote } from "./Notice";

// One assistant answer: text, confidence (C.4), cautions, ambiguity choices,
// review routing (C.5) and expandable citations (C.2).
export default function AnswerCard({ turn, onPick, disabled }) {
  if (turn.error) return <ErrorNote error={turn.error} />;
  const a = turn.answer;
  return (
    <div className="chat-answer">
      <div className="who">RoboAdvice</div>
      <SafeText text={a.text} />
      <div style={{ marginTop: 10 }}>
        <ConfidenceBadge confidence={a.confidence} label={a.confidenceLabel} abstained={a.abstained} intent={a.intent} />
        {a.decisionId && <span className="hint" style={{ marginLeft: 10 }}>Decision {a.decisionId}</span>}
      </div>

      {a.cautions?.length > 0 && (
        <div className="callout warn">
          {a.cautions.map((c, i) => (
            <div key={i}>{c}</div>
          ))}
        </div>
      )}

      {a.safetyRefused && (
        <div className="callout warn" role="status">
          This request was blocked by our AI safety checks and recorded for officer review. Nothing was sent to the advisory model.
        </div>
      )}

      {a.refusedFormula && (
        <div className="callout warn">
          Scoring formula withheld — confidential business logic. The explanation above is qualitative only.
        </div>
      )}

      {a.requiresReview && (
        <div className="callout warn">
          This answer was flagged for review. No action will be taken on it until a human has checked it.
        </div>
      )}

      {a.ambiguityOptions?.length > 0 && onPick && (
        <div style={{ marginTop: 10 }}>
          <div className="hint">Did you mean:</div>
          <div className="chip-row">
            {a.ambiguityOptions.map((o) => (
              <button key={o} className="chip" disabled={disabled} onClick={() => onPick(o)}>
                {o}
              </button>
            ))}
          </div>
        </div>
      )}

      <SourceList sources={a.sources} />
    </div>
  );
}
