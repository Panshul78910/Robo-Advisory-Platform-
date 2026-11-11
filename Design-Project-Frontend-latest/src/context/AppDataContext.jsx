import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useAuth } from "./AuthContext";

// Holds the multi-turn chat (B.7) and the profiling conversation so they survive
// navigating between pages. Cleared whenever a different user signs in.
const AppDataContext = createContext(null);

const MAX_LEN = 8000; // matches the backend default max_user_message_chars
const MIN_GAP_MS = 800; // light client-side throttle; the real rate limit is server-side

export function AppDataProvider({ children }) {
  const { user } = useAuth();
  const [turns, setTurns] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const [pending, setPending] = useState(false);
  const lastSent = useRef(0);
  const [profiling, setProfiling] = useState({ started: false, turns: [], result: null, last: null });

  useEffect(() => {
    setTurns([]);
    setConversationId(null);
    setPending(false);
    setProfiling({ started: false, turns: [], result: null, last: null });
  }, [user?.userId]);

  const ask = useCallback(
    async (raw) => {
      const message = (raw || "").trim();
      if (!message || pending) return null;
      if (message.length > MAX_LEN) {
        const error = new Error(
          `That message is too long (${message.length.toLocaleString()} characters). Please shorten it to ${MAX_LEN.toLocaleString()} characters or fewer and try again. Nothing was sent.`
        );
        const turn = {
          question: `Message not sent (${message.length.toLocaleString()} characters).`,
          error,
        };
        setTurns((t) => [...t, turn]);
        return turn;
      }
      if (Date.now() - lastSent.current < MIN_GAP_MS) return null;
      lastSent.current = Date.now();
      setPending(true);
      // Show the question straight away; the answer fills in when it arrives.
      const id = lastSent.current;
      setTurns((t) => [...t, { id, question: message, pending: true }]);
      const settle = (done) => setTurns((t) => t.map((x) => (x.id === id ? done : x)));
      let turn;
      try {
        const answer = await api.chat(message, conversationId);
        if (answer?.conversationId && answer.conversationId !== conversationId) {
          setConversationId(answer.conversationId);
        }
        turn = { id, question: message, answer };
        settle(turn);
      } catch (error) {
        turn = { id, question: message, error };
        settle(turn);
      } finally {
        setPending(false);
      }
      return turn;
    },
    [pending, conversationId]
  );

  return (
    <AppDataContext.Provider value={{ turns, ask, pending, profiling, setProfiling, setConversationId }}>
      {children}
    </AppDataContext.Provider>
  );
}

export function useAppData() {
  const ctx = useContext(AppDataContext);
  if (!ctx) throw new Error("useAppData must be used inside AppDataProvider");
  return ctx;
}
