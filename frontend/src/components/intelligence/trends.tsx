"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from "recharts";
import { useIntelTopic, useIntelTopics } from "@/lib/api/hooks";
import type { EmergingType, IntelTopics } from "@/lib/api/schemas";
import { entityHref } from "@/lib/entities";
import { TYPE_LABEL, TYPE_ORDER, fmtDate, fmtNumber } from "@/lib/format";
import { useUrlState } from "@/lib/url-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartSkeleton, EmptyState, ErrorState, LimitationNote, ListSkeleton } from "@/components/common/states";
import { Pagination } from "@/components/explore/controls";
import { CoverageNotes, IntelFilters, IntelHeader, TYPE_COLOR, TrendBadge, axis, fmtShare, recordLink, tooltipStyle, useIntelParams } from "./common";

function emergingFor(data: IntelTopics["data"], topicId: string): { type: string; status: string } | null {
  for (const [type, e] of Object.entries(data.emerging.by_type)) {
    const t = e.assessable ? e.topics?.find((x) => x.id === topicId) : undefined;
    if (t && (t.status === "rising" || t.status === "declining")) return { type, status: t.status };
  }
  return null;
}

function EmergingPanel({ data }: { data: IntelTopics["data"] }) {
  const entries = Object.entries(data.emerging.by_type) as [string, EmergingType][];
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Emerging topics</CardTitle>
          <CardDescription>Only where publication dates cover both comparison windows</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        <p className="text-xs text-muted-foreground" data-testid="emerging-method">{data.emerging.method}</p>
        <ul className="flex flex-col gap-2">
          {entries.map(([type, e]) => (
            <li key={type} className="rounded-md border border-border p-2.5" data-testid="emerging-type" data-type={type} data-assessable={e.assessable}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{TYPE_LABEL[type]}</span>
                {e.assessable ? <Badge tone="success">assessable</Badge> : <Badge>not assessable</Badge>}
                {e.windows && (
                  <span className="text-xs text-muted-foreground">
                    recent {fmtDate(e.windows.recent.from)}–{fmtDate(e.windows.recent.to)} ({fmtNumber(e.windows.recent.records)}) vs {fmtDate(e.windows.baseline.from)}–{fmtDate(e.windows.baseline.to)} ({fmtNumber(e.windows.baseline.records)})
                  </span>
                )}
              </div>
              {!e.assessable && <p className="mt-1 text-xs text-muted-foreground">{e.reason}</p>}
              {e.assessable && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {(e.topics ?? []).filter((t) => t.status === "rising" || t.status === "declining").map((t) => (
                    <Link key={t.id} href={`/intelligence/trends/${t.id}?types=${type}`} className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs hover:border-border-strong">
                      <TrendBadge status={t.status} />{t.label}
                      <span className="tabular text-muted-foreground">{fmtShare(t.baseline_share)} → {fmtShare(t.recent_share)} · z {t.z.toFixed(1)}</span>
                    </Link>
                  ))}
                  {!(e.topics ?? []).some((t) => t.status === "rising" || t.status === "declining") && <span className="text-xs text-muted-foreground">No topic passed the threshold.</span>}
                </div>
              )}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

export function TrendRadar() {
  const params = useIntelParams();
  const { data, isPending, isError, error, refetch } = useIntelTopics(params);
  const types = params.types ? String(params.types).split(",") : [...TYPE_ORDER];
  const rows = data?.data.topics.filter((t) => t.records > 0) ?? [];
  const chart = rows.slice(0, 12).map((t) => ({ label: t.label, ...Object.fromEntries(types.map((k) => [k, t.by_type[k] ?? 0])) }));
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      <IntelHeader title="Trend radar" description="How often each AI topic appears in stored records, by data category. Topics are assigned by transparent keyword rules; the matched term is kept for every record." />
      <IntelFilters />
      <CoverageNotes />
      {isError ? <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card> : (
        <>
          {data && data.data.undated_excluded > 0 && (
            <LimitationNote>{fmtNumber(data.data.undated_excluded)} records without a publication date (startups, products) are excluded by the date filter.</LimitationNote>
          )}
          <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Topic frequency</CardTitle>
                  <CardDescription>{data ? `${fmtNumber(data.data.records)} records in this selection · top 12 topics` : "Loading"}</CardDescription>
                </div>
              </CardHeader>
              <CardContent>
                {isPending ? <ChartSkeleton height={360} /> : chart.length === 0 ? <EmptyState title="No topics in this selection" /> : (
                  <div className="h-[380px]" data-testid="topic-chart">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={chart} layout="vertical" margin={{ left: 8, right: 12 }}>
                        <CartesianGrid horizontal={false} stroke="var(--chart-grid)" />
                        <XAxis type="number" {...axis} />
                        <YAxis type="category" dataKey="label" width={150} {...axis} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} />
                        <RTooltip {...tooltipStyle} />
                        <Legend wrapperStyle={{ fontSize: 11 }} />
                        {types.map((k) => <Bar key={k} dataKey={k} name={TYPE_LABEL[k]} stackId="a" fill={TYPE_COLOR[k]} />)}
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </CardContent>
            </Card>
            {isPending ? <Card className="p-4"><ListSkeleton rows={5} /></Card> : <EmergingPanel data={data.data} />}
          </div>
          <Card className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-sm" data-testid="topic-table">
              <thead><tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th scope="col" className="px-4 py-2.5 font-medium">Topic</th>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">Records</th>
                {types.map((k) => <th key={k} scope="col" className="px-3 py-2.5 text-right font-medium">{TYPE_LABEL[k]} (share)</th>)}
                <th scope="col" className="px-3 py-2.5 font-medium">Trend</th>
              </tr></thead>
              <tbody>
                {isPending ? <tr><td colSpan={types.length + 3} className="p-4"><ListSkeleton rows={6} /></td></tr> : rows.map((t) => {
                  const em = emergingFor(data.data, t.id);
                  return (
                    <tr key={t.id} className="border-b border-border/60 last:border-0" data-topic={t.id}>
                      <td className="px-4 py-2"><Link href={`/intelligence/trends/${t.id}${params.types ? `?types=${params.types}` : ""}`} className="font-medium hover:text-accent hover:underline">{t.label}</Link>
                        <div className="text-[11px] text-muted-foreground">{t.top_terms.slice(0, 3).map((x) => x.term).join(", ")}</div></td>
                      <td className="tabular px-3 py-2 text-right">{fmtNumber(t.records)}</td>
                      {types.map((k) => (
                        <td key={k} className="tabular px-3 py-2 text-right">{fmtNumber(t.by_type[k] ?? 0)} <span className="text-[11px] text-muted-foreground">({fmtShare(t.share_by_type[k])})</span></td>
                      ))}
                      <td className="px-3 py-2">{em ? <span className="inline-flex items-center gap-1"><TrendBadge status={em.status} /><span className="text-[11px] text-muted-foreground">{TYPE_LABEL[em.type]}</span></span> : <span className="text-xs text-muted-foreground">—</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>
        </>
      )}
    </div>
  );
}

export function TopicDetail({ topicId }: { topicId: string }) {
  const { get, set } = useUrlState();
  const params = useIntelParams();
  const page = Number(get("page")) || 1;
  const pageSize = Number(get("page_size")) || 24;
  const { data, isPending, isError, error, refetch } = useIntelTopic(topicId, { ...params, page, page_size: pageSize });
  const d = data?.data;
  const weekly = (d?.timeline.buckets ?? []).map((b) => ({ week: b.start, share: b.topic_share === null || b.topic_share === undefined ? null : +(b.topic_share * 100).toFixed(1), records: b.topic_records ?? 0, total: b.records }));
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      <Link href="/intelligence/trends" className="inline-flex w-fit items-center gap-1 text-xs text-muted-foreground hover:text-foreground"><ArrowLeft className="size-3.5" /> Trend radar</Link>
      <IntelHeader title={d?.topic.label ?? "Topic"} description={d ? <>Matched by: <code className="text-[11px] break-words">{d.topic.patterns.join(" | ")}</code></> : "Loading"} />
      <IntelFilters />
      {isError ? <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card> : isPending ? <Card className="p-4"><ListSkeleton rows={6} /></Card> : (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" data-testid="topic-counts">
            <Card className="p-3"><div className="text-xs text-muted-foreground">All categories</div><div className="tabular text-xl font-semibold">{fmtNumber(d!.frequency.records)}</div></Card>
            {TYPE_ORDER.map((k) => (
              <Card key={k} className="p-3"><div className="text-xs text-muted-foreground">{TYPE_LABEL[k]}</div>
                <div className="tabular text-xl font-semibold">{fmtNumber(d!.frequency.by_type[k] ?? 0)}</div>
                <div className="text-[11px] text-muted-foreground">{fmtShare(d!.frequency.share_by_type[k])} of category</div></Card>
            ))}
          </div>
          <div className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
            <Card>
              <CardHeader><div><CardTitle>Weekly share of dated records</CardTitle>
                <CardDescription>Share of each week’s dated records that match this topic. Empty weeks have no bar; nothing is interpolated.</CardDescription></div></CardHeader>
              <CardContent>
                {weekly.length === 0 ? <EmptyState title="No dated records in this selection">Startups and products have no publication dates.</EmptyState> : (
                  <div className="h-[260px]" data-testid="topic-weekly">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={weekly} margin={{ left: -10, right: 8 }}>
                        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                        <XAxis dataKey="week" {...axis} tickFormatter={(v: string) => fmtDate(v, { month: "short", day: "numeric" })} minTickGap={24} />
                        <YAxis {...axis} unit="%" />
                        <RTooltip {...tooltipStyle} labelFormatter={(v) => `Week of ${fmtDate(String(v))}`}
                          formatter={(v, _n, item) => [`${v ?? "no records"}${v === null ? "" : "%"} (${(item.payload as { records: number }).records} of ${(item.payload as { total: number }).total})`, "Topic share"]} />
                        <Bar dataKey="share" fill="var(--chart-3)" radius={[3, 3, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader><div><CardTitle>Organisations</CardTitle><CardDescription>Verified links and news co-mentions are kept apart</CardDescription></div></CardHeader>
              <CardContent className="flex flex-col gap-3 text-sm">
                <div>
                  <div className="mb-1 text-xs font-medium text-muted-foreground">Linked records (verified mappings)</div>
                  {d!.organisations.verified.length ? (
                    <ul className="flex flex-col gap-1" data-testid="orgs-verified">
                      {d!.organisations.verified.map((o) => <li key={o.id} className="flex justify-between gap-2"><Link href={entityHref(o.id, true)} className="truncate hover:text-accent hover:underline">{o.name}</Link><span className="tabular text-muted-foreground">{o.records}</span></li>)}
                    </ul>
                  ) : <p className="text-xs text-muted-foreground">None. Research papers carry no organisation affiliation.</p>}
                </div>
                <div>
                  <div className="mb-1 text-xs font-medium text-muted-foreground">Named in news (co-mentions, not relationships)</div>
                  {d!.organisations.mentioned.length ? (
                    <ul className="flex flex-col gap-1" data-testid="orgs-mentioned">
                      {d!.organisations.mentioned.map((o) => <li key={o.id} className="flex justify-between gap-2"><Link href={entityHref(o.id, true)} className="truncate hover:text-accent hover:underline">{o.name}</Link><span className="tabular text-muted-foreground">{o.articles} article{o.articles === 1 ? "" : "s"}</span></li>)}
                    </ul>
                  ) : <p className="text-xs text-muted-foreground">None.</p>}
                </div>
              </CardContent>
            </Card>
          </div>
          <Card>
            <CardHeader><div><CardTitle>Supporting records</CardTitle><CardDescription>{fmtNumber(data!.meta.total)} records · newest publication first · matched term shown</CardDescription></div></CardHeader>
            <CardContent className="flex flex-col gap-3">
              <ul className="flex flex-col divide-y divide-border/60" data-testid="topic-records">
                {d!.records.map((r) => (
                  <li key={`${r.type}-${r.id}`} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                    <Badge>{TYPE_LABEL[r.type]}</Badge>
                    <Link href={recordLink(r.type, r.id)} className="min-w-0 flex-1 truncate font-medium hover:text-accent hover:underline" title={r.name}>{r.name}</Link>
                    {r.matched_term && <Badge tone="accent">“{r.matched_term}”</Badge>}
                    <span className="text-xs text-muted-foreground">{r.published ? fmtDate(r.published) : "undated"}</span>
                  </li>
                ))}
              </ul>
              {data!.meta.total > 0 && (
                <Pagination page={data!.meta.page} pageSize={data!.meta.page_size} total={data!.meta.total} totalPages={data!.meta.total_pages}
                  onPage={(p) => set({ page: p })} onPageSize={(s) => set({ page_size: s })} />
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
