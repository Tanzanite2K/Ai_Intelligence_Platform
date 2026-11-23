"use client";

import Link from "next/link";
import { ArrowRight, ExternalLink, FileText, Lock, ShieldAlert } from "lucide-react";
import { useQuality, useReviews } from "@/lib/api/hooks";
import type { ReviewItem } from "@/lib/api/schemas";
import { TYPE_LABEL, TYPE_ORDER, fmtDateTime, fmtNumber } from "@/lib/format";
import { entityHref } from "@/lib/entities";
import { recordHref } from "@/lib/records";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/tooltip";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/common/states";
import { MethodBadge } from "./common";

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[110px_1fr] gap-2 py-1 text-sm">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="min-w-0">{children}</span>
    </div>
  );
}

function ReviewCard({ r }: { r: ReviewItem }) {
  const a = r.audit;
  const uncertain = a?.assessment?.toLowerCase().startsWith("uncertain");
  const shared = r.shares_canonical_with > 0;
  // A true collision: the candidate it may NOT be merged into is stored under the very same canonical name (e.g. Cohere vs Cohere).
  const collision = shared && r.candidate_entity?.canonical_name === r.canonical_name;
  return (
    <Card className="flex flex-col">
      <CardHeader className="pb-2">
        <div className="min-w-0">
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">
            {r.original_name}
            <Badge tone="warning"><ShieldAlert className="size-3" /> pending review</Badge>
          </CardTitle>
          <CardDescription>Mapping #{r.id} · flagged {fmtDateTime(r.resolved_at)}</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3">
        <section aria-label="Stored in the database">
          <div className="mb-1 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">Stored in the database</div>
          <Fact label="Match method"><MethodBadge method={r.match_method} /></Fact>
          <Fact label="Confidence"><span className="tabular">{r.confidence}</span> <span className="text-xs text-muted-foreground">(fixed value for review cases)</span></Fact>
          <Fact label="Kept as">
            <Link href={`${entityHref(r.canonical_name)}${shared ? "?tab=review" : ""}`} className="text-accent hover:underline">{r.canonical_name}</Link>
            <span className="text-xs text-muted-foreground"> (separate entity, not merged)</span>
          </Fact>
          <Fact label="Source record">
            {r.source_record ? (
              <Link href={recordHref(r.source_record.type, r.source_record.id)} className="inline-flex items-center gap-1 text-accent hover:underline">
                {TYPE_LABEL[r.source_record.type]} · {r.source_record.name} <ArrowRight className="size-3" />
              </Link>
            ) : (
              <a href={r.source_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">source page <ExternalLink className="size-3" /></a>
            )}
            {r.source_record && <div className="truncate text-xs text-muted-foreground">{r.source_record.source_name}{r.source_record.url ? ` · ${r.source_record.url.replace(/^https?:\/\/(www\.)?/, "")}` : ""}</div>}
          </Fact>
        </section>

        {collision ? (
          <div className="rounded-md border border-warning/30 bg-warning/8 px-3 py-2 text-xs text-muted-foreground">
            <b className="text-warning">Name collision:</b> the candidate it was flagged against is stored under the same canonical name “{r.canonical_name}”
            ({fmtNumber(r.shares_canonical_with)} verified mappings). The profile keeps this case apart under “Pending review” so the two are not mixed.
          </div>
        ) : shared ? (
          <p className="text-xs text-muted-foreground">
            {fmtNumber(r.shares_canonical_with)} other verified mapping(s) use the name “{r.canonical_name}”; this pending case is listed separately under “Pending review” on that profile.
          </p>
        ) : null}

        <section aria-label="Audit notes" className="rounded-md border border-border bg-surface-2/40 p-3">
          <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
            <FileText className="size-3.5" /> Audit notes
            <Tooltip content="The resolver stores only the REVIEW flag; the candidate, reason and evidence were recorded in docs/entity_review.md during the manual audit on 2026-11-21.">
              <span tabIndex={0} className="cursor-help font-normal normal-case underline decoration-dotted">docs/entity_review.md</span>
            </Tooltip>
          </div>
          {a ? (
            <>
              <Fact label="Candidate">
                {r.candidate_entity ? (
                  <Link href={entityHref(r.candidate_entity.id, true)} className="text-accent hover:underline">{r.proposed_candidate}</Link>
                ) : (
                  <Tooltip content="No verified mapping with this canonical name exists, so there is no profile to open.">
                    <span tabIndex={0}>{r.proposed_candidate}</span>
                  </Tooltip>
                )}
                {r.candidate_entity && <span className="text-xs text-muted-foreground"> · {fmtNumber(r.candidate_entity.records)} records, {fmtNumber(r.candidate_entity.verified_mappings)} mappings</span>}
              </Fact>
              <Fact label="Why flagged"><code className="text-xs">{a.reason}</code></Fact>
              <Fact label="Evidence">{a.evidence}</Fact>
              <Fact label="Assessment">
                <Badge tone={uncertain ? "warning" : "neutral"}>{uncertain ? "uncertain" : "recommendation"}</Badge> <span className="text-xs">{a.assessment}</span>
                <div className="text-[11px] text-muted-foreground">Recommendation only — not applied.</div>
              </Fact>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">No audit notes recorded for this case. The candidate and reason were not persisted by the resolver.</p>
          )}
        </section>
      </CardContent>
    </Card>
  );
}

function QualitySummary() {
  const { data, isPending, isError, error, refetch } = useQuality();
  return (
    <Card>
      <CardHeader><div><CardTitle>Record validation</CardTitle><CardDescription>Validation status and date verification assigned at ingestion</CardDescription></div></CardHeader>
      <CardContent className="overflow-x-auto">
        {isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : isPending ? <ListSkeleton rows={5} /> : (
          <table className="w-full min-w-[520px] text-sm">
            <thead><tr className="border-b border-border text-left text-xs text-muted-foreground">
              <th scope="col" className="py-2 pr-3 font-medium">Type</th>
              <th scope="col" className="py-2 pr-3 text-right font-medium">Verified</th>
              <th scope="col" className="py-2 pr-3 text-right font-medium">Partial</th>
              <th scope="col" className="py-2 pr-3 text-right font-medium">With publication date</th>
              <th scope="col" className="py-2 text-right font-medium">Date verified</th>
            </tr></thead>
            <tbody className="tabular">
              {TYPE_ORDER.map((t) => {
                const st = data.data.validation_status[t] ?? {};
                const d = data.data.dates.find((x) => x.type === t);
                return (
                  <tr key={t} className="border-b border-border/60 last:border-0">
                    <td className="py-2 pr-3">{TYPE_LABEL[t]}</td>
                    <td className="py-2 pr-3 text-right">{fmtNumber(st.VERIFIED ?? 0)}</td>
                    <td className="py-2 pr-3 text-right">{(st.PARTIAL ?? 0) > 0 ? <Tooltip content={data.meta.statuses.PARTIAL ?? ""}><span tabIndex={0} className="text-warning">{fmtNumber(st.PARTIAL ?? 0)}</span></Tooltip> : 0}</td>
                    <td className="py-2 pr-3 text-right">{d ? `${fmtNumber(d.dated)} / ${fmtNumber(d.total)}` : "—"}</td>
                    <td className="py-2 text-right">{d ? fmtNumber(d.date_verified) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <p className="mt-2 text-[11px] text-muted-foreground">
          Startups and products have no publication date in their sources. Paper dates come from arXiv/Hugging Face metadata without a separate verification step.
        </p>
      </CardContent>
    </Card>
  );
}

export function DataQuality() {
  const { data, isPending, isError, error, refetch } = useReviews();
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Data quality</h1>
        <p className="text-sm text-muted-foreground">Entity-resolution cases awaiting a human decision, and how records were validated.</p>
      </div>
      <Card className="flex items-start gap-3 p-4">
        <Lock className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div className="text-sm">
          <div className="font-medium">Read-only review queue{data ? ` · ${fmtNumber(data.meta.total)} pending` : ""}</div>
          <p className="text-xs text-muted-foreground">
            Each case is stored as its own organisation (no merge) until someone decides. Decisions are not made here: they are applied by editing
            <code className="mx-1">src/entity_resolution/seed.py</code> and running <code className="mx-1">python -m src.main rebuild-entities</code> after a verified database backup.
          </p>
        </div>
      </Card>
      {isError ? <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card> : isPending ? <Card className="p-4"><ListSkeleton rows={6} /></Card> : data.data.length === 0 ? (
        <Card><EmptyState title="No mappings pending review" /></Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">{data.data.map((r) => <ReviewCard key={r.id} r={r} />)}</div>
      )}
      <QualitySummary />
    </div>
  );
}
