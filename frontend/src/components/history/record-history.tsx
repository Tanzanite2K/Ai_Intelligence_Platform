"use client";

import { GitCompareArrows, History, LineChart as LineIcon, ListOrdered, Radar } from "lucide-react";
import { useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from "recharts";
import { useCompare, useMetrics, useRecordHistory, type Metrics } from "@/lib/api/history";
import { fmtDate, fmtDateTime, fmtNumber } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartSkeleton, EmptyState, ErrorState, LimitationNote, ListSkeleton } from "@/components/common/states";
import { axis, tooltipStyle } from "@/components/intelligence/common";

export const ORIGIN_LABEL: Record<string, string> = { baseline_backfill: "Baseline", observed_change: "Observed", live: "Live" };
const METRIC_LABEL: Record<string, string> = { github_stars: "GitHub stars", employeeCount: "Team size" };

function OriginBadge({ origin }: { origin: string }) {
  return <Badge tone={origin === "baseline_backfill" ? "neutral" : "accent"}>{ORIGIN_LABEL[origin] ?? origin}</Badge>;
}

function short(v: unknown): string {
  if (v === null || v === undefined) return "—";
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > 160 ? `${s.slice(0, 160)}…` : s;
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-md border border-border bg-surface-2/40 px-3 py-2">
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <span className="tabular text-sm font-medium">{value}</span>
      {hint && <span className="text-[11px] text-muted-foreground">{hint}</span>}
    </div>
  );
}

function MetricChart({ data }: { data: Metrics }) {
  const series = data.data.series;
  if (!series.length) return <EmptyState title="No numeric metrics">This record type has no tracked metric.</EmptyState>;
  return (
    <div className="flex flex-col gap-4">
      {series.map((s) => {
        const pts = s.points.map((p) => ({ at: p.at ? fmtDate(p.at) : "", value: p.value, kind: p.kind }));
        return (
          <div key={s.metric} className="flex flex-col gap-1" data-testid={`metric-${s.metric}`}>
            <div className="flex items-baseline justify-between text-xs">
              <span className="font-medium">{METRIC_LABEL[s.metric] ?? s.metric}</span>
              <span className="text-muted-foreground">{s.points.length} sample{s.points.length === 1 ? "" : "s"} · latest {fmtNumber(s.points.at(-1)?.value ?? null)}</span>
            </div>
            <div className="h-44">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={pts} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="at" {...axis} />
                  <YAxis {...axis} width={56} tickFormatter={(v: number) => fmtNumber(v)} />
                  <RTooltip {...tooltipStyle} formatter={(v) => [fmtNumber(Number(v)), METRIC_LABEL[s.metric] ?? s.metric]} />
                  <Line type="monotone" dataKey="value" stroke="var(--chart-3)" strokeWidth={2} dot={{ r: 4 }} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        );
      })}
      {data.note && <LimitationNote>{data.note}</LimitationNote>}
    </div>
  );
}

function CompareCard({ id, versions }: { id: number; versions: number[] }) {
  const newest = versions.at(-1) ?? 1;
  const [a, setA] = useState<number>(Math.max(1, newest - 1));
  const [b, setB] = useState<number>(newest);
  const { data, isPending, isError, error, refetch } = useCompare(id, { a, b });
  const sel = "h-8 rounded-md border border-border bg-surface px-2 text-sm";
  return (
    <Card data-testid="version-compare">
      <CardHeader className="pb-2">
        <div>
          <CardTitle className="flex items-center gap-1.5"><GitCompareArrows className="size-4" /> Compare versions</CardTitle>
          <CardDescription>Field-level differences between two stored versions of this record</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <label className="flex items-center gap-1.5">From
            <select aria-label="Older version" className={sel} value={a} onChange={(e) => setA(Number(e.target.value))}>
              {versions.map((v) => <option key={v} value={v}>v{v}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-1.5">to
            <select aria-label="Newer version" className={sel} value={b} onChange={(e) => setB(Number(e.target.value))}>
              {versions.map((v) => <option key={v} value={v}>v{v}</option>)}
            </select>
          </label>
        </div>
        {isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : isPending ? <ListSkeleton rows={3} /> : !data.data ? (
          <EmptyState title="No versions">{data.note}</EmptyState>
        ) : data.data.changes.length === 0 ? (
          <EmptyState title={a === b ? "Same version selected" : "No field differences"}>{data.note ?? "These versions store identical content."}</EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-muted-foreground"><tr><th className="py-1.5 pr-3 font-medium">Field</th><th className="py-1.5 pr-3 font-medium">v{data.data.from.version}</th><th className="py-1.5 font-medium">v{data.data.to.version}</th></tr></thead>
              <tbody className="divide-y divide-border/60">
                {data.data.changes.map((c) => (
                  <tr key={c.field} className="align-top">
                    <td className="py-1.5 pr-3 font-mono text-[11px]">{c.field} <Badge tone={c.change === "changed" ? "info" : c.change === "added" ? "success" : "danger"}>{c.change}</Badge></td>
                    <td className="py-1.5 pr-3 text-muted-foreground line-through decoration-danger/50">{short(c.before)}</td>
                    <td className="py-1.5">{short(c.after)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function RecordHistoryPanel({ id }: { id: number }) {
  const { data, isPending, isError, error, refetch } = useRecordHistory(id);
  const metrics = useMetrics(id);
  if (isError) return <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card>;
  if (isPending) return <Card className="p-4"><ListSkeleton rows={6} /></Card>;
  const h = data.data;
  return (
    <div className="flex flex-col gap-4" data-testid="record-history">
      {data.note && <LimitationNote>{data.note}</LimitationNote>}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="First seen" value={h.first_seen.at ? fmtDate(h.first_seen.at) : "—"} hint={h.first_seen.bound === "on_or_before" ? "on or before (baseline)" : "exact"} />
        <Stat label="Last seen" value={h.last_seen ? fmtDate(h.last_seen) : "—"} />
        <Stat label="Versions" value={fmtNumber(h.counts.versions)} hint="new only when content changes" />
        <Stat label="Observations" value={fmtNumber(h.counts.observations)} hint={`${h.counts.live_observations} live · ${h.counts.sources} source${h.counts.sources === 1 ? "" : "s"}`} />
      </div>
      <div className="flex flex-wrap gap-2">
        {h.in_baseline && <Badge tone="neutral">In baseline snapshot</Badge>}
        {h.newly_discovered && <Badge tone="success">Newly discovered</Badge>}
        {h.presence.map((p, i) => <Badge key={i} tone={p.event === "disappeared" ? "danger" : "info"}>{p.event} {p.observed_at ? fmtDate(p.observed_at) : ""}</Badge>)}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader className="pb-2"><div><CardTitle className="flex items-center gap-1.5"><History className="size-4" /> Versions</CardTitle><CardDescription>Each row is a distinct stored content; the open version is current</CardDescription></div></CardHeader>
          <CardContent>
            <ol className="flex flex-col gap-2" data-testid="version-list">
              {[...h.versions].reverse().map((v) => (
                <li key={v.version} className={cn("rounded-md border px-3 py-2 text-xs", v.current ? "border-accent/40 bg-accent-soft/40" : "border-border")}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">v{v.version}</span><OriginBadge origin={v.origin} />{v.current && <Badge tone="accent">current</Badge>}
                    <span className="text-muted-foreground">{v.valid_from ? fmtDateTime(v.valid_from) : ""}{v.valid_to ? ` → ${fmtDateTime(v.valid_to)}` : ""}</span>
                  </div>
                  {v.changed_fields.length > 0 && <div className="mt-1 text-muted-foreground">Changed: {v.changed_fields.join(", ")}</div>}
                  <div className="mt-1 font-mono text-[10px] text-muted-foreground">hash {v.content_hash.slice(0, 16)}…</div>
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>
        <CompareCard id={id} versions={h.versions.map((v) => v.version)} />
      </div>

      <Card>
        <CardHeader className="pb-2"><div><CardTitle className="flex items-center gap-1.5"><LineIcon className="size-4" /> Historical metrics</CardTitle><CardDescription>Recorded values only; nothing is interpolated</CardDescription></div></CardHeader>
        <CardContent>{metrics.isError ? <ErrorState error={metrics.error} onRetry={() => void metrics.refetch()} /> : metrics.isPending ? <ChartSkeleton height={180} /> : <MetricChart data={metrics.data} />}</CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2"><div><CardTitle className="flex items-center gap-1.5"><ListOrdered className="size-4" /> Observations</CardTitle><CardDescription>Every time a source showed this record to FrontierAtlas</CardDescription></div></CardHeader>
        <CardContent>
          {h.observations.length === 0 ? <EmptyState title="Never observed" /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs" data-testid="observation-table">
                <thead className="text-muted-foreground"><tr><th className="py-1.5 pr-3 font-medium">Observed</th><th className="py-1.5 pr-3 font-medium">Source</th><th className="py-1.5 pr-3 font-medium">Batch</th><th className="py-1.5 font-medium">Origin</th></tr></thead>
                <tbody className="divide-y divide-border/60">
                  {h.observations.map((o, i) => (
                    <tr key={i}><td className="tabular py-1.5 pr-3">{o.observed_at ? fmtDateTime(o.observed_at) : "—"}</td><td className="py-1.5 pr-3">{o.source}</td>
                      <td className="py-1.5 pr-3 text-muted-foreground">{o.trigger ?? "—"} · <span className="font-mono text-[10px]">{o.batch_id.slice(0, 8)}</span></td><td className="py-1.5"><OriginBadge origin={o.origin} /></td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="mt-2 flex items-center gap-1 text-[11px] text-muted-foreground"><Radar className="size-3" /> Disappearance is recorded only after a complete scan of a full-catalogue source.</p>
        </CardContent>
      </Card>
    </div>
  );
}
