/* Executions per day, stacked by outcome. Colours are dedicated chart tokens validated for
   colour-vision deficiency (see index.css); order completed → other → failed keeps red and green
   apart; identity is also given by legend labels, tooltip text and a data-table view. */

import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Button } from "@/components/ui/button";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import type { OverviewAnalytics } from "@/types/api";

const SERIES = [
  { key: "completed", label: "Completed", color: "var(--chart-completed)" },
  { key: "other", label: "Other (active, cancelled, rejected)", color: "var(--chart-other)" },
  { key: "failed", label: "Failed / interrupted", color: "var(--chart-failed)" },
] as const;

type Row = OverviewAnalytics["series"][number];

function shortDate(iso: string) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function ChartTooltip({ active, payload, label }: { active?: boolean; payload?: { payload: Row }[]; label?: string }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  const total = row.completed + row.other + row.failed;
  return (
    <div className="min-w-44 rounded-lg border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="mb-1.5 font-medium">{label ? shortDate(label) : ""} · {total} task{total === 1 ? "" : "s"}</div>
      {SERIES.map((s) => (
        <div key={s.key} className="flex items-center justify-between gap-4 py-0.5">
          <span className="flex items-center gap-1.5 text-muted-foreground">
            <span className="size-2 rounded-sm" style={{ background: s.color }} />
            {s.label.split(" (")[0]}
          </span>
          <span className="tabular font-medium text-foreground">{row[s.key]}</span>
        </div>
      ))}
    </div>
  );
}

export function ExecutionsChart({ series }: { series: Row[] }) {
  const [showTable, setShowTable] = useState(false);
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground" aria-label="Legend">
        {SERIES.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm" style={{ background: s.color }} aria-hidden />
            {s.label}
          </span>
        ))}
        <Button variant="ghost" size="sm" className="ml-auto h-6 px-2 text-xs" onClick={() => setShowTable((v) => !v)}>
          {showTable ? "Show chart" : "View as table"}
        </Button>
      </div>
      {showTable ? (
        <div className="max-h-64 overflow-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow><TableHead>Date</TableHead>{SERIES.map((s) => <TableHead key={s.key} className="text-right">{s.label.split(" (")[0]}</TableHead>)}</TableRow>
            </TableHeader>
            <TableBody>
              {series.map((r) => (
                <TableRow key={r.date}>
                  <TableCell>{shortDate(r.date)}</TableCell>
                  {SERIES.map((s) => <TableCell key={s.key} className="tabular text-right">{r[s.key]}</TableCell>)}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <div className="h-56" role="img" aria-label="Executions per day by outcome">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={series} margin={{ top: 4, right: 4, bottom: 0, left: -20 }} barCategoryGap="22%">
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <XAxis dataKey="date" tickFormatter={shortDate} tickLine={false} axisLine={false}
                     tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} interval="preserveStartEnd" minTickGap={16} />
              <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={44}
                     tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: "var(--muted)", opacity: 0.6 }} />
              {SERIES.map((s, i) => (
                <Bar key={s.key} dataKey={s.key} stackId="a" fill={s.color} stroke="var(--card)" strokeWidth={2}
                     radius={i === SERIES.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]} isAnimationActive={false} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
