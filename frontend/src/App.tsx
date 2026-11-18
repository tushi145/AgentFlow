import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { lazy, Suspense } from "react";
import { BrowserRouter, Route, Routes } from "react-router";
import { Toaster } from "sonner";

import { ThemeProvider, useTheme } from "@/components/theme-provider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppLayout } from "@/layouts/AppLayout";
import LoginPage from "@/pages/LoginPage";
import { NotFoundPage, StagePlaceholder } from "@/pages/StagePlaceholder";
import { ApiError } from "@/services/api";

// Route-level code splitting: each page is its own chunk.
const OverviewPage = lazy(() => import("@/pages/OverviewPage"));
const NewTaskPage = lazy(() => import("@/pages/NewTaskPage"));
const ActiveExecutionsPage = lazy(() => import("@/pages/ActiveExecutionsPage"));
const HistoryPage = lazy(() => import("@/pages/HistoryPage"));
const TaskExecutionPage = lazy(() => import("@/pages/TaskExecutionPage"));

function PageFallback() {
  return (
    <div className="flex justify-center py-24" aria-busy="true">
      <Loader2 className="size-5 animate-spin text-muted-foreground" />
    </div>
  );
}

const page = (el: React.ReactNode) => <Suspense fallback={<PageFallback />}>{el}</Suspense>;

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false,
        retry: (count, err) => !(err instanceof ApiError && err.status > 0 && err.status < 500) && count < 2,
      },
    },
  });
}

function ThemedToaster() {
  const { resolved } = useTheme();
  return <Toaster theme={resolved} position="bottom-right" richColors closeButton />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<AppLayout />}>
        <Route index element={page(<OverviewPage />)} />
        <Route path="tasks/new" element={page(<NewTaskPage />)} />
        <Route path="tasks/:taskId" element={page(<TaskExecutionPage />)} />
        <Route path="executions" element={page(<ActiveExecutionsPage />)} />
        <Route path="history" element={page(<HistoryPage />)} />
        <Route path="reports" element={<StagePlaceholder title="Reports" stage="3D" description="Generated reports with sources and verification." />} />
        <Route path="reports/:reportId" element={<StagePlaceholder title="Report" stage="3D" description="Report viewer." />} />
        <Route path="evaluation" element={<StagePlaceholder title="Evaluation" stage="3D" description="Quality metrics and benchmarks." />} />
        <Route path="agents" element={<StagePlaceholder title="Agent Configuration" stage="3D" description="Models, instructions and limits per agent." />} />
        <Route path="settings" element={<StagePlaceholder title="Settings" stage="3D" description="Theme, providers and defaults." />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

export default function App({ queryClient = makeQueryClient() }: { queryClient?: QueryClient }) {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider delayDuration={250}>
          <BrowserRouter>
            <AppRoutes />
          </BrowserRouter>
          <ThemedToaster />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
