import { createContext, useContext, useEffect, useState } from "react";
import { api, setApiSessionDate } from "../api";
import { useAuth } from "./AuthContext";

// A.5: the simulated "today". Every price and valuation in the app resolves
// against this date; the backend has no fallback to the real calendar.
const SessionContext = createContext(null);

export function SessionProvider({ children }) {
  const { user } = useAuth();
  const [sessionDate, setSessionDate] = useState(null);
  const [range, setRange] = useState({ from: null, to: null });
  const [priceMeta, setPriceMeta] = useState({ file: null, label: null, instrumentCount: null });

  useEffect(() => {
    setSessionDate(null);
    setRange({ from: null, to: null });
    setPriceMeta({ file: null, label: null, instrumentCount: null });
    setApiSessionDate(null);
  }, [user?.userId]);

  // Throws an ApiError (with the backend's "choose a date between X and Y" message) if invalid.
  async function chooseDate(date) {
    const res = await api.setSessionDate(date);
    setApiSessionDate(res.sessionDate);
    setSessionDate(res.sessionDate);
    setRange({ from: res.availableFrom, to: res.availableTo });
    setPriceMeta({
      file: res.priceFile || "/api/market/prices",
      label: res.priceFileLabel || "Appendix C prices (JSON price service)",
      instrumentCount: res.instrumentCount ?? null,
      format: res.priceFormat || "json",
    });
    return res;
  }

  // The picker needs min/max *before* a date is chosen, which is exactly when
  // no date exists. GET /api/session/date now returns the range alongside a
  // null sessionDate, so Login can seed it without a successful POST.
  function seedRange({ availableFrom, availableTo }) {
    if (availableFrom || availableTo) setRange({ from: availableFrom, to: availableTo });
  }

  return (
    <SessionContext.Provider value={{ sessionDate, range, chooseDate, seedRange, priceMeta }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside SessionProvider");
  return ctx;
}
