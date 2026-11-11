import Sidebar from "./Sidebar";

export default function AppLayout({ children }) {
  return (
    <div className="app-shell">
      <Sidebar />
      {children}
    </div>
  );
}
