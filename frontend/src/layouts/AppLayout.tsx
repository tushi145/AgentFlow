import { Loader2, TriangleAlert, X } from "lucide-react";
import { useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router";

import { ErrorState } from "@/components/common";
import { Button } from "@/components/ui/button";
import { useCurrentUser } from "@/hooks/use-auth";
import { useSystemStatus } from "@/hooks/use-system";
import { Sidebar } from "@/layouts/Sidebar";
import { TopBar } from "@/layouts/TopBar";

const COLLAPSE_KEY = "agentflow-sidebar-collapsed";

function readCollapsed() {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

/** Shown on every page while live execution is unavailable, so demo mode is never ambiguous. */
function DemoModeNotice() {
  const { data } = useSystemStatus();
  const [dismissed, setDismissed] = useState(() => {
    try { return sessionStorage.getItem("agentflow-demo-notice") === "1"; } catch { return false; }
  });
  if (!data || data.live_mode_available || dismissed) return null;
  return (
    <div className="flex items-start gap-3 border-b border-status-demo/25 bg-status-demo/10 px-4 py-2 text-xs sm:px-6">
      <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-status-demo" aria-hidden />
      <p className="flex-1 text-foreground/90">
        <span className="font-semibold text-status-demo">Live AI execution is not configured.</span>{" "}
        Missing <code className="font-mono">{data.missing_for_live.join(", ")}</code> in <code className="font-mono">.env</code>.
        Tasks can still run in <strong>DEMO mode</strong>, which uses fixed templates and synthetic sources — not AI.
      </p>
      <Button variant="ghost" size="icon" className="size-5" aria-label="Dismiss" onClick={() => {
        setDismissed(true);
        try { sessionStorage.setItem("agentflow-demo-notice", "1"); } catch { /* ignore */ }
      }}>
        <X className="size-3" />
      </Button>
    </div>
  );
}

export function AppLayout() {
  const me = useCurrentUser();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(readCollapsed);

  if (me.isLoading) {
    return (
      <div className="flex h-svh items-center justify-center" aria-busy="true">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (me.data === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  if (me.error) {
    return (
      <div className="mx-auto max-w-lg p-6 pt-24">
        <ErrorState error={me.error} onRetry={() => me.refetch()} />
      </div>
    );
  }

  const toggle = () => {
    setCollapsed((c) => {
      try { localStorage.setItem(COLLAPSE_KEY, c ? "0" : "1"); } catch { /* ignore */ }
      return !c;
    });
  };

  return (
    <div className="flex min-h-svh">
      <Sidebar collapsed={collapsed} onToggle={toggle} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <DemoModeNotice />
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 sm:px-6 lg:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
