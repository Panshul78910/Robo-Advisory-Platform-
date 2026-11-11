import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, onUnauthorized, setApiSessionDate, setToken } from "../api";

const IDLE_MS = 15 * 60 * 1000; // auto sign-out after 15 minutes of no input

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const logout = useCallback(() => {
    setToken(null);
    setApiSessionDate(null);
    setUser(null);
  }, []);

  // Any 401 from the backend signs the user out.
  useEffect(() => {
    onUnauthorized(() => {
      logout();
      setError("Your session has expired. Please sign in again.");
    });
  }, [logout]);

  // Idle timeout.
  useEffect(() => {
    if (!user) return undefined;
    let timer;
    const reset = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        logout();
        setError("You were signed out after 15 minutes of inactivity.");
      }, IDLE_MS);
    };
    const events = ["mousedown", "keydown", "touchstart"];
    events.forEach((e) => window.addEventListener(e, reset));
    reset();
    return () => {
      clearTimeout(timer);
      events.forEach((e) => window.removeEventListener(e, reset));
    };
  }, [user, logout]);

  async function applySession(res, email) {
    setToken(res.accessToken);
    const me = await api.me();
    setUser({
      userId: me.userId,
      email: email.trim().toLowerCase(),
      displayName: res.displayName,
      // The backend calls the back-office role "admin"; the UI calls it "officer".
      role: res.role === "admin" ? "officer" : "client",
      clientId: res.clientId,
      clientCode: me.clientCode,
    });
  }

  async function login(email, password) {
    setBusy(true);
    setError("");
    try {
      const res = await api.login(email.trim(), password);
      await applySession(res, email);
      return true;
    } catch (e) {
      setToken(null);
      setError(e.message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function register(email, password, fullName) {
    setBusy(true);
    setError("");
    try {
      const res = await api.register(email.trim(), password, fullName.trim());
      await applySession(res, email);
      return true;
    } catch (e) {
      setToken(null);
      setError(e.message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthContext.Provider value={{ user, login, register, logout, error, busy, setError }}>{children}</AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
