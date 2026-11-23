"use client";

import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from "recharts";
import { useIntelCoverage, useIntelTimeline } from "@/lib/api/hooks";
import type { TimelineBucket } from "@/lib/api/schemas";
import { TYPE_LABEL, fmtDate, fmtNumber } from "@/lib/format";
import { useUrlState } from "@/lib/url-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartSkeleton, EmptyState, ErrorState, LimitationNote, ListSkeleton } from "@/components/common/states";
import { Field, Select, inputCls } from "@/components/explore/controls";
import { CoverageNotes, IntelHeader, TYPE_COLOR, axis, tooltipStyle } from "./common";
import { TOPIC_OPTIONS } from "./topics";

const GRAN = ["day", "week", "month"] as const;

function CoverageTable() {
  const { data, isPending, isError, error, refetch } = useIntelCoverage();
  return (
    <Card className="overflow-x-auto">
      <CardHeader><div><CardTitle>Available historical coverage</CardTitle><CardDescription>Measured from stored publication and collection dates</CardDescription></div></CardHeader>
      <CardContent>
        {isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : isPending ? <ListSkeleton rows={5} /> : (
          <table className="w-full min-w-[820px] text-sm" data-testid="coverage-table">
            <thead><tr className="border-b border-border text-left text-xs text-muted-foreground">
              <th scope="col" className="py-2 pr-3 font-medium">Category</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Records</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">With publication date</th>
              <th scope="col" className="px-3 py-2 font-medium">Publication range</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Days with records / missing</th>
              <th scope="col" className="px-3 py-2 font-medium">Collected</th>
              <th scope="col" className="px-3 py-2 font-medium">Supports</th>
            </tr></thead>
            <tbody>
              {data.data.categories.map((c) => (
                <tr key={c.type} className="border-b border-border/60 last:border-0" data-type={c.type}>
                  <td className="py-2 pr-3 font-medium"><span className="mr-1.5 inline-block size-2 rounded-full" style={{ background: TYPE_COLOR[c.type] }} />{c.label}</td>
                  <td className="tabular px-3 py-2 text-right">{fmtNumber(c.records)}</td>
                  <td className="tabular px-3 py-2 text-right">{fmtNumber(c.with_publication_date)}</td>
                  <td className="px-3 py-2 text-xs">{c.publication_first ? `${fmtDate(c.publication_first)} – ${fmtDate(c.publication_last)} (${c.span_days} d)` : <span className="text-muted-foreground">no dates</span>}</td>
                  <td className="tabular px-3 py-2 text-right text-xs">{c.with_publication_date ? <>{c.days_with_records} / <span className={c.missing_days ? "text-warning" : ""}>{c.missing_days}</span></> : "—"}</td>
                  <td className="px-3 py-2 text-xs">{c.collection_days.map((d) => fmtDate(d)).join(", ")}</td>
                  <td className="px-3 py-2"><div className="flex flex-wrap gap-1">{GRAN.map((g) => (
                    <Badge key={g} tone={c.granularity[g]?.supported ? "success" : "neutral"} title={c.granularity[g]?.reason ?? "supported"}>{c.granularity[g]?.supported ? "✓" : "✕"} {g}</Badge>
                  ))}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

function fmtBucket(b: TimelineBucket, g: string) {
  if (g === "month") return fmtDate(b.start, { month: "short", year: "numeric" });
  return fmtDate(b.start, { month: "short", day: "numeric" });
}

export function HistoricalTrends() {
  const { get, set } = useUrlState();
  const type = get("type") || "papers";
  const granularity = (GRAN as readonly string[]).includes(get("granularity")) ? get("granularity") : "week";
  const topic = get("topic");
  const { data, isPending, isError, error, refetch } = useIntelTimeline({ type, granularity, topic: topic || undefined, date_from: get("date_from") || undefined, date_to: get("date_to") || undefined });
  const t = data?.data;
  const series = (t?.buckets ?? []).map((b) => ({
    label: fmtBucket(b, granularity), start: b.start, records: b.records, empty: !b.has_records, partial: b.partial,
    topic: b.topic_records ?? null, share: b.topic_share === null || b.topic_share === undefined ? null : +(b.topic_share * 100).toFixed(1),
  }));
  const color = TYPE_COLOR[type] ?? "var(--chart-2)";
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      <IntelHeader title="Historical trends" description="Daily, weekly and monthly activity by publication date, only where the stored dates support it. Empty periods are shown as gaps; no values are interpolated." />
      <div className="grid gap-3 rounded-lg border border-border bg-surface p-3 sm:grid-cols-2 lg:grid-cols-5" data-testid="history-controls">
        <Field label="Category" htmlFor="h-type">
          <Select id="h-type" value={type} onChange={(v) => set({ type: v })}
            options={[{ value: "papers", label: "Research papers" }, { value: "jobs", label: "Jobs" }, { value: "news", label: "News" }, { value: "startups", label: "Startups (no dates)" }, { value: "products", label: "Products (no dates)" }, { value: "all", label: "All dated categories" }]} />
        </Field>
        <Field label="Granularity" htmlFor="h-gran">
          <Select id="h-gran" value={granularity} onChange={(v) => set({ granularity: v })} options={GRAN.map((g) => ({ value: g, label: g[0]!.toUpperCase() + g.slice(1) }))} />
        </Field>
        <Field label="Topic share (optional)" htmlFor="h-topic"><Select id="h-topic" value={topic} onChange={(v) => set({ topic: v })} options={TOPIC_OPTIONS} placeholder="No topic" /></Field>
        <Field label="Published from" htmlFor="h-from"><input id="h-from" type="date" className={inputCls} value={get("date_from")} onChange={(e) => set({ date_from: e.target.value })} /></Field>
        <Field label="Published to" htmlFor="h-to"><input id="h-to" type="date" className={inputCls} value={get("date_to")} onChange={(e) => set({ date_to: e.target.value })} /></Field>
      </div>
      <CoverageNotes />
      <Card>
        <CardHeader>
          <div>
            <CardTitle>{TYPE_LABEL[type] ?? "All dated categories"} by {granularity}</CardTitle>
            <CardDescription>
              {t && t.first ? `${fmtDate(t.first)} – ${fmtDate(t.last)} · ${fmtNumber(t.records ?? 0)} dated records` : "Publication dates"}
              {t && t.undated_excluded > 0 && ` · ${fmtNumber(t.undated_excluded)} undated records not shown`}
            </CardDescription>
          </div>
          {t && <Badge tone={t.supported ? "success" : "warning"} data-testid="granularity-support">{t.supported ? "supported" : "not supported"}</Badge>}
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : isPending ? <ChartSkeleton height={300} /> : !t || t.buckets.length === 0 ? (
            <EmptyState title="Nothing to chart">{t?.reason ?? "No records with a publication date in this selection."}</EmptyState>
          ) : (
            <>
              {!t.supported && <LimitationNote>{t.reason}. The buckets below are shown for transparency but are too few to read as a trend.</LimitationNote>}
              <div className="h-[320px]" data-testid="history-chart">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={series} margin={{ left: -6, right: 8 }}>
                    <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                    <XAxis dataKey="label" {...axis} minTickGap={20} />
                    <YAxis yAxisId="n" {...axis} allowDecimals={false} />
                    {topic && <YAxis yAxisId="s" orientation="right" {...axis} unit="%" />}
                    <RTooltip {...tooltipStyle} formatter={(v, n, item) => {
                      const p = item.payload as { empty: boolean; partial: boolean };
                      if (n === "Topic share") return [v === null ? "no records" : `${v}%`, n];
                      return [`${v}${p.empty ? " (gap: no records)" : ""}${p.partial ? " (partial period)" : ""}`, n];
                    }} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Bar yAxisId="n" dataKey="records" name="Records" fill={color} radius={[2, 2, 0, 0]} />
                    {topic && <Bar yAxisId="n" dataKey="topic" name="Matching topic" fill="var(--chart-2)" radius={[2, 2, 0, 0]} />}
                    {topic && <Line yAxisId="s" dataKey="share" name="Topic share" stroke="var(--chart-4)" dot={false} connectNulls={false} strokeWidth={2} />}
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <div>
                <div className="mb-1 text-xs font-medium text-muted-foreground">Gaps (periods with no records)</div>
                {t.gaps.length ? (
                  <ul className="flex flex-wrap gap-1.5" data-testid="gaps">
                    {t.gaps.slice(0, 30).map((g) => <li key={g.from}><Badge tone="warning">{fmtDate(g.from)} – {fmtDate(g.to)} · {g.buckets} {granularity}{g.buckets === 1 ? "" : "s"}</Badge></li>)}
                    {t.gaps.length > 30 && <li className="text-xs text-muted-foreground">+{t.gaps.length - 30} more</li>}
                  </ul>
                ) : <p className="text-xs text-muted-foreground" data-testid="no-gaps">No empty {granularity}s between the first and last publication date.</p>}
              </div>
              <p className="text-[11px] text-muted-foreground">Counts depend on how far back each source was crawled (e.g. papers come from Hugging Face daily paper lists), so rising bars can reflect collection depth rather than activity. Compare shares, not counts.</p>
            </>
          )}
        </CardContent>
      </Card>
      <CoverageTable />
    </div>
  );
}
