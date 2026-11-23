"use client";

import Link from "next/link";
import { Building2, Info, Link2, Newspaper } from "lucide-react";
import { useIntelMap, useIntelSignals } from "@/lib/api/hooks";
import type { MapOrg, SignalCategory } from "@/lib/api/schemas";
import { entityHref } from "@/lib/entities";
import { TYPE_LABEL, fmtDate, fmtNumber } from "@/lib/format";
import { useUrlState } from "@/lib/url-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/common/states";
import { Field, Pagination, Select, TextFilter } from "@/components/explore/controls";
import { CoverageNotes, IntelFilters, IntelHeader, TYPE_COLOR, fmtShare, recordLink, useIntelParams } from "./common";
import { TOPIC_OPTIONS } from "./topics";

/* ------------------------------------------------------------------ Market Signals */
function Pct({ n, of }: { n: number; of: number }) {
  return <span className="text-muted-foreground">{of ? `${Math.round((n / of) * 100)}%` : "—"}</span>;
}

function SignalCard({ c }: { c: SignalCategory }) {
  const max = Math.max(1, ...c.top_topics.map((t) => t.records));
  return (
    <Card data-testid="signal-card" data-type={c.type}>
      <CardHeader className="pb-2">
        <div>
          <CardTitle className="flex items-center gap-2"><span className="size-2.5 rounded-full" style={{ background: TYPE_COLOR[c.type] }} />{c.label}</CardTitle>
          <CardDescription>
            Collected {c.collection_days.map((d) => fmtDate(d)).join(", ") || "—"} · {Object.entries(c.sources).map(([s, n]) => `${s} ${fmtNumber(n)}`).join(" · ")}
          </CardDescription>
        </div>
        <div className="tabular text-right text-2xl font-semibold">{fmtNumber(c.records)}<div className="text-[11px] font-normal text-muted-foreground">records collected</div></div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        <dl className="grid grid-cols-3 gap-2 text-xs">
          <div><dt className="text-muted-foreground">With publication date</dt><dd className="tabular font-medium">{fmtNumber(c.with_publication_date)} <Pct n={c.with_publication_date} of={c.records} /></dd></div>
          <div><dt className="text-muted-foreground">With a topic</dt><dd className="tabular font-medium">{fmtNumber(c.with_topic)} <Pct n={c.with_topic} of={c.records} /></dd></div>
          <div><dt className="text-muted-foreground">Verified organisation</dt><dd className="tabular font-medium">{fmtNumber(c.with_verified_organisation)} <Pct n={c.with_verified_organisation} of={c.records} /></dd></div>
        </dl>
        {c.publication_first && <p className="text-xs text-muted-foreground">Publication dates {fmtDate(c.publication_first)} – {fmtDate(c.publication_last)}</p>}
        <div>
          <div className="mb-1 text-xs font-medium text-muted-foreground">Leading topics (share of category)</div>
          {c.top_topics.length ? (
            <ul className="flex flex-col gap-1">
              {c.top_topics.map((t) => (
                <li key={t.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 text-xs">
                  <Link href={`/intelligence/trends/${t.id}?types=${c.type}`} className="relative truncate rounded px-1.5 py-0.5 hover:underline">
                    <span className="absolute inset-y-0 left-0 rounded bg-accent-soft" style={{ width: `${(t.records / max) * 100}%` }} />
                    <span className="relative">{t.label}</span>
                  </Link>
                  <span className="tabular text-muted-foreground">{fmtNumber(t.records)} · {fmtShare(t.share)}</span>
                </li>
              ))}
            </ul>
          ) : <p className="text-xs text-muted-foreground">No topic matched.</p>}
        </div>
        {c.top_organisations.length > 0 && (
          <div>
            <div className="mb-1 text-xs font-medium text-muted-foreground">Most linked organisations (verified)</div>
            <div className="flex flex-wrap gap-1">
              {c.top_organisations.map((o) => <Link key={o.id} href={entityHref(o.id, true)} className="rounded-md border border-border px-1.5 py-0.5 text-xs hover:underline">{o.name} <span className="text-muted-foreground">{o.records}</span></Link>)}
            </div>
          </div>
        )}
        {c.most_mentioned.length > 0 && (
          <div>
            <div className="mb-1 text-xs font-medium text-muted-foreground">Most named in articles (co-mentions)</div>
            <div className="flex flex-wrap gap-1">
              {c.most_mentioned.map((o) => <Link key={o.id} href={entityHref(o.id, true)} className="rounded-md border border-dashed border-border px-1.5 py-0.5 text-xs hover:underline">{o.name} <span className="text-muted-foreground">{o.articles}</span></Link>)}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function MarketSignals() {
  const params = useIntelParams();
  const { data, isPending, isError, error, refetch } = useIntelSignals(params);
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      <IntelHeader title="Market signals" description="Measurable indicators per data category. Every number counts records this platform collected from specific sources: it measures collection volume, not market growth." />
      <IntelFilters />
      {data ? <CoverageNotes extra={data.data.notes.filter((n) => n.startsWith("Signals count"))} /> : <CoverageNotes />}
      {isError ? <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card> : isPending ? <Card className="p-4"><ListSkeleton rows={6} /></Card> : data.data.categories.length === 0 ? (
        <Card><EmptyState title="No categories selected" /></Card>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">{data.data.categories.map((c) => <SignalCard key={c.type} c={c} />)}</div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ Innovation Map */
function Matrix({ m }: { m: { organisations: string[]; topics: { id: string; label: string }[]; cells: number[][] } }) {
  const max = Math.max(1, ...m.cells.flat());
  if (!m.organisations.length) return <EmptyState title="No organisation has verified records in this selection" />;
  return (
    <div className="overflow-x-auto pr-28" data-testid="org-topic-matrix">
      <table className="table-fixed text-xs">
        <thead><tr>
          <th scope="col" className="sticky left-0 bg-surface px-2 py-1 text-left font-medium text-muted-foreground">Organisation</th>
          {m.topics.map((t) => (
            <th key={t.id} scope="col" className="relative h-44 w-9 min-w-9 max-w-9 p-0 align-bottom font-normal text-muted-foreground">
              <Link href={`/intelligence/trends/${t.id}`} title={t.label} className="absolute bottom-2 left-4 origin-bottom-left -rotate-45 whitespace-nowrap hover:text-foreground">{t.label}</Link>
            </th>
          ))}
        </tr></thead>
        <tbody>
          {m.organisations.map((o, i) => (
            <tr key={o}>
              <th scope="row" className="sticky left-0 max-w-40 truncate bg-surface px-2 py-0.5 text-left font-medium"><Link href={entityHref(o)} className="hover:underline">{o}</Link></th>
              {(m.cells[i] ?? []).map((v, j) => (
                <td key={j} className="p-0.5"><div title={`${o} · ${m.topics[j]?.label ?? ""}: ${v} verified record(s)`}
                  className="tabular flex size-8 items-center justify-center rounded text-[10px]"
                  style={{ background: v ? `color-mix(in oklch, var(--accent) ${Math.round(15 + (v / max) * 70)}%, transparent)` : "var(--surface-2)", color: v / max > 0.5 ? "white" : undefined }}>
                  {v || ""}</div></td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OrgRow({ o }: { o: MapOrg }) {
  return (
    <li className="flex flex-col gap-1.5 py-3" data-testid="map-org">
      <div className="flex flex-wrap items-center gap-2">
        <Link href={entityHref(o.id, true)} className="font-medium hover:text-accent hover:underline">{o.name}</Link>
        {Object.entries(o.by_type).map(([t, n]) => (
          <Badge key={t} tone="success"><Link2 className="size-3" />{n} {(TYPE_LABEL[t] ?? t).toLowerCase()}</Badge>
        ))}
        {o.news_mentions > 0 && <Badge><Newspaper className="size-3" />named in {o.news_mentions} article{o.news_mentions === 1 ? "" : "s"}</Badge>}
      </div>
      {o.topics.length > 0 && (
        <div className="flex flex-wrap gap-1 text-xs">
          {o.topics.map((t) => <Link key={t.id} href={`/intelligence/trends/${t.id}`} className="rounded bg-accent-soft px-1.5 py-0.5 text-accent hover:underline">{t.label} {t.records}</Link>)}
        </div>
      )}
      {o.co_mentioned_with.length > 0 && (
        <p className="text-xs text-muted-foreground" data-testid="co-mentions">
          Co-mentioned in news with {o.co_mentioned_with.map((c, i) => <span key={c.id}>{i > 0 && ", "}<Link href={entityHref(c.id, true)} className="hover:text-foreground hover:underline">{c.name}</Link> ({c.articles})</span>)}
        </p>
      )}
      {o.sample_records.length > 0 && (
        <p className="truncate text-xs text-muted-foreground">e.g. {o.sample_records.map((r, i) => <span key={`${r.type}-${r.id}`}>{i > 0 && " · "}<Link href={recordLink(r.type, r.id)} className="hover:text-foreground hover:underline">{r.name}</Link></span>)}</p>
      )}
    </li>
  );
}

export function InnovationMap() {
  const { get, set } = useUrlState();
  const params = useIntelParams(["topic", "q", "relation"]);
  const page = Number(get("page")) || 1;
  const pageSize = Number(get("page_size")) || 24;
  const { data, isPending, isError, error, refetch } = useIntelMap({ ...params, page, page_size: pageSize });
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      <IntelHeader title="Innovation map" description="Organisations connected to startups, products and jobs through verified entity mappings, and to news through co-mentions. Co-mentions never imply partnership, investment or ownership." />
      <IntelFilters>
        <Field label="Topic" htmlFor="map-topic"><Select id="map-topic" value={get("topic")} onChange={(v) => set({ topic: v })} options={TOPIC_OPTIONS} placeholder="All topics" /></Field>
        <Field label="Connection" htmlFor="map-relation">
          <Select id="map-relation" value={get("relation") || "all"} onChange={(v) => set({ relation: v === "all" ? "" : v })}
            options={[{ value: "all", label: "Verified links and co-mentions" }, { value: "verified", label: "Verified links only" }, { value: "mentioned", label: "News co-mentions only" }]} />
        </Field>
        <Field label="Organisation" htmlFor="map-q"><TextFilter id="map-q" value={get("q")} onCommit={(v) => set({ q: v })} placeholder="Name contains…" /></Field>
      </IntelFilters>
      <Card className="p-3">
        <ul className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-3" data-testid="map-legend">
          <li className="flex gap-1.5"><Link2 className="size-3.5 shrink-0 text-success" /><span><b className="text-foreground">Verified link</b>: the record belongs to the organisation by a verified entity mapping (pending reviews excluded).</span></li>
          <li className="flex gap-1.5"><Newspaper className="size-3.5 shrink-0" /><span><b className="text-foreground">Co-mention</b>: named in the same news article (evidence-validated tag). Not a relationship.</span></li>
          <li className="flex gap-1.5"><Info className="size-3.5 shrink-0" /><span>Research papers have no organisation affiliation in the stored data and appear only through topics.</span></li>
        </ul>
      </Card>
      {isError ? <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card> : isPending ? <Card className="p-4"><ListSkeleton rows={6} /></Card> : (
        <div className="grid gap-4 2xl:grid-cols-[1fr_1fr]">
          <Card>
            <CardHeader><div><CardTitle>Organisation × topic</CardTitle><CardDescription>Verified records per organisation and topic (top 12 × 10 in this selection)</CardDescription></div></CardHeader>
            <CardContent><Matrix m={data.data.matrix} /></CardContent>
          </Card>
          <Card>
            <CardHeader><div><CardTitle className="flex items-center gap-1.5"><Building2 className="size-4" /> Organisations</CardTitle><CardDescription>{fmtNumber(data.meta.total)} match · most verified records first</CardDescription></div></CardHeader>
            <CardContent className="flex flex-col gap-2">
              {data.data.organisations.length === 0 ? <EmptyState title="No organisations match these filters" /> : (
                <ul className="flex flex-col divide-y divide-border/60">{data.data.organisations.map((o) => <OrgRow key={o.id} o={o} />)}</ul>
              )}
              {data.meta.total > 0 && <Pagination page={data.meta.page} pageSize={data.meta.page_size} total={data.meta.total} totalPages={data.meta.total_pages} onPage={(p) => set({ page: p })} onPageSize={(s) => set({ page_size: s })} />}
            </CardContent>
          </Card>
        </div>
      )}
      <CoverageNotes />
    </div>
  );
}
