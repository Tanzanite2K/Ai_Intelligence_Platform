"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { RotateCcw } from "lucide-react";
import type { ReactNode } from "react";
import type { Query } from "@/lib/api/client";
import { useIntelCoverage } from "@/lib/api/hooks";
import { TYPE_LABEL, TYPE_ORDER } from "@/lib/format";
import { useUrlState } from "@/lib/url-state";
import { cn } from "@/lib/utils";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { LimitationNote } from "@/components/common/states";
import { Field, inputCls } from "@/components/explore/controls";

export const TYPE_COLOR: Record<string, string> = {
  startups: "var(--chart-1)",
  products: "var(--chart-2)",
  papers: "var(--chart-3)",
  jobs: "var(--chart-4)",
  news: "var(--chart-5)",
};
export const axis = { stroke: "var(--muted-foreground)", fontSize: 11, tickLine: false, axisLine: false } as const;
export const tooltipStyle = {
  contentStyle: { background: "var(--surface-2)", border: "1px solid var(--border-strong)", borderRadius: 8, fontSize: 12, color: "var(--foreground)" },
  labelStyle: { color: "var(--muted-foreground)", marginBottom: 4 },
  itemStyle: { color: "var(--foreground)", padding: 0 },
  cursor: { fill: "var(--accent-soft)" },
};

export const fmtShare = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`);

const TABS = [
  { href: "/intelligence", label: "Brief" },
  { href: "/intelligence/trends", label: "Trend Radar" },
  { href: "/intelligence/signals", label: "Market Signals" },
  { href: "/intelligence/map", label: "Innovation Map" },
  { href: "/intelligence/history", label: "Historical Trends" },
  { href: "/intelligence/changes", label: "Change Feed" },
  { href: "/intelligence/comparisons", label: "Historical Changes" },
];

export function IntelHeader({ title, description }: { title: string; description: ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="flex flex-col gap-3">
      <nav aria-label="Frontier Intelligence" className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-1">
        {TABS.map((t) => {
          const active = t.href === "/intelligence" ? pathname === t.href : pathname.startsWith(t.href);
          return (
            <Link key={t.href} href={t.href} aria-current={active ? "page" : undefined}
              className={cn("shrink-0 rounded-full border px-3 py-1 text-xs whitespace-nowrap",
                active ? "border-accent/50 bg-accent-soft text-accent" : "border-border text-muted-foreground hover:text-foreground")}>
              {t.label}
            </Link>
          );
        })}
      </nav>
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        <div className="max-w-3xl text-sm text-muted-foreground">{description}</div>
      </div>
    </div>
  );
}

/** Data-coverage caveats from /intelligence/coverage, shown on every module. */
export function CoverageNotes({ extra }: { extra?: string[] }) {
  const { data } = useIntelCoverage();
  const notes = [...(data?.data.notes ?? []), ...(extra ?? [])];
  if (!notes.length) return null;
  return (
    <LimitationNote>
      <ul className="flex flex-col gap-0.5" data-testid="coverage-notes">
        {notes.map((n) => <li key={n}>{n}</li>)}
      </ul>
    </LimitationNote>
  );
}

/** Category chips + publication-date range bound to the URL (`types`, `date_from`, `date_to`). */
export function IntelFilters({ children, showDates = true }: { children?: ReactNode; showDates?: boolean }) {
  const { get, set, reset, all } = useUrlState();
  const selected = get("types") ? get("types").split(",") : [];
  const toggle = (t: string) => {
    const next = selected.includes(t) ? selected.filter((x) => x !== t) : [...selected, t];
    set({ types: TYPE_ORDER.filter((x) => next.includes(x)).join(",") });
  };
  const active = Object.keys(all).some((k) => k !== "page");
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-3" data-testid="intel-filters">
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Data categories">
        <span className="mr-1 text-xs font-medium text-muted-foreground">Categories</span>
        {TYPE_ORDER.map((t) => {
          const on = selected.length === 0 || selected.includes(t);
          return (
            <button key={t} type="button" aria-pressed={selected.includes(t)} onClick={() => toggle(t)} data-type={t}
              className={cn("inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs",
                selected.includes(t) ? "border-accent/50 bg-accent-soft text-accent" : on ? "border-border text-foreground" : "border-border text-muted-foreground/60")}>
              <span className="size-2 rounded-full" style={{ background: TYPE_COLOR[t] }} />{TYPE_LABEL[t]}
            </button>
          );
        })}
        {selected.length === 0 && <span className="text-[11px] text-muted-foreground">all selected</span>}
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {showDates && (
          <>
            <Field label="Published from" htmlFor="intel-from" hint="Undated records are excluded when a date is set">
              <input id="intel-from" type="date" className={inputCls} value={get("date_from")} onChange={(e) => set({ date_from: e.target.value })} />
            </Field>
            <Field label="Published to" htmlFor="intel-to">
              <input id="intel-to" type="date" className={inputCls} value={get("date_to")} onChange={(e) => set({ date_to: e.target.value })} />
            </Field>
          </>
        )}
        {children}
      </div>
      {active && (
        <div><Button variant="ghost" size="sm" onClick={reset}><RotateCcw /> Reset filters</Button></div>
      )}
    </div>
  );
}

export function useIntelParams(extra: string[] = []): Query {
  const { get } = useUrlState();
  const q: Query = { types: get("types") || undefined, date_from: get("date_from") || undefined, date_to: get("date_to") || undefined };
  for (const k of extra) q[k] = get(k) || undefined;
  return q;
}

const CONF_TONE: Record<string, BadgeProps["tone"]> = { high: "success", medium: "info", low: "warning" };

export function ConfidenceBadge({ level, explanation }: { level: string; explanation: string }) {
  return (
    <Tooltip content={explanation}>
      <Badge tone={CONF_TONE[level] ?? "neutral"} tabIndex={0} data-testid="confidence">{level} confidence</Badge>
    </Tooltip>
  );
}

const STATUS_TONE: Record<string, BadgeProps["tone"]> = { rising: "success", declining: "danger", stable: "neutral", too_few_records: "neutral" };
const STATUS_LABEL: Record<string, string> = { rising: "rising share", declining: "falling share", stable: "stable", too_few_records: "too few records" };

export function TrendBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"} data-status={status}>{STATUS_LABEL[status] ?? status}</Badge>;
}

export function recordLink(type: string, id: number) {
  return `/${type}/${id}`;
}
