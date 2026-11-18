import { ArrowLeft, Download, ExternalLink, FileText, ListTree, ShieldCheck } from "lucide-react";
import { useMemo } from "react";
import { Link, useParams } from "react-router";

import { EmptyState, ErrorState, LoadingRows, PageHeader, SectionTitle, StatCard } from "@/components/common";
import { SourcesPanel, VerificationPanel } from "@/components/execution/research-panels";
import { MarkdownReport } from "@/components/report/markdown-report";
import { ModeBadge, TONE_CLASSES } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useReport } from "@/hooks/use-tasks";
import { verdictMeta } from "@/lib/execution-meta";
import { formatDateTime, formatPercent } from "@/lib/format";
import { tableOfContents } from "@/lib/report-markdown";
import { cn } from "@/lib/utils";
import { ApiError, urls } from "@/services/api";

function BackLink() {
  return (
    <Link to="/reports" className="mb-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
      <ArrowLeft className="size-3" /> All reports
    </Link>
  );
}

function Toc({ markdown }: { markdown: string }) {
  const toc = useMemo(() => tableOfContents(markdown), [markdown]);
  if (!toc.length) return null;
  const links = (
    <ol className="space-y-1 text-sm">
      {toc.map((h) => (
        <li key={h.id} className={cn(h.level === 3 && "pl-3")}>
          <a href={`#${h.id}`} className="block truncate text-muted-foreground hover:text-foreground"
             onClick={(e) => { e.preventDefault(); document.getElementById(h.id)?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>
            {h.text}
          </a>
        </li>
      ))}
      <li className="border-t pt-1"><a href="#report-sources" className="block text-muted-foreground hover:text-foreground">Sources</a></li>
      <li><a href="#report-verification" className="block text-muted-foreground hover:text-foreground">Verification</a></li>
    </ol>
  );
  return (
    <>
      <details className="rounded-lg border px-3 py-2 xl:hidden">
        <summary className="flex cursor-pointer items-center gap-1.5 text-sm font-medium"><ListTree className="size-4" /> Contents</summary>
        <div className="mt-2">{links}</div>
      </details>
      <nav aria-label="Report contents" className="sticky top-20 hidden max-h-[calc(100vh-6rem)] overflow-y-auto xl:block">
        <div className="mb-2 text-xs font-medium text-muted-foreground uppercase">Contents</div>
        {links}
      </nav>
    </>
  );
}

export default function ReportViewerPage() {
  const { reportId } = useParams();
  const id = Number(reportId);
  const valid = Number.isInteger(id) && id > 0;
  const report = useReport(valid ? id : null);
  const r = report.data;

  if (!valid || (report.error instanceof ApiError && report.error.status === 404)) {
    return (
      <div className="space-y-4">
        <BackLink />
        <EmptyState icon={FileText} title="Report not found" description="It may have been deleted with its execution, or the link is wrong."
                    action={<Button asChild variant="outline"><Link to="/reports">All reports</Link></Button>} />
      </div>
    );
  }
  if (report.error) return <div className="space-y-4"><BackLink /><ErrorState error={report.error} onRetry={() => report.refetch()} /></div>;
  if (!r) return <div className="space-y-4" aria-busy="true"><BackLink /><LoadingRows rows={2} /><LoadingRows rows={8} /></div>;

  const demo = r.mode === "demo";
  const counts = r.verifications.reduce<Record<string, number>>((acc, v) => ({ ...acc, [v.verdict]: (acc[v.verdict] ?? 0) + 1 }), {});

  return (
    <>
      <BackLink />
      <PageHeader
        title={r.title}
        badge={<ModeBadge mode={demo ? "demo" : "live"} />}
        description={<span className="text-xs">Generated {formatDateTime(r.created_at)}</span>}
        actions={
          <>
            <Button asChild variant="outline" size="sm"><Link to={`/tasks/${r.task_id}`}><ExternalLink className="size-3.5" /> Execution</Link></Button>
            <Button asChild size="sm"><a href={urls.reportMarkdown(r.id)} download><Download className="size-3.5" /> Markdown</a></Button>
            <Button asChild variant="outline" size="sm"><a href={urls.reportPdf(r.id)} download><Download className="size-3.5" /> PDF</a></Button>
          </>
        }
      />
      <div className="space-y-6">
        {demo && (
          <div role="note" className={cn("rounded-lg border px-4 py-3 text-sm", TONE_CLASSES.warning)}>
            Demo report: produced from fixed templates with synthetic <span className="font-mono">demo://</span> sources. It is not research and contains no real findings.
          </div>
        )}
        {r.prompt && (
          <Card className="gap-0 py-3">
            <CardContent className="px-4">
              <div className="text-xs font-medium text-muted-foreground">Objective</div>
              <p className="mt-0.5 text-sm whitespace-pre-wrap break-words">{r.prompt}</p>
            </CardContent>
          </Card>
        )}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <StatCard label="Cited sources" value={r.source_count} hint={`${r.sources.length} retrieved`} />
          <StatCard label="Avg. confidence" value={r.avg_confidence === null ? null : formatPercent(r.avg_confidence)}
                    hint={r.avg_confidence === null ? "No verified claims" : demo ? "Template value (demo)" : "Across verified claims"} />
          <StatCard label="Claims verified" value={r.verifications.length} icon={ShieldCheck}
                    hint={r.verifications.length ? Object.entries(counts).map(([k, n]) => `${n} ${verdictMeta(k).label.toLowerCase()}`).join(" · ") : "No verification results"} />
          <StatCard label="Invalid citations removed" value={r.citation_violations}
                    hint={r.citation_violations ? "Citations to unknown sources were stripped" : "All citations resolved to sources"} />
        </div>

        <div className="grid gap-6 xl:grid-cols-[220px_minmax(0,1fr)]">
          <Toc markdown={r.markdown} />
          <div className="min-w-0 space-y-8">
            <Card className="gap-0 py-5">
              <CardContent className="px-4 sm:px-6">
                <MarkdownReport markdown={r.markdown} sources={r.sources} />
              </CardContent>
            </Card>
            <section id="report-sources" aria-labelledby="sources-title" className="scroll-mt-20">
              <SectionTitle><span id="sources-title">Sources ({r.sources.length})</span></SectionTitle>
              <SourcesPanel sources={r.sources} verifications={r.verifications} isLoading={false} error={null} onRetry={() => report.refetch()} />
            </section>
            <section id="report-verification" aria-labelledby="verification-title" className="scroll-mt-20">
              <SectionTitle><span id="verification-title">Verification (latest iteration)</span></SectionTitle>
              <VerificationPanel verifications={r.verifications} sources={r.sources} isLoading={false} error={null}
                                 onRetry={() => report.refetch()} demo={demo} />
            </section>
          </div>
        </div>
      </div>
    </>
  );
}
