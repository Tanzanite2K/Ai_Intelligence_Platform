"use client";

import Link from "next/link";
import { ArrowLeft, ExternalLink, Globe, Info, ShieldAlert } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import { useEntity, useEntityRecords, useEntityRelated } from "@/lib/api/hooks";
import type { EntityDetail, TypeSlug } from "@/lib/api/schemas";
import { TYPE_LABEL, TYPE_ORDER, fmtDateTime, fmtNumber } from "@/lib/format";
import { useUrlState } from "@/lib/url-state";
import { entityHref, METHOD_INFO } from "@/lib/entities";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip } from "@/components/ui/tooltip";
import { EmptyState, ErrorState, LimitationNote } from "@/components/common/states";
import { Pagination } from "@/components/explore/controls";
import { ListSkeletonRows, pageParams } from "@/components/explore/explorer";
import { RecordRow } from "@/components/explore/record-items";
import { MethodBadge, OriginBadge, ReviewBadge } from "./common";
import { OrgEvidence, OrgTimeline } from "@/components/history/org-history";

type Tab = "overview" | "timeline" | "evidence" | TypeSlug | "review" | "relationships" | "provenance";

function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  const body = (
    <div className="flex flex-col gap-0.5 rounded-md border border-border bg-surface-2/40 px-3 py-2">
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <span className="tabular text-lg font-semibold">{value}</span>
    </div>
  );
  return hint ? <Tooltip content={hint}><div tabIndex={0}>{body}</div></Tooltip> : body;
}

function Overview({ e }: { e: EntityDetail }) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader><CardTitle>Identity</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-3 text-sm">
          <div className="grid grid-cols-[130px_1fr] gap-y-2">
            <span className="text-xs text-muted-foreground">Canonical name</span><span className="font-medium">{e.canonical_name}</span>
            <span className="text-xs text-muted-foreground">Entity ID</span><code className="text-xs break-all">{e.id}</code>
            <span className="text-xs text-muted-foreground">Origin</span><span><OriginBadge origin={e.origin} />{e.seed_id !== null && <span className="ml-2 text-xs text-muted-foreground">seed #{e.seed_id}</span>}</span>
            <span className="text-xs text-muted-foreground">Domain</span>
            <span>{e.domain ? <a href={`https://${e.domain}`} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">{e.domain} <ExternalLink className="size-3" /></a> : <span className="text-muted-foreground/70 italic">not stored</span>}</span>
            <span className="text-xs text-muted-foreground">Aliases</span>
            <span className="flex flex-wrap gap-1">{e.aliases.length ? e.aliases.map((a) => <Badge key={a}>{a}</Badge>) : <span className="text-muted-foreground/70 italic">{e.origin === "SEED" ? "none curated" : "only curated organisations have aliases"}</span>}</span>
            <span className="text-xs text-muted-foreground">Names in sources</span>
            <span className="flex flex-wrap gap-1">{e.original_names.map((n) => <Badge key={n}>{n}</Badge>)}</span>
            <span className="text-xs text-muted-foreground">Description</span>
            <span className="text-muted-foreground/70 italic">{e.unavailable.description ?? "not stored"}</span>
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Linked records</CardTitle><CardDescription>Through verified mappings only</CardDescription></CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
            {TYPE_ORDER.map((t) => <Stat key={t} label={TYPE_LABEL[t] ?? t} value={fmtNumber(e.records_by_type[t] ?? 0)} />)}
          </div>
          {e.review_record_count > 0 && (
            <LimitationNote>
              {fmtNumber(e.review_record_count)} more record(s) carry this name through a mapping that is <b>pending human review</b>. They are
              listed under “Pending review” and are not counted as this organisation’s records.
            </LimitationNote>
          )}
          <div>
            <div className="mb-1 text-xs text-muted-foreground">How source names were matched ({fmtNumber(e.mapping_count)} mappings)</div>
            <div className="flex flex-wrap gap-1">{Object.entries(e.match_methods).sort((a, b) => b[1] - a[1]).map(([m, n]) => <MethodBadge key={m} method={m} count={n} />)}</div>
          </div>
          <div className="flex flex-col gap-1 text-[11px] text-muted-foreground">
            {e.unavailable.papers && <p>· {e.unavailable.papers}</p>}
            {e.unavailable.news && <p>· {e.unavailable.news}</p>}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function RecordsTab({ id, type, mapping = "verified", note }: { id: string; type?: TypeSlug; mapping?: "verified" | "review"; note?: string }) {
  const { all, set } = useUrlState();
  const { page, page_size } = pageParams(all);
  const { data, isPending, isError, error, refetch, isFetching } = useEntityRecords(id, { type, mapping, page, page_size, sort: "newest" });
  return (
    <div className="flex flex-col gap-3">
      {note && <LimitationNote>{note}</LimitationNote>}
      {isError ? <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card> : isPending ? <Card><ListSkeletonRows rows={5} /></Card> : data.data.length === 0 ? (
        <Card><EmptyState title={`No ${type ? (TYPE_LABEL[type] ?? type).toLowerCase() : "records"} linked`}>No stored record is linked to this organisation through {mapping === "review" ? "a pending-review" : "a verified"} mapping.</EmptyState></Card>
      ) : (
        <Card className={cn(isFetching && "opacity-60 transition-opacity")}><ul>{data.data.map((r) => <RecordRow key={r.id} r={r} showType={!type} />)}</ul></Card>
      )}
      {data && <Pagination page={data.meta.page} pageSize={data.meta.page_size} total={data.meta.total} totalPages={data.meta.total_pages} onPage={(p) => set({ page: p })} onPageSize={(s) => set({ page_size: s })} />}
    </div>
  );
}

function RelationshipsTab({ e }: { e: EntityDetail }) {
  const { data, isPending, isError, error, refetch } = useEntityRelated(e.id);
  const types = TYPE_ORDER.filter((t) => (e.records_by_type[t] ?? 0) > 0);
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader><CardTitle>Record-type links</CardTitle><CardDescription>What this organisation is connected to in the stored data</CardDescription></CardHeader>
        <CardContent className="text-sm">
          {types.length === 0 ? <p className="text-muted-foreground">No verified record links.</p> : (
            <ul className="flex flex-col gap-1.5">
              {types.map((t) => <li key={t} className="flex justify-between"><span>{TYPE_LABEL[t]}</span><span className="tabular text-muted-foreground">{fmtNumber(e.records_by_type[t] ?? 0)}</span></li>)}
            </ul>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Related organisations</CardTitle><CardDescription>Co-mentioned in evidence-validated news tags</CardDescription></CardHeader>
        <CardContent>
          {isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : isPending ? <ListSkeletonRows rows={3} /> : data.data.length === 0 ? (
            <EmptyState className="py-4" title="No organisation relationships recorded">{data.note}</EmptyState>
          ) : (
            <ul className="flex flex-col gap-1">{data.data.map((r) => <li key={r.id} className="flex justify-between text-sm"><Link href={entityHref(r.id, true)} className="hover:text-accent">{r.name}</Link><span className="text-xs text-muted-foreground">{r.co_mentions} articles</span></li>)}</ul>
          )}
          <p className="mt-3 text-[11px] text-muted-foreground">Ownership, investment and partnership relationships are not collected by any source.</p>
        </CardContent>
      </Card>
    </div>
  );
}

function ProvenanceTab({ e }: { e: EntityDetail }) {
  return (
    <Card className="overflow-x-auto">
      <CardHeader>
        <div>
          <CardTitle>Entity mappings</CardTitle>
          <CardDescription>
            Every raw name the resolver mapped to “{e.canonical_name}”, as stored{e.mappings.length < e.mapping_count ? ` (first ${e.mappings.length} of ${fmtNumber(e.mapping_count)})` : ""}. Confidence values are the stored ones.
          </CardDescription>
        </div>
      </CardHeader>
      <table className="w-full min-w-[720px] text-sm">
        <thead>
          <tr className="border-y border-border text-left text-xs text-muted-foreground">
            <th scope="col" className="px-4 py-2 font-medium">Name in source</th>
            <th scope="col" className="px-3 py-2 font-medium">Method</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Confidence</th>
            <th scope="col" className="px-3 py-2 font-medium">Status</th>
            <th scope="col" className="px-3 py-2 font-medium">Resolved</th>
            <th scope="col" className="px-3 py-2 font-medium">Source record</th>
          </tr>
        </thead>
        <tbody>
          {e.mappings.map((m) => (
            <tr key={m.id} className={cn("border-b border-border/60 last:border-0", m.needs_review && "bg-warning/5")}>
              <td className="px-4 py-2">{m.original_name}</td>
              <td className="px-3 py-2"><MethodBadge method={m.match_method} /></td>
              <td className="tabular px-3 py-2 text-right">{m.confidence}</td>
              <td className="px-3 py-2">{m.needs_review ? <Badge tone="warning"><ShieldAlert className="size-3" /> pending review</Badge> : <Badge tone="success">verified</Badge>}</td>
              <td className="px-3 py-2 text-xs text-muted-foreground">{fmtDateTime(m.resolved_at)}</td>
              <td className="px-3 py-2">
                <a href={m.source_url} target="_blank" rel="noopener noreferrer" className="inline-flex max-w-[260px] items-center gap-1 truncate text-xs text-accent hover:underline">
                  <span className="truncate">{m.source_url.replace(/^https?:\/\/(www\.)?/, "")}</span><ExternalLink className="size-3 shrink-0" />
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

const TYPE_NOTES: Partial<Record<TypeSlug, string>> = {
  papers: "Stored research papers carry no organisation field, so papers cannot be linked to organisations.",
  news: "Articles appear here when this organisation is one of their evidence-validated organisation tags.",
};

export function OrganisationProfile({ id }: { id: string }) {
  const { get, set } = useUrlState();
  const { data: e, isPending, isError, error, refetch } = useEntity(id);
  const back = <Link href="/organisations" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" /> Organisations</Link>;
  if (!id.startsWith("e_") || (error instanceof ApiError && (error.status === 404 || error.status === 422))) {
    return <div className="mx-auto flex max-w-3xl flex-col gap-4">{back}<Card><EmptyState title="Organisation not found">No organisation with this ID exists in the entity mappings.</EmptyState></Card></div>;
  }
  if (isError) return <div className="mx-auto flex max-w-3xl flex-col gap-4">{back}<Card><ErrorState error={error} onRetry={() => void refetch()} /></Card></div>;
  if (isPending) {
    return (
      <div className="mx-auto flex max-w-[1400px] flex-col gap-4" aria-busy="true">
        {back}<Skeleton className="h-8 w-1/3" /><Skeleton className="h-4 w-1/2" />
        <div className="grid gap-4 lg:grid-cols-2"><Card className="h-56 p-4"><Skeleton className="h-full w-full" /></Card><Card className="h-56 p-4"><Skeleton className="h-full w-full" /></Card></div>
      </div>
    );
  }
  const tabs: { key: Tab; label: string; count?: number }[] = [
    { key: "overview", label: "Overview" },
    { key: "timeline", label: "Timeline" },
    { key: "evidence", label: "Evidence" },
    ...TYPE_ORDER.map((t) => ({ key: t as Tab, label: TYPE_LABEL[t] ?? t, count: e.records_by_type[t] ?? 0 })),
    ...(e.review_record_count > 0 ? [{ key: "review" as Tab, label: "Pending review", count: e.review_record_count }] : []),
    { key: "relationships", label: "Relationships" },
    { key: "provenance", label: "Data provenance", count: e.mapping_count },
  ];
  const tab = (tabs.some((t) => t.key === get("tab")) ? get("tab") : "overview") as Tab;
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      {back}
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="accent">Organisation</Badge>
          <OriginBadge origin={e.origin} />
          {e.needs_review && <ReviewBadge />}
        </div>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{e.canonical_name}</h1>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
          {e.domain && <span className="inline-flex items-center gap-1"><Globe className="size-3.5" />{e.domain}</span>}
          <span className="tabular">{fmtNumber(e.record_count)} verified records</span>
          <span className="tabular">{fmtNumber(e.mapping_count)} mappings</span>
          {e.aliases.length > 0 && <span>Aliases: {e.aliases.join(", ")}</span>}
        </div>
      </header>

      <div role="tablist" aria-label="Profile sections" className="-mx-1 flex gap-1 overflow-x-auto border-b border-border px-1">
        {tabs.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => set({ tab: t.key === "overview" ? null : t.key })}
            className={cn(
              "-mb-px flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2 text-sm whitespace-nowrap",
              tab === t.key ? "border-accent text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
              t.key === "review" && "text-warning",
            )}
          >
            {t.label}
            {t.count !== undefined && <span className="tabular rounded-full bg-surface-2 px-1.5 text-[10px] text-muted-foreground">{fmtNumber(t.count)}</span>}
          </button>
        ))}
      </div>

      <div role="tabpanel">
        {tab === "overview" && <Overview e={e} />}
        {tab === "timeline" && <OrgTimeline id={e.id} />}
        {tab === "evidence" && <OrgEvidence id={e.id} />}
        {(TYPE_ORDER as readonly string[]).includes(tab) && <RecordsTab key={tab} id={e.id} type={tab as TypeSlug} note={TYPE_NOTES[tab as TypeSlug]} />}
        {tab === "review" && (
          <RecordsTab id={e.id} mapping="review"
            note={`These records were stored under the name “${e.canonical_name}” by a mapping that entity resolution flagged as ambiguous. They may belong to a different organisation and are not counted as linked records until a human decides.`} />
        )}
        {tab === "relationships" && <RelationshipsTab e={e} />}
        {tab === "provenance" && <ProvenanceTab e={e} />}
      </div>
      {e.needs_review && <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground"><Info className="size-3.5" /> {METHOD_INFO.REVIEW?.help}</p>}
    </div>
  );
}
