import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAppData } from "../context/AppDataContext";
import { useLoad } from "../hooks";
import { human, portfolioName } from "../lib/format";
import AnswerCard from "../components/AnswerCard";
import CkaCheck from "../components/CkaCheck";
import SafeText from "../components/SafeText";
import { DisclosureBox } from "../components/DisclosureAndSources";
import { ErrorNote, Loading } from "../components/Notice";

const QUICK = [
  "Why was I assigned this risk category?",
  "What is the scoring formula?",
  "What is rebalancing and why does it matter?",
  "What does my portfolio hold?",
];

export default function ProfilingPage() {
  const { profiling, setProfiling, turns, ask, pending } = useAppData();
  const [text, setText] = useState("");
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const endRef = useRef(null);
  const elig = useLoad(() => api.getEligibility(), [profiling.result]);

  // Braces matter: newer browsers return a Promise from scrollIntoView, and React
  // would try to call any returned value as the effect's cleanup.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [profiling.turns.length]);

  const add = (role, content) => ({ role, content });

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.profilingStart();
      setProfiling({
        started: true,
        turns: [add("assistant", r.reply)],
        result: r.complete ? r : null,
        last: r,
      });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function sendMessage(message) {
    if (!message || busy) return;
    setBusy(true);
    setError(null);
    setProfiling((p) => ({ ...p, turns: [...p.turns, add("user", message)] }));
    try {
      const r = await api.profilingMessage(message);
      setProfiling((p) => ({
        ...p,
        turns: [...p.turns, add("assistant", r.reply)],
        last: r,
        result: r.complete ? r : null,
      }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  function send(e) {
    e.preventDefault();
    const message = text.trim();
    setText("");
    sendMessage(message);
  }

  async function submitAsk(ev) {
    ev.preventDefault();
    const q = question;
    setQuestion("");
    await ask(q);
  }

  const { started, turns: profileTurns, result, last } = profiling;
  const e = elig.data;

  return (
    <div className="main">
      <div className="page-header">
        <h1>Risk conversation</h1>
        <p>One short conversation to work out your risk category — there&apos;s no separate questionnaire. Ask about the formula or your category here as well.</p>
      </div>

      <section className="RoboAdvisor">
        <div className="RoboAdvisor-head"><h2>Establish your risk category</h2></div>
        <div className="RoboAdvisor-body">
          {!started && (
            <>
              <p>
                I'll use the details already in your <Link to="/profile">profile</Link> and only ask about what's missing.
              </p>
              <button className="btn" onClick={start} disabled={busy}>{busy ? "Starting…" : "Start the conversation"}</button>
            </>
          )}

          {started && (
            <div className="msg-list">
              {profileTurns.map((t, i) => (
                <div key={i} className={`msg ${t.role}`}>
                  <div className="who">{t.role === "user" ? "You" : "RoboAdvice"}</div>
                  <SafeText text={t.content} />
                </div>
              ))}
              {busy && (
                <div className="msg typing">
                  <div className="who">RoboAdvice</div>
                  <Loading what="Thinking" />
                </div>
              )}
              <div ref={endRef} />
            </div>
          )}

          <ErrorNote error={error} />

          {started && !result && last?.inconsistencies?.length > 0 && (
            <div className="callout warn">
              <strong>Before I can assign a category, I need to clear something up</strong>
              {last.inconsistencies.map((i) => <div key={i.code} style={{ marginTop: 4 }}>{i.question}</div>)}
            </div>
          )}

          {started && !result && (
            <form onSubmit={send} className="chat-input-row">
              <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type your answer…" maxLength={4000} disabled={busy} aria-label="Your answer" autoFocus />
              <button className="btn" type="submit" disabled={busy || !text.trim()}>{busy ? "…" : "Send"}</button>
            </form>
          )}
        </div>
      </section>

      {result && (
        <section className="RoboAdvisor">
          <div className="RoboAdvisor-head"><h2>Your result</h2></div>
          <div className="stat-row">
            <div className="stat"><div className="stat-label">Risk category</div><div className="stat-value">{human(result.riskCategory)}</div></div>
            <div className="stat"><div className="stat-label">Eligible portfolios</div><div className="stat-value" style={{ fontSize: 15 }}>
              {result.eligiblePortfolios.map((c) => `${c} ${portfolioName(c)}`).join(", ")}</div></div>
            <div className="stat"><div className="stat-label">CKA-style check</div><div className="stat-value">{e?.ckaPassed ? "Passed" : "Not passed"}</div></div>
          </div>
          <div className="RoboAdvisor-body">
            <p>
              Next: review your options and invest in <Link to="/portfolio">Portfolio & trading</Link>.
              {" "}Want to run it again? <button className="linklike" onClick={start}>Start over</button>
            </p>
            {e && !e.ckaPassed && <CkaCheck onPassed={() => elig.reload()} />}
            <DisclosureBox />
          </div>
        </section>
      )}

      <section className="RoboAdvisor">
        <div className="RoboAdvisor-head"><h2>Ask about your portfolio or risk category</h2></div>
        <div className="RoboAdvisor-body">
          <p style={{ color: "var(--ink-soft)" }}>
            Answers are grounded in our policy documents.
          </p>

          {turns.map((turn, i) => (
            <div className="chat-turn" key={i}>
              <div className="chat-question">
                <div className="who">You</div>
                {turn.question}
              </div>
              {turn.pending
                ? (
                  <div className="chat-answer typing">
                    <div className="who">RoboAdvice</div>
                    <Loading what="Thinking" />
                  </div>
                )
                : <AnswerCard turn={turn} onPick={ask} disabled={pending} />}
            </div>
          ))}

          <div className="chip-row">
            {QUICK.map((q) => (
              <button key={q} className="chip" disabled={pending} onClick={() => ask(q)}>{q}</button>
            ))}
          </div>

          <form onSubmit={submitAsk} className="chat-input-row">
            <input
              value={question}
              onChange={(ev) => setQuestion(ev.target.value)}
              placeholder="e.g. Why was I assigned this risk category?"
              aria-label="Your question"
            />
            <button className="btn" type="submit" disabled={pending || !question.trim()}>Ask</button>
          </form>
        </div>
      </section>
    </div>
  );
}
