"use client";

import Link from "next/link";
import { AlertTriangle, CheckCircle2, CircleDot, ExternalLink, Scale, ShieldQuestion } from "lucide-react";
import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from "recharts";
import { useEvidence, useTimeline, type Claim } from "@/lib/api/history";
import { fmtDate, fmtNumber } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/tooltip";
import { EmptyState, ErrorState, LimitationNote, ListSkeleton } from "@/components/common/states";
import { axis, tooltipStyle } from "@/components/intelligence/common";

const KIND_LABEL: Record<string, string> = {
  job_posted: "Job posted", news_mention: "News mention", product_listed: "Product listed", profile_listed: "Profile listed",
  paper_published: "Paper published", record_changed: "Record changed", record_disappeared: "Disappeared", record_reappeared: "Reappeared",
};
const KIND_TONE: Record<string, BadgeProps["tone"]> = {
  job_posted: "info", news_mention: "neutral", product_listed: "accent", profile_listed: "accent", record_changed: "warning", record_disappeared: "danger", record_reappeared: "success",
};
const BASIS_LABEL: Record<string, string> = {
  published: "publication date", first_seen_on_or_before: "first seen on or before (baseline)", first_seen: "first seen",
  observed_change: "observed change", observed: "observed", collected: "collected",
};

export function OrgTimeline({ id }: { id: string }) {
  const { data, isPending, isError, error, refetch } = useTimeline(id);
  const [kind, setKind] = useState<string>("");
  const events = useMemo(() => (data?.data.events ?? []).filter((e) => !kind || e.kind === kind), [data, kind]);
  if (isError) return <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card>;
  if (isPending) return <Card className="p-4"><ListSkeleton rows={8} /></Card>;
  const t = data.data;
  if (!t.events.length) return <Card><EmptyState title="No dated events">No verified records are linked to this organisation.</EmptyState></Card>;
  return (
    <div className="flex flex-col gap-4" data-testid="org-timeline">
      {data.note && <LimitationNote>{data.note}</LimitationNote>}
      {t.published_per_month.length > 0 && (
        <Card>
          <CardHeader className="pb-1"><div><CardTitle>Dated activity per month</CardTitle><CardDescription>Events with a source publication date (jobs, news, papers)</CardDescription></div></CardHeader>
          <CardContent className="h-40">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={t.published_per_month} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="month" {...axis} /><YAxis {...axis} width={32} allowDecimals={false} />
                <RTooltip {...tooltipStyle} /><Bar dataKey="events" fill="var(--chart-1)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}
      <Card>
        <CardHeader className="pb-2">
          <div><CardTitle>Timeline</CardTitle><CardDescription>{fmtNumber(t.events.length)} events · each links to its record and source</CardDescription></div>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter events">
            <button onClick={() => setKind("")} className={cn("rounded-full border px-2.5 py-0.5 text-xs", !kind ? "border-accent/50 bg-accent-soft text-accent" : "border-border text-muted-foreground")}>All {t.events.length}</button>
            {Object.entries(t.counts).map(([k, n]) => (
              <button key={k} onClick={() => setKind(k)} className={cn("rounded-full border px-2.5 py-0.5 text-xs", kind === k ? "border-accent/50 bg-accent-soft text-accent" : "border-border text-muted-foreground")}>
                {KIND_LABEL[k] ?? k} {n}
              </button>
            ))}
          </div>
          <ol className="relative flex flex-col gap-3 border-l border-border pl-4">
            {events.slice(0, 200).map((e, i) => (
              <li key={i} className="relative">
                <CircleDot className="absolute top-0.5 -left-[22px] size-3 bg-surface text-accent" />
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="tabular font-medium">{e.date ? fmtDate(e.date) : "—"}</span>
                  <Badge tone={KIND_TONE[e.kind] ?? "neutral"}>{KIND_LABEL[e.kind] ?? e.kind}</Badge>
                  <span className="text-muted-foreground">{BASIS_LABEL[e.date_basis] ?? e.date_basis} · {e.source}</span>
                </div>
                <Link href={`/${e.record.type}/${e.record.id}`} className="mt-0.5 block text-sm hover:text-accent">{e.title}{e.detail ? <span className="text-muted-foreground"> · {e.detail}</span> : null}</Link>
              </li>
            ))}
          </ol>
          {events.length > 200 && <p className="text-[11px] text-muted-foreground">Showing the 200 most recent of {events.length} events.</p>}
        </CardContent>
      </Card>
    </div>
  );
}

type TierInfo = { tone: BadgeProps["tone"]; label: string; icon: typeof CheckCircle2 };
const SINGLE: TierInfo = { tone: "neutral", label: "Single source", icon: CircleDot };
const TIER: Record<string, TierInfo> = {
  corroborated: { tone: "success", label: "Corroborated", icon: CheckCircle2 },
  single_source: SINGLE,
  conflicting: { tone: "danger", label: "Conflicting", icon: AlertTriangle },
};
const CLAIM_LABEL: Record<string, string> = {
  builds_product: "Builds product", hiring: "Hiring for", directory_listing: "Listed by", yc_batch: "Y Combinator batch", website: "Website", news_coverage: "News story",
};

function ClaimCard({ c }: { c: Claim }) {
  const [open, setOpen] = useState(false);
  const t = TIER[c.support_tier] ?? SINGLE;
  const Icon = t.icon;
  return (
    <li className="rounded-md border border-border px-3 py-2" data-testid="claim">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">{CLAIM_LABEL[c.kind] ?? c.kind}</span>
        <span className="text-sm font-medium">{c.object}{c.value ? `: ${c.value}` : ""}</span>
        <Badge tone={t.tone}><Icon className="size-3" />{t.label}</Badge>
        <Tooltip content={`Independent source groups: ${c.independent_source_groups.join(", ") || "none"} · strongest evidence: ${c.strongest_evidence}${c.syndicated_copies ? ` · ${c.syndicated_copies} syndicated copies not counted` : ""}`}>
          <Badge tone="info" tabIndex={0}>confidence {Math.round(c.confidence * 100)}%</Badge>
        </Tooltip>
        {c.needs_review && <Badge tone="warning"><ShieldQuestion className="size-3" />needs review</Badge>}
      </div>
      {c.conflicting_values.length > 0 && <p className="mt-1 text-xs text-danger">Other independent sources state: {c.conflicting_values.join(", ")}</p>}
      <button onClick={() => setOpen(!open)} className="mt-1 text-xs text-accent hover:underline" aria-expanded={open}>
        {open ? "Hide" : "Show"} {c.evidence_count} evidence item{c.evidence_count === 1 ? "" : "s"}
      </button>
      {open && (
        <ul className="mt-1 flex flex-col gap-1 border-l border-border pl-3">
          {c.evidence.map((e, i) => (
            <li key={i} className="text-xs">
              <Badge tone={e.kind === "primary" ? "accent" : e.kind === "syndicated" ? "warning" : "neutral"}>{e.kind}</Badge>{" "}
              <Link href={`/${{ STARTUP: "startups", PRODUCT: "products", JOB: "jobs", NEWS: "news", RESEARCH_PAPER: "papers" }[e.record_type] ?? "explore"}/${e.record_id}`} className="hover:text-accent">{e.snippet}</Link>
              <span className="text-muted-foreground"> · {e.source}{e.date ? ` · ${fmtDate(e.date)}` : ""}</span>
              {e.url && <a href={e.url} target="_blank" rel="noopener noreferrer" className="ml-1 inline-flex text-muted-foreground hover:text-foreground" aria-label="Open source"><ExternalLink className="size-3" /></a>}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function OrgEvidence({ id }: { id: string }) {
  const { data, isPending, isError, error, refetch } = useEvidence(id);
  const [tier, setTier] = useState("");
  if (isError) return <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card>;
  if (isPending) return <Card className="p-4"><ListSkeleton rows={8} /></Card>;
  const d = data.data;
  const claims = d.claims.filter((c) => !tier || c.support_tier === tier);
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_320px]" data-testid="org-evidence">
      <Card>
        <CardHeader className="pb-2"><div><CardTitle className="flex items-center gap-1.5"><Scale className="size-4" /> Evidence-backed claims</CardTitle>
          <CardDescription>Each claim is stated by the linked records themselves; corroboration counts independent source groups only</CardDescription></div></CardHeader>
        <CardContent className="flex flex-col gap-3">
          {d.review && <LimitationNote>{d.review.note} Pending: {d.review.pending_names.join(", ")}.</LimitationNote>}
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter claims">
            {[["", "All", d.claims.length], ...Object.entries(d.summary).map(([k, n]) => [k, TIER[k]?.label ?? k, n] as const)].map(([k, label, n]) => (
              <button key={String(k)} onClick={() => setTier(String(k))} className={cn("rounded-full border px-2.5 py-0.5 text-xs", tier === k ? "border-accent/50 bg-accent-soft text-accent" : "border-border text-muted-foreground")}>{label} {n}</button>
            ))}
          </div>
          {claims.length === 0 ? <EmptyState title="No claims">No linked record states a claim of this kind.</EmptyState> : (
            <ul className="flex flex-col gap-2">{claims.map((c, i) => <ClaimCard key={`${c.kind}-${c.object}-${c.value}-${i}`} c={c} />)}</ul>
          )}
        </CardContent>
      </Card>
      <aside className="flex flex-col gap-4">
        <Card>
          <CardHeader className="pb-1"><CardTitle>How confidence is computed</CardTitle></CardHeader>
          <CardContent className="text-xs text-muted-foreground">{d.confidence_formula}</CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-1"><div><CardTitle>Co-mentioned in news</CardTitle><CardDescription>{d.co_mentions_note}</CardDescription></div></CardHeader>
          <CardContent>
            {d.co_mentions.length === 0 ? <p className="text-xs text-muted-foreground">No co-mentions.</p> : (
              <ul className="flex flex-col gap-1 text-xs">{d.co_mentions.map((m) => <li key={m.organisation} className="flex justify-between"><span>{m.organisation}</span><span className="tabular text-muted-foreground">{m.articles}</span></li>)}</ul>
            )}
          </CardContent>
        </Card>
      </aside>
    </div>
  );
}
