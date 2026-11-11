import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useSession } from "../context/SessionContext";

// Role guard. Not signed in -> login. Wrong role -> explicit access-denied panel.
// Clients must also have picked a session date (A.5) before seeing any page.
// NOTE: this is only UX. The real access control is enforced by the backend,
// which scopes every client query to the authenticated principal.
export default function ProtectedRoute({ role, children }) {
  const { user } = useAuth();
  const { sessionDate } = useSession();
  const location = useLocation();
  const from = `${location.pathname}${location.search}`;

  if (!user) return <Navigate to="/login" replace state={{ from }} />;

  if (user.role !== role) {
    return (
      <div className="access-denied">
        <h2>Access denied</h2>
        <p>
          This page is restricted to <strong>{role}</strong> accounts. You are signed in as <strong>{user.role}</strong>.
        </p>
      </div>
    );
  }

  if (role === "client" && !sessionDate) return <Navigate to="/login" replace state={{ from }} />;
  return children;
}
