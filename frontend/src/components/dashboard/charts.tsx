"use client";

import {
  Bar, BarChart, CartesianGrid, Cell, Legend, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis,
} from "recharts";
import { useState } from "react";
import { useActivity, useFreshness, useOverview, useSourceContribution } from "@/lib/api/hooks";
import { TYPE_LABEL, TYPE_ORDER, fmtCompact, fmtDate, fmtNumber } from "@/lib/format";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartSkeleton, EmptyState, ErrorState, LimitationNote } from "@/components/common/states";
import { cn } from "@/lib/utils";

const COLORS: Record<string, string> = {
  startups: "var(--chart-1)",
  products: "var(--chart-2)",
  papers: "var(--chart-3)",
  jobs: "var(--chart-4)",
  news: "var(--chart-5)",
};
const axis = { stroke: "var(--muted-foreground)", fontSize: 11, tickLine: false, axisLine: false } as const;
const tooltipStyle = {
  contentStyle: { background: "var(--surface-2)", border: "1px solid var(--border-strong)", borderRadius: 8, fontSize: 12, color: "var(--foreground)" },
  labelStyle: { color: "var(--muted-foreground)", marginBottom: 4 },
  itemStyle: { color: "var(--foreground)", padding: 0 },
  cursor: { fill: "var(--accent-soft)" },
};

function Frame({ title, description, children, action, className }: { title: string; description?: string; children: React.ReactNode; action?: React.ReactNode; className?: string }) {
  return (
    <Card className={cn("flex flex-col", className)}>
      <CardHeader>
        <div>
          <CardTitle>{title}</CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </div>
        {action}
      </CardHeader>
      <CardContent className="flex-1">{children}</CardContent>
    </Card>
  );
}

/** 1. Records by category — from /dashboard/overview. */
export function RecordsByCategory() {
  const { data, isPending, isError, error, refetch } = useOverview();
  const SHORT: Record<string, string> = { startups: "Startups", products: "Products", papers: "Papers", jobs: "Jobs", news: "News" };
  const rows = TYPE_ORDER.map((t) => ({ type: t, label: SHORT[t] ?? t, records: data?.records[t] ?? 0 }));
  return (
    <Frame title="Records by category" description="Stored records per type">
      {isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : isPending ? <ChartSkeleton /> : (
        <div className="h-[220px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
              <XAxis dataKey="label" {...axis} interval={0} />
              <YAxis {...axis} tickFormatter={fmtCompact} />
              <RTooltip {...tooltipStyle} formatter={(v) => [fmtNumber(Number(v)), "Records"]} />
              <Bar dataKey="records" radius={[4, 4, 0, 0]} maxBarSize={56}>
                {rows.map((r) => <Cell key={r.type} fill={COLORS[r.type]} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Frame>
  );
}

type Mode = "collected" | "published";

/** 2. Data ingestion over time — real daily aggregates; the API reports when history is too short. */
export function IngestionOverTime() {
  const [mode, setMode] = useState<Mode>("collected");
  const { data, isPending, isError, error, refetch } = useActivity(90);
  const series = mode === "collected" ? data?.data.collected_per_day ?? [] : data?.data.published_per_day ?? [];
  const present = TYPE_ORDER.filter((t) => series.some((d) => (d[t] ?? 0) > 0));
  const toggle = (
    <div className="flex rounded-md border border-border p-0.5 text-xs" role="tablist" aria-label="Date basis">
      {(["collected", "published"] as const).map((m) => (
        <button
          key={m}
          role="tab"
          aria-selected={mode === m}
          onClick={() => setMode(m)}
          className={cn("rounded px-2 py-1", mode === m ? "bg-surface-2 text-foreground" : "text-muted-foreground hover:text-foreground")}
        >
          {m === "collected" ? "Collected" : "Published"}
        </button>
      ))}
    </div>
  );
  return (
    <Frame
      title="Data ingestion over time"
      description={mode === "collected" ? "Records stored per day by the platform (last 90 days)" : "Source publication dates per day (papers, jobs, news; last 90 days)"}
      action={toggle}
      className="lg:col-span-2"
    >
      {isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : isPending ? <ChartSkeleton /> : (
        <div className="flex flex-col gap-3">
          {mode === "collected" && data?.note && <LimitationNote>{data.note}</LimitationNote>}
          {series.length === 0 ? (
            <EmptyState title="No dated records in this window" />
          ) : (
            <div className="h-[220px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={series} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                  <XAxis dataKey="date" {...axis} tickFormatter={(d: string) => fmtDate(d, { day: "2-digit", month: "short" })} minTickGap={24} />
                  <YAxis {...axis} tickFormatter={fmtCompact} />
                  <RTooltip {...tooltipStyle} labelFormatter={(d) => fmtDate(String(d))} formatter={(v, n) => [fmtNumber(Number(v)), TYPE_LABEL[String(n)] ?? n]} />
                  <Legend formatter={(v: string) => <span className="text-xs text-muted-foreground">{TYPE_LABEL[v] ?? v}</span>} iconSize={8} />
                  {present.map((t) => (
                    <Bar key={t} dataKey={t} stackId="a" fill={COLORS[t]} maxBarSize={mode === "collected" ? 64 : 18} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      )}
    </Frame>
  );
}

/** 3. Source contribution — records per source (stacked by type). */
export function SourceContributionChart() {
  const { data, isPending, isError, error, refetch } = useSourceContribution();
  type Row = { source: string; total: number; counts: Record<string, number> };
  const bySource = new Map<string, Row>();
  for (const r of data ?? []) {
    const row = bySource.get(r.source) ?? { source: r.source, total: 0, counts: {} };
    row.counts[r.type] = (row.counts[r.type] ?? 0) + r.records;
    row.total += r.records;
    bySource.set(r.source, row);
  }
  const rows = [...bySource.values()].sort((a, b) => b.total - a.total).map((r) => ({ source: r.source, total: r.total, ...r.counts }));
  const present = TYPE_ORDER.filter((t) => rows.some((r) => ((r as Record<string, unknown>)[t] as number | undefined ?? 0) > 0));
  return (
    <Frame title="Source contribution" description="Stored records per source">
      {isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : isPending ? <ChartSkeleton height={260} /> : rows.length === 0 ? (
        <EmptyState title="No records stored yet" />
      ) : (
        <div style={{ height: Math.max(180, rows.length * 30 + 40) }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 16, left: 8, bottom: 0 }}>
              <CartesianGrid horizontal={false} stroke="var(--chart-grid)" />
              <XAxis type="number" {...axis} tickFormatter={fmtCompact} />
              <YAxis type="category" dataKey="source" {...axis} width={150}
                tickFormatter={(s: string) => `${s} (${fmtCompact(bySource.get(s)?.total ?? 0)})`} />
              <RTooltip {...tooltipStyle} formatter={(v, n) => [fmtNumber(Number(v)), TYPE_LABEL[String(n)] ?? n]} />
              {present.map((t) => (
                <Bar key={t} dataKey={t} stackId="s" fill={COLORS[t]} maxBarSize={18} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Frame>
  );
}

const BUCKETS = [
  { key: "b24h", label: "< 24 h", color: "var(--success)" },
  { key: "b7d", label: "1–7 d", color: "var(--chart-1)" },
  { key: "b30d", label: "7–30 d", color: "var(--chart-2)" },
  { key: "b365d", label: "30–365 d", color: "var(--chart-3)" },
  { key: "older", label: "> 1 year", color: "var(--muted-foreground)" },
  { key: "undated", label: "No publication date", color: "var(--border-strong)" },
] as const;

/** 5. Data freshness — age of source publication dates per type (disjoint buckets). */
export function FreshnessChart() {
  const { data, isPending, isError, error, refetch } = useFreshness();
  const rows = (data?.data ?? [])
    .map((f) => ({
      type: TYPE_LABEL[f.type] ?? f.type,
      order: TYPE_ORDER.indexOf(f.type as (typeof TYPE_ORDER)[number]),
      b24h: f.within_24h,
      b7d: f.within_7d - f.within_24h,
      b30d: f.within_30d - f.within_7d,
      b365d: f.within_365d - f.within_30d,
      older: f.older_than_365d,
      undated: f.undated,
    }))
    .sort((a, b) => a.order - b.order);
  return (
    <Frame title="Data freshness" description="Age of each record's source publication date">
      {isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : isPending ? <ChartSkeleton height={200} /> : (
        <div className="flex flex-col gap-2">
          <div className="h-[200px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows} layout="vertical" stackOffset="expand" margin={{ top: 0, right: 12, left: 8, bottom: 0 }}>
                <XAxis type="number" {...axis} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} />
                <YAxis type="category" dataKey="type" {...axis} width={104} />
                <RTooltip {...tooltipStyle} formatter={(v, n) => [fmtNumber(Number(v)), BUCKETS.find((b) => b.key === n)?.label ?? n]} />
                {BUCKETS.map((b) => (
                  <Bar key={b.key} dataKey={b.key} stackId="f" fill={b.color} maxBarSize={16} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
            {BUCKETS.map((b) => (
              <span key={b.key} className="inline-flex items-center gap-1">
                <span className="size-2 rounded-sm" style={{ background: b.color }} /> {b.label}
              </span>
            ))}
          </div>
          {data && <p className="text-[11px] text-muted-foreground">Startups and products carry no publication date in their sources, so they appear as undated.</p>}
        </div>
      )}
    </Frame>
  );
}
