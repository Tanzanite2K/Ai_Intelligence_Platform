"use client";

import Link from "next/link";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from "recharts";
import { useIntelChanges, type Section } from "@/lib/api/history";
import { fmtDate, fmtNumber } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartSkeleton, EmptyState, ErrorState, LimitationNote } from "@/components/common/states";
import { IntelHeader, axis, fmtShare, tooltipStyle } from "@/components/intelligence/common";

type StatusInfo = { label: string; tone: "success" | "warning" | "neutral" };
const NO_DATA: StatusInfo = { label: "No data", tone: "neutral" };
const STATUS: Record<string, StatusInfo> = {
  measured: { label: "Measured", tone: "success" }, insufficient_history: { label: "Insufficient history", tone: "warning" },
  insufficient_data: { label: "Insufficient data", tone: "warning" }, no_data: { label: "No data", tone: "neutral" }, no_history: { label: "No history", tone: "neutral" },
};

function SectionCard({ s, children }: { s: Section; children?: React.ReactNode }) {
  const st = STATUS[s.status] ?? NO_DATA;
  return (
    <Card data-testid={`section-${s.id}`}>
      <CardHeader className="pb-2">
        <div className="flex flex-col gap-1">
          <CardTitle className="flex flex-wrap items-center gap-2">{s.title}<Badge tone={st.tone}>{st.label}</Badge>{s.label === "descriptive" && <Badge tone="neutral">descriptive, not a trend label</Badge>}</CardTitle>
          {s.basis && <CardDescription>{s.basis}</CardDescription>}
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {s.explanation && <LimitationNote>{s.explanation}</LimitationNote>}
        {children}
      </CardContent>
    </Card>
  );
}

function Research({ s }: { s: Section }) {
  if (!s.statements.length) return <SectionCard s={s}><EmptyState title="No topic met the minimum">Topics need at least 10 papers in one window.</EmptyState></SectionCard>;
  return (
    <SectionCard s={s}>
      {s.period && <p className="text-xs text-muted-foreground">Recent {fmtDate(s.period.recent[0])}–{fmtDate(s.period.recent[1])} ({fmtNumber(s.period.papers.recent)} papers) vs prior {fmtDate(s.period.prior[0])}–{fmtDate(s.period.prior[1])} ({fmtNumber(s.period.papers.prior)} papers)</p>}
      <ul className="flex flex-col divide-y divide-border/60">
        {s.statements.map((st, i) => {
          const pp = st.change_pp ?? 0;
          const Icon = pp > 0.5 ? ArrowUpRight : pp < -0.5 ? ArrowDownRight : Minus;
          return (
            <li key={i} className="flex flex-col gap-1 py-2">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Icon className={pp > 0.5 ? "size-4 text-success" : pp < -0.5 ? "size-4 text-danger" : "size-4 text-muted-foreground"} />
                <span className="font-medium">{st.statement.split(":")[0]}</span>
                <span className="tabular text-xs text-muted-foreground">{fmtShare(st.prior_share)} → {fmtShare(st.recent_share)} ({pp > 0 ? "+" : ""}{pp} pp)</span>
              </div>
              <p className="text-xs text-muted-foreground">{st.statement}</p>
              {st.evidence && st.evidence.length > 0 && (
                <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs">{st.evidence.map((e) => <Link key={e.id} href={`/${e.type}/${e.id}`} className="truncate text-accent hover:underline">{e.name ?? `#${e.id}`}</Link>)}</div>
              )}
            </li>
          );
        })}
      </ul>
    </SectionCard>
  );
}

function Startups({ s }: { s: Section }) {
  const data = (s.series ?? []).map((x) => ({ batch: String(x.batch), startups: Number(x.startups) }));
  return (
    <SectionCard s={s}>
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 8, bottom: 30, left: 0 }}>
            <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="batch" {...axis} angle={-45} textAnchor="end" interval={0} height={50} fontSize={9} /><YAxis {...axis} width={36} />
            <RTooltip {...tooltipStyle} /><Bar dataKey="startups" fill="var(--chart-1)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <ul className="flex flex-col gap-1 text-xs">{s.statements.map((st, i) => <li key={i}>{st.statement}</li>)}</ul>
    </SectionCard>
  );
}

function Hiring({ s }: { s: Section }) {
  return (
    <SectionCard s={s}>
      <div className="grid gap-4 md:grid-cols-2">
        <div><div className="mb-1 text-xs font-medium">Postings by publication day</div>
          <ul className="flex flex-col gap-0.5 text-xs">{(s.per_day ?? []).map((d) => <li key={d.date} className="flex justify-between"><span>{fmtDate(d.date)}</span><span className="tabular">{d.jobs}</span></li>)}</ul></div>
        <div><div className="mb-1 text-xs font-medium">Current postings by role family ({fmtNumber(s.jobs ?? 0)})</div>
          <ul className="flex flex-col gap-0.5 text-xs">{(s.roles ?? []).map((r) => <li key={r.role_family} className="flex justify-between"><span>{r.role_family}</span><span className="tabular">{r.jobs}</span></li>)}</ul></div>
      </div>
      {s.statements.map((st, i) => <p key={i} className="text-sm">{st.statement}</p>)}
    </SectionCard>
  );
}

function Metrics({ s }: { s: Section }) {
  return (
    <SectionCard s={s}>
      {s.snapshot && (
        <div className="text-xs">
          <p className="mb-1 text-muted-foreground">Baseline snapshot: {fmtNumber(s.snapshot.papers_with_stars)} papers with stars, median {fmtNumber(s.snapshot.median)}.</p>
          <ol className="flex flex-col gap-0.5">{s.snapshot.top.map((t) => <li key={t.id} className="flex justify-between gap-2"><Link href={`/papers/${t.id}`} className="truncate hover:text-accent">{t.name}</Link><span className="tabular shrink-0">{fmtNumber(t.stars)} ★</span></li>)}</ol>
        </div>
      )}
      {s.statements.map((st, i) => <p key={i} className="text-sm">{st.statement}</p>)}
    </SectionCard>
  );
}

/** Records first seen by a live collection batch, per day and type (presence events "appeared"). The baseline is never counted. */
function NewRecords({ s }: { s: Section }) {
  const rows = (s.series ?? []).map((x) => ({ date: String(x.date), type: String(x.type), n: Number(x.new) }));
  const byDate = new Map<string, { total: number; types: { type: string; n: number }[] }>();
  for (const r of rows) {
    const d = byDate.get(r.date) ?? { total: 0, types: [] };
    d.total += r.n;
    d.types.push({ type: r.type, n: r.n });
    byDate.set(r.date, d);
  }
  if (!byDate.size) return <SectionCard s={s} />;
  return (
    <SectionCard s={s}>
      <ul className="flex flex-col gap-2 text-sm">
        {[...byDate.entries()].reverse().map(([date, d]) => (
          <li key={date}>
            <div className="flex justify-between font-medium"><span>{fmtDate(date)}</span><span className="tabular">{fmtNumber(d.total)} new</span></div>
            <div className="text-xs text-muted-foreground">{d.types.map((t) => `${t.type} ${fmtNumber(t.n)}`).join(" · ")}</div>
          </li>
        ))}
      </ul>
      <Link href="/intelligence/changes" className="text-xs text-accent hover:underline">Open the Change Feed for every record</Link>
    </SectionCard>
  );
}

export function IntelChanges() {
  const { data, isPending, isError, error, refetch } = useIntelChanges();
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      <IntelHeader title="Historical Changes" description="Evidence-backed comparisons over time. Each statement is computed from stored records and links to examples; nothing is projected." />
      {isError ? <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card> : isPending ? <div className="grid gap-4 lg:grid-cols-2"><Card className="p-4"><ChartSkeleton /></Card><Card className="p-4"><ChartSkeleton /></Card></div> : (
        <>
          <LimitationNote>{data.meta.rule} Sections below are descriptive comparisons of publication dates and current listings, not emerging-trend labels.</LimitationNote>
          <div className="grid gap-4 xl:grid-cols-2">
            {data.data.sections.map((s) =>
              s.id === "research_topics" ? <Research key={s.id} s={s} /> : s.id === "startup_discovery" ? <Startups key={s.id} s={s} /> :
              s.id === "hiring" ? <Hiring key={s.id} s={s} /> : s.id === "metric_movement" ? <Metrics key={s.id} s={s} /> :
              s.id === "new_records" ? <NewRecords key={s.id} s={s} /> : <SectionCard key={s.id} s={s} />)}
          </div>
        </>
      )}
    </div>
  );
}
