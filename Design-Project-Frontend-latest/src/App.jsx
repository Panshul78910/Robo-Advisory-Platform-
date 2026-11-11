import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { SessionProvider } from "./context/SessionContext";
import { AppDataProvider } from "./context/AppDataContext";
import ProtectedRoute from "./components/ProtectedRoute";
import AppLayout from "./components/AppLayout";
import Login from "./pages/Login";
import ClientDashboard from "./pages/ClientDashboard";
import ProfilePage from "./pages/ProfilePage";
import ProfilingPage from "./pages/ProfilingPage";
import PortfolioPage from "./pages/PortfolioPage";
import OfficerDashboard from "./pages/OfficerDashboard";

function RootRedirect() {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to={user.role === "officer" ? "/officer" : "/dashboard"} replace />;
}

const client = (page) => (
  <ProtectedRoute role="client">
    <AppLayout>{page}</AppLayout>
  </ProtectedRoute>
);

export default function App() {
  return (
    <AuthProvider>
      <SessionProvider>
        <AppDataProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/dashboard" element={client(<ClientDashboard />)} />
              <Route path="/profile" element={client(<ProfilePage />)} />
              <Route path="/profiling" element={client(<ProfilingPage />)} />
              <Route path="/portfolio" element={client(<PortfolioPage />)} />
              <Route
                path="/officer"
                element={
                  <ProtectedRoute role="officer">
                    <AppLayout>
                      <OfficerDashboard />
                    </AppLayout>
                  </ProtectedRoute>
                }
              />
              <Route path="/" element={<RootRedirect />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </BrowserRouter>
        </AppDataProvider>
      </SessionProvider>
    </AuthProvider>
  );
}
