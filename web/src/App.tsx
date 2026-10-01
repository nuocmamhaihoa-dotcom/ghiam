import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router";
import { Toaster } from "sonner";

import { AuthProvider } from "./components/AuthProvider";
import { Layout } from "./components/Layout";
import { ApiError } from "./lib/api";
import { useAuth } from "./lib/auth-context";
import { JobDetailPage } from "./pages/JobDetailPage";
import { JobsPage } from "./pages/JobsPage";
import { LoginPage } from "./pages/LoginPage";
import { ProxiesPage } from "./pages/ProxiesPage";
import { ResultsPage } from "./pages/ResultsPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 2000,
      retry: (failureCount, error) =>
        !(error instanceof ApiError && error.status >= 400 && error.status < 500) && failureCount < 2,
    },
  },
});

function RequireAuth() {
  const { username } = useAuth();
  const location = useLocation();
  if (!username) {
    return <Navigate to="/login" replace state={{ from: `${location.pathname}${location.search}` }} />;
  }
  return <Layout />;
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route element={<RequireAuth />}>
              <Route index element={<Navigate to="/proxies" replace />} />
              <Route path="proxies" element={<ProxiesPage />} />
              <Route path="jobs" element={<JobsPage />} />
              <Route path="jobs/:jobId" element={<JobDetailPage />} />
              <Route path="results" element={<ResultsPage />} />
              <Route path="*" element={<Navigate to="/proxies" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
        <Toaster richColors closeButton position="top-right" duration={5000} />
      </AuthProvider>
    </QueryClientProvider>
  );
}
