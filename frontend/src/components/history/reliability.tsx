"use client";

import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from "recharts";
import { useReliability } from "@/lib/api/history";
import { fmtNumber } from "@/lib/format";
import { useUrlState } from "@/lib/url-state";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartSkeleton, EmptyState, ErrorState, LimitationNote } from "@/components/common/states";
import { axis, tooltipStyle } from "@/components/intelligence/common";

export function SourceReliability() {
  const { get, set } = useUrlState();
  const days = Number(get("days") || 30);
  const { data, isPending, isError, error, refetch } = useReliability({ days });
  const daily = (() => {
    if (!data) return [];
    const by: Record<string, { date: string; ok: number; partial: number; failed: number; inaccessible: number }> = {};
    for (const s of data.data) for (const d of s.days) {
      const e = (by[d.date] ??= { date: d.date, ok: 0, partial: 0, failed: 0, inaccessible: 0 });
      e.ok += d.ok; e.partial += d.partial; e.failed += d.failed; e.inaccessible += d.inaccessible;
    }
    return Object.values(by).sort((a, b) => a.date.localeCompare(b.date));
  })();
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      <div><h1 className="text-lg font-semibold tracking-tight">Source Reliability</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">How each source behaved across recorded collection runs, by day. Built from the run log; nothing is estimated.</p></div>
      <div className="flex gap-1.5">{[7, 30, 90].map((d) => <button key={d} onClick={() => set({ days: d === 30 ? null : d })} className={cn("rounded-full border px-2.5 py-0.5 text-xs", days === d ? "border-accent/50 bg-accent-soft text-accent" : "border-border text-muted-foreground")}>{d} days</button>)}</div>
      {isError ? <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card> : isPending ? <Card className="p-4"><ChartSkeleton /></Card> : data.data.length === 0 ? (
        <Card><EmptyState title="No runs in this period">No collection runs were recorded in the last {days} days.</EmptyState></Card>
      ) : (
        <>
          {data.note && <LimitationNote>{data.note}</LimitationNote>}
          <Card>
            <CardHeader className="pb-1"><div><CardTitle>Run outcomes per day</CardTitle><CardDescription>All sources · {data.meta.days_with_runs.length} day(s) with runs</CardDescription></div></CardHeader>
            <CardContent className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={daily} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="date" {...axis} /><YAxis {...axis} width={32} allowDecimals={false} />
                  <RTooltip {...tooltipStyle} /><Legend wrapperStyle={{ fontSize: 11 }} />
                  <Bar dataKey="ok" name="OK" stackId="a" fill="var(--success)" isAnimationActive={false} />
                  <Bar dataKey="partial" name="Partial" stackId="a" fill="var(--info)" isAnimationActive={false} />
                  <Bar dataKey="failed" name="Failed" stackId="a" fill="var(--danger)" isAnimationActive={false} />
                  <Bar dataKey="inaccessible" name="Inaccessible" stackId="a" fill="var(--warning)" isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2"><div><CardTitle>By source</CardTitle><CardDescription>{data.definition}</CardDescription></div></CardHeader>
            <CardContent className="overflow-x-auto">
              <table className="w-full text-left text-xs" data-testid="reliability-table">
                <thead className="text-muted-foreground"><tr>{["Source", "Success rate", "Runs", "OK", "Partial", "Failed", "Inaccessible", "Stored", "Days"].map((h) => <th key={h} className="py-1.5 pr-3 font-medium">{h}</th>)}</tr></thead>
                <tbody className="divide-y divide-border/60">
                  {data.data.map((s) => {
                    const rate = s.success_rate ?? 0;
                    return (
                      <tr key={s.source}>
                        <td className="py-1.5 pr-3 font-medium">{s.source}</td>
                        <td className="py-1.5 pr-3"><div className="flex items-center gap-2"><div className="h-1.5 w-20 overflow-hidden rounded-full bg-surface-2"><div className={cn("h-full", rate >= 0.8 ? "bg-success" : rate >= 0.5 ? "bg-warning" : "bg-danger")} style={{ width: `${Math.round(rate * 100)}%` }} /></div><span className="tabular">{Math.round(rate * 100)}%</span></div></td>
                        {[s.runs, s.ok, s.partial, s.failed, s.inaccessible, s.days.reduce((a, d) => a + d.stored, 0)].map((n, i) => <td key={i} className="tabular py-1.5 pr-3">{fmtNumber(n)}</td>)}
                        <td className="py-1.5 pr-3">{s.days.map((d) => <Badge key={d.date} tone={d.failed + d.inaccessible === 0 ? "success" : d.ok + d.partial === 0 ? "danger" : "warning"} className="mr-1">{d.date.slice(5)}</Badge>)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
