/* Renders a generated report (Markdown). Raw HTML is NOT rendered (react-markdown default): reports
   contain text derived from untrusted web pages. Only http(s) links become external links. */

import { ExternalLink } from "lucide-react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { isWebUrl, remarkCitations, remarkHeadingIds, scrollToSource } from "@/lib/report-markdown";
import type { ResearchSource } from "@/types/api";

export function MarkdownReport({ markdown, sources }: { markdown: string; sources: ResearchSource[] }) {
  const byRef = new Map(sources.map((s) => [s.ref, s]));

  // `id` comes from remarkHeadingIds (assigned at parse time, matching tableOfContents()).
  const heading = (Tag: "h1" | "h2" | "h3" | "h4", cls: string): Components["h1"] =>
    function H({ children, id }) {
      return <Tag id={id} className={cls}>{children}</Tag>;
    };

  const components: Components = {
    h1: heading("h1", "mb-4 text-2xl font-semibold tracking-tight scroll-mt-20"),
    h2: heading("h2", "mt-8 mb-3 border-b pb-1.5 text-lg font-semibold scroll-mt-20"),
    h3: heading("h3", "mt-6 mb-2 text-base font-semibold scroll-mt-20"),
    h4: heading("h4", "mt-4 mb-2 text-sm font-semibold scroll-mt-20"),
    p: ({ children }) => <p className="my-3 leading-7">{children}</p>,
    ul: ({ children }) => <ul className="my-3 list-disc space-y-1 pl-6">{children}</ul>,
    ol: ({ children }) => <ol className="my-3 list-decimal space-y-1 pl-6">{children}</ol>,
    li: ({ children }) => <li className="leading-7">{children}</li>,
    blockquote: ({ children }) => <blockquote className="my-4 border-l-4 border-status-demo/50 bg-status-demo/5 px-4 py-1 text-sm">{children}</blockquote>,
    code: ({ children }) => <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">{children}</code>,
    pre: ({ children }) => <pre className="my-4 overflow-x-auto rounded-lg bg-muted p-3 text-xs">{children}</pre>,
    hr: () => <hr className="my-6" />,
    table: ({ children }) => (
      <div className="my-4 overflow-x-auto rounded-lg border">
        <table className="w-full border-collapse text-sm">{children}</table>
      </div>
    ),
    thead: ({ children }) => <thead className="bg-muted/50">{children}</thead>,
    th: ({ children }) => <th className="border-b px-3 py-2 text-left font-medium whitespace-nowrap">{children}</th>,
    td: ({ children }) => <td className="border-b px-3 py-2 align-top">{children}</td>,
    a: ({ href, children }) => {
      if (href?.startsWith("#source-")) {
        const ref = href.slice("#source-".length);
        const src = byRef.get(ref);
        const link = (
          <a href={href} data-citation={ref} className="font-medium text-primary underline-offset-2 hover:underline"
             onClick={(e) => { e.preventDefault(); scrollToSource(ref); }}>{children}</a>
        );
        if (!src) return link;
        return (
          <Tooltip>
            <TooltipTrigger asChild>{link}</TooltipTrigger>
            <TooltipContent className="max-w-72">{src.title} · {src.domain}</TooltipContent>
          </Tooltip>
        );
      }
      if (href?.startsWith("#")) return <a href={href} className="text-primary hover:underline">{children}</a>;
      if (isWebUrl(href)) {
        return (
          <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-baseline gap-0.5 break-all text-primary underline-offset-2 hover:underline">
            {children}<ExternalLink className="size-3 shrink-0 self-center" aria-hidden />
          </a>
        );
      }
      // demo:// and any other scheme: shown as text, never as a link.
      return <span className="break-all font-mono text-[0.85em] text-muted-foreground">{children}</span>;
    },
    img: ({ alt }) => <span className="text-muted-foreground">[image: {alt}]</span>, // never load remote images
  };

  return (
    <article className="min-w-0 text-sm break-words" data-testid="report-markdown">
      <Markdown remarkPlugins={[remarkGfm, remarkHeadingIds, remarkCitations]} components={components}>{markdown}</Markdown>
    </article>
  );
}
