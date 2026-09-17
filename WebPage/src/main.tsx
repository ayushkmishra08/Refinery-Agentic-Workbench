/**
 * Entry point and routing.
 *
 * There is one gate: signed in, or not. Every route below `<RequireSession>` is part of the
 * authenticated workstation, and the shell renders nothing until a session exists — which is the
 * browser mirroring the workbench's own rule that identity comes before the question.
 *
 * Lazy-loading is deliberate: the chat is the page people land on, and it should not wait for the
 * approvals table to parse.
 */
import { StrictMode, Suspense, lazy, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import AppShell from "@/layouts/AppShell";
import SignIn from "@/pages/SignIn";
import { AuthProvider, useAuth } from "@/store/auth";
import { Spinner, ToastProvider } from "@/ui";

import { PageErrorBoundary } from "@/components/ErrorBoundary";
import "./index.css";

const Chat = lazy(() => import("@/pages/Chat"));
const Overview = lazy(() => import("@/pages/Overview"));
const Documents = lazy(() => import("@/pages/Documents"));
const Access = lazy(() => import("@/pages/Access"));
const Security = lazy(() => import("@/pages/Security"));
const Account = lazy(() => import("@/pages/Account"));
const Knowledge = lazy(() => import("@/pages/Knowledge"));

function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex h-full min-h-64 items-center justify-center">
      <span className="flex items-center gap-2 text-sm text-muted-foreground">
        <Spinner className="size-4 text-primary" /> {label}…
      </span>
    </div>
  );
}

/** The single gate. No session, no workstation — and no flash of protected chrome while deciding. */
function RequireSession({ children }: { children: ReactNode }) {
  const { session, ready } = useAuth();
  if (!ready) return <div className="app-canvas min-h-screen"><Loading /></div>;
  if (!session) return <SignIn />;
  return <>{children}</>;
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/*"
          element={
            <RequireSession>
              <AppShellRoutes />
            </RequireSession>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}

function AppShellRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/chat" replace />} />
        <Route path="chat" element={<PageErrorBoundary name="Ask"><Suspense fallback={<Loading label="Opening the workspace" />}><Chat /></Suspense></PageErrorBoundary>} />
        <Route path="overview" element={<PageErrorBoundary name="Overview"><Suspense fallback={<Loading />}><Overview /></Suspense></PageErrorBoundary>} />
        <Route path="documents" element={<PageErrorBoundary name="Documents"><Suspense fallback={<Loading />}><Documents /></Suspense></PageErrorBoundary>} />
        <Route path="knowledge" element={<PageErrorBoundary name="Knowledge"><Suspense fallback={<Loading />}><Knowledge /></Suspense></PageErrorBoundary>} />
        <Route path="access" element={<PageErrorBoundary name="Access"><Suspense fallback={<Loading />}><Access /></Suspense></PageErrorBoundary>} />
        <Route path="security" element={<PageErrorBoundary name="Security"><Suspense fallback={<Loading />}><Security /></Suspense></PageErrorBoundary>} />
        <Route path="account" element={<PageErrorBoundary name="Account"><Suspense fallback={<Loading />}><Account /></Suspense></PageErrorBoundary>} />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Route>
    </Routes>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthProvider>
      <ToastProvider>
        <App />
      </ToastProvider>
    </AuthProvider>
  </StrictMode>,
);
