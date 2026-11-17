/* Display formatters. Missing values render as an em dash — never as invented numbers. */

export const DASH = "—";

/** Parse an API timestamp. The API sends UTC with an offset; a value without an offset is
 *  treated as UTC too (never as browser-local time). */
export function parseApiDate(iso: string): Date {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso.trim());
  return new Date(hasZone ? iso : `${iso}Z`);
}

export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return DASH;
  return new Intl.NumberFormat("en-US").format(n);
}

export function formatCompact(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return DASH;
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function formatCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return DASH;
  if (usd === 0) return "$0.00";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

export function formatPercent(ratio: number | null | undefined, digits = 0): string {
  if (ratio === null || ratio === undefined) return DASH;
  return `${(ratio * 100).toFixed(digits)}%`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return DASH;
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m}m ${s.toString().padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${(m % 60).toString().padStart(2, "0")}m`;
}

export function durationBetween(start: string | null, end: string | null, now = Date.now()): number | null {
  if (!start) return null;
  const a = parseApiDate(start).getTime();
  const b = end ? parseApiDate(end).getTime() : now;
  return Math.max(0, (b - a) / 1000);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  return new Intl.DateTimeFormat("en-US", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(parseApiDate(iso));
}

export function formatRelative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return DASH;
  const diff = (now - parseApiDate(iso).getTime()) / 1000;
  if (diff < 45) return "just now";
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  if (diff < 3600) return rtf.format(-Math.round(diff / 60), "minute");
  if (diff < 86400) return rtf.format(-Math.round(diff / 3600), "hour");
  return rtf.format(-Math.round(diff / 86400), "day");
}
