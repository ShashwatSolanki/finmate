import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../lib/auth";

export function ProtectedRoute() {
  const { token } = useAuth();
  const location = useLocation();
  if (!token) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}

export function GuestRoute() {
  const { token } = useAuth();
  if (token) return <Navigate to="/chat" replace />;
  return <Outlet />;
}
