/* Reusable page building blocks. */

import { AlertTriangle, Inbox, RefreshCw, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { DASH } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ApiError } from "@/services/api";

export function PageHeader({ title, description, actions, badge }: {
  title: string; description?: ReactNode; actions?: ReactNode; badge?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 pb-6 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0 space-y-1">
        <div className="flex items-center gap-2">
          <h1 className="truncate text-xl font-semibold tracking-tight sm:text-2xl">{title}</h1>
          {badge}
        </div>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/** A metric tile. `value === null` renders an em dash — never a made-up number. */
export function StatCard({ label, value, hint, icon: Icon, loading, className }: {
  label: string; value: ReactNode | null; hint?: ReactNode; icon?: LucideIcon; loading?: boolean;
  className?: string;
}) {
  return (
    <Card className={cn("gap-0 py-4", className)}>
      <CardContent className="space-y-2 px-4">
        <div className="flex items-center justify-between text-xs font-medium text-muted-foreground">
          <span>{label}</span>
          {Icon && <Icon className="size-3.5" aria-hidden />}
        </div>
        {loading ? (
          <Skeleton className="h-7 w-20" />
        ) : (
          <div className="tabular text-2xl font-semibold tracking-tight">{value ?? DASH}</div>
        )}
        {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
      </CardContent>
    </Card>
  );
}

export function EmptyState({ icon: Icon = Inbox, title, description, action, className }: {
  icon?: LucideIcon; title: string; description?: ReactNode; action?: ReactNode; className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 py-14 text-center", className)}>
      <div className="flex size-10 items-center justify-center rounded-full bg-muted">
        <Icon className="size-5 text-muted-foreground" aria-hidden />
      </div>
      <div className="space-y-1">
        <p className="font-medium">{title}</p>
        {description && <p className="max-w-md text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const apiErr = error instanceof ApiError ? error : null;
  return (
    <div role="alert" className={cn("flex flex-col items-start gap-3 rounded-xl border border-status-danger/30 bg-status-danger/5 p-4 sm:flex-row sm:items-center", className)}>
      <AlertTriangle className="size-5 shrink-0 text-status-danger" aria-hidden />
      <div className="min-w-0 flex-1 text-sm">
        <p className="font-medium">{apiErr?.message ?? (error instanceof Error ? error.message : "Something went wrong.")}</p>
        {apiErr && (
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">
            {apiErr.code}{apiErr.requestId ? ` · request ${apiErr.requestId}` : ""}
          </p>
        )}
      </div>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>
          <RefreshCw className="size-3.5" /> Retry
        </Button>
      )}
    </div>
  );
}

export function LoadingRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

export function KeyValue({ label, children, mono }: { label: string; children: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={cn("min-w-0 truncate text-right", mono && "font-mono text-xs")}>{children}</dd>
    </div>
  );
}

export function SectionTitle({ children, actions }: { children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between">
      <h2 className="text-sm font-semibold">{children}</h2>
      {actions}
    </div>
  );
}
