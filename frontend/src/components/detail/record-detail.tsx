"use client";

import Link from "next/link";
import { ArrowLeft, Building2, CheckCircle2, Clock, ExternalLink, FileSearch, Info, Quote, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { useRecord } from "@/lib/api/hooks";
import type { FieldInfo, Provenance, RecordDetail, TypeSlug } from "@/lib/api/schemas";
import { TYPE_LABEL, fmtDate, fmtDateTime, fmtNumber, fmtRelative } from "@/lib/format";
import { entityHref } from "@/lib/entities";
import { fieldLabel, recordHref } from "@/lib/records";
import { cn } from "@/lib/utils";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip } from "@/components/ui/tooltip";
import { EmptyState, ErrorState } from "@/components/common/states";
import { RecordBadges, RecordFacts } from "@/components/explore/record-items";
import { RecordHistoryPanel } from "@/components/history/record-history";
import { useUrlState } from "@/lib/url-state";

const PROV: Record<Provenance, { label: string; tone: BadgeProps["tone"]; help: string }> = {
  source: { label: "Source", tone: "neutral", help: "Taken directly from the original source (API, feed or page)." },
  enriched: { label: "Enriched", tone: "info", help: "Added by the platform from another live source (e.g. GitHub API star counts)." },
  entity_resolution: { label: "Resolved", tone: "accent", help: "Canonical name chosen by entity resolution; the source wrote it differently." },
  llm_validated: { label: "LLM · validated", tone: "success", help: "Extracted by an LLM and accepted only because its verbatim evidence quote was found in the source text." },
  missing: { label: "Missing", tone: "warning", help: "Not present in the stored source data. Nothing has been inferred or filled in." },
};

const DATE_KEYS = new Set(["published_date", "date", "github_stars_checked_at"]);
/** Long-text keys rendered in the body section (a product "description" that is only a task tag is not). */
function bodyKeysFor(r: RecordDetail): string[] {
  const keys = BODY_KEY[r.type] ?? [];
  const desc = r.fields.find((f) => f.key === "description")?.value;
  return r.type === "PRODUCT" && typeof desc === "string" && desc.length <= 40 ? [] : keys;
}

const BODY_KEY: Record<string, string[]> = {
  STARTUP: ["description"],
  PRODUCT: ["description"],
  RESEARCH_PAPER: ["abstract"],
  JOB: ["description"],
  NEWS: ["summary", "full_text"],
};

export function ProvenanceBadge({ p }: { p: Provenance }) {
  const m = PROV[p];
  return (
    <Tooltip content={m.help}>
      <Badge tone={m.tone} tabIndex={0}>{m.label}</Badge>
    </Tooltip>
  );
}

function isUrl(v: unknown): v is string {
  return typeof v === "string" && /^https?:\/\//i.test(v);
}

function Value({ k, v }: { k: string; v: unknown }) {
  if (v === null || v === undefined) return <span className="text-muted-foreground/60 italic">not available</span>;
  if (typeof v === "boolean") return <span>{v ? "Yes" : "No"}</span>;
  if (typeof v === "number") return <span className="tabular">{fmtNumber(v)}</span>;
  if (isUrl(v))
    return (
      <a href={v} target="_blank" rel="noopener noreferrer" className="inline-flex max-w-full items-center gap-1 break-all text-accent hover:underline">
        {v} <ExternalLink className="size-3 shrink-0" />
      </a>
    );
  if (typeof v === "string") return <span className="break-words">{DATE_KEYS.has(k) ? fmtDateTime(v) : v}</span>;
  if (Array.isArray(v)) {
    if (v.length === 0) return <span className="text-muted-foreground/60 italic">none</span>;
    return (
      <span className="flex flex-wrap gap-1">
        {v.map((x, i) => <Badge key={`${String(x)}-${i}`}>{String(x)}</Badge>)}
      </span>
    );
  }
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>);
    if (entries.length === 0) return <span className="text-muted-foreground/60 italic">none</span>;
    return (
      <span className="flex flex-col">
        {entries.map(([a, b]) => (
          <span key={a}><span className="text-muted-foreground">{fieldLabel(a)}:</span> {typeof b === "object" ? JSON.stringify(b) : String(b)}</span>
        ))}
      </span>
    );
  }
  return <span>{String(v)}</span>;
}

function Evidence({ text }: { text: string }) {
  return (
    <blockquote className="mt-1.5 flex gap-1.5 rounded-md border-l-2 border-success/60 bg-success/6 px-2 py-1 text-xs text-muted-foreground">
      <Quote className="mt-0.5 size-3 shrink-0 text-success" />
      <span className="break-words"><span className="font-medium text-foreground/80">Evidence: </span>{text}</span>
    </blockquote>
  );
}

function FieldsTable({ fields, bodyKeys }: { fields: FieldInfo[]; bodyKeys: string[] }) {
  return (
    <dl className="divide-y divide-border/60">
      {fields.map((f) => (
        <div key={f.key} className="grid gap-1 py-2.5 sm:grid-cols-[190px_1fr_auto] sm:gap-3">
          <dt className="text-xs font-medium text-muted-foreground">{fieldLabel(f.key)}</dt>
          <dd className="min-w-0 text-sm">
            {f.provenance === "missing" ? (
              <span className="text-muted-foreground/60 italic">not available in source</span>
            ) : bodyKeys.includes(f.key) ? (
              <span className="text-xs text-muted-foreground">shown above</span>
            ) : (
              <Value k={f.key} v={f.value} />
            )}
            {f.evidence && f.provenance !== "missing" && <Evidence text={f.evidence} />}
            {f.note && f.provenance !== "missing" && f.provenance !== "source" && <p className="mt-1 text-[11px] text-muted-foreground">{f.note}</p>}
          </dd>
          <dd className="sm:text-right"><ProvenanceBadge p={f.provenance} /></dd>
        </div>
      ))}
    </dl>
  );
}

function Body({ r }: { r: RecordDetail }) {
  const [expanded, setExpanded] = useState(false);
  const byKey = new Map(r.fields.map((f) => [f.key, f]));
  const keys = BODY_KEY[r.type] ?? [];
  const blocks = keys.map((k) => byKey.get(k)).filter((f): f is FieldInfo => !!f && typeof f.value === "string" && f.value.length > 0);
  if (r.type === "PRODUCT" && blocks[0] && (blocks[0].value as string).length <= 40) return null; // task tag, shown as a fact
  if (blocks.length === 0) {
    return (
      <Card>
        <CardContent className="pt-4 text-sm text-muted-foreground italic">The source provides no description for this record.</CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardContent className="flex flex-col gap-4 pt-4">
        {blocks.map((f) => {
          const text = f.value as string;
          const long = f.key === "full_text" && text.length > 1200;
          return (
            <section key={f.key}>
              <h2 className="mb-1.5 flex items-center gap-2 text-xs font-semibold text-muted-foreground uppercase">
                {fieldLabel(f.key)} <ProvenanceBadge p={f.provenance} />
              </h2>
              <p className={cn("text-sm leading-relaxed whitespace-pre-line", long && !expanded && "line-clamp-[12]")}>{text}</p>
              {long && (
                <Button variant="ghost" size="sm" className="mt-1 px-0 text-accent" onClick={() => setExpanded((e) => !e)}>
                  {expanded ? "Show less" : "Show full article text"}
                </Button>
              )}
            </section>
          );
        })}
      </CardContent>
    </Card>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1.5 text-sm">
      <span className="shrink-0 text-xs text-muted-foreground">{label}</span>
      <span className="min-w-0 text-right">{children}</span>
    </div>
  );
}

function freshnessLabel(h: number | null): { text: string; tone: BadgeProps["tone"] } {
  if (h === null) return { text: "unknown", tone: "neutral" };
  if (h <= 24) return { text: "< 24 h", tone: "success" };
  if (h <= 24 * 7) return { text: "< 7 days", tone: "info" };
  if (h <= 24 * 30) return { text: "< 30 days", tone: "neutral" };
  return { text: `${Math.round(h / 24)} days`, tone: "neutral" };
}

function FreshnessCard({ r }: { r: RecordDetail }) {
  const f = freshnessLabel(r.freshness_hours);
  const dateSource = r.fields.find((x) => x.key === "date_source")?.value;
  return (
    <Card>
      <CardHeader className="pb-1"><CardTitle className="flex items-center gap-1.5"><Clock className="size-4" /> Freshness</CardTitle></CardHeader>
      <CardContent className="divide-y divide-border/60">
        <Row label="Published">
          {r.published_at ? fmtDateTime(r.published_at) : <span className="text-muted-foreground/70">no publication date in source</span>}
        </Row>
        <Row label="Age"><Badge tone={f.tone}>{f.text}</Badge> <span className="text-xs text-muted-foreground">{r.published_at ? "since publication" : "since collection"}</span></Row>
        {r.date_verified !== null && (
          <Row label="Date verified">
            {r.date_verified ? <span className="inline-flex items-center gap-1 text-success"><CheckCircle2 className="size-3.5" /> Yes</span> : <span className="text-warning">No</span>}
          </Row>
        )}
        {typeof dateSource === "string" && <Row label="Date evidence"><code className="text-xs break-all">{dateSource}</code></Row>}
        <Row label="Collected">{fmtDateTime(r.collected_at)}</Row>
        {r.updated_at && <Row label="Last updated">{fmtDateTime(r.updated_at)}</Row>}
      </CardContent>
    </Card>
  );
}

function ProvenanceCard({ r }: { r: RecordDetail }) {
  const p = r.provenance;
  const ev = (p.evidence ?? {}) as Record<string, unknown>;
  const str = (v: unknown) => (typeof v === "string" || typeof v === "number" ? String(v) : null);
  return (
    <Card>
      <CardHeader className="pb-1"><CardTitle className="flex items-center gap-1.5"><FileSearch className="size-4" /> Source &amp; provenance</CardTitle></CardHeader>
      <CardContent className="divide-y divide-border/60">
        <Row label="Source">{r.source_name}</Row>
        <Row label="Source page">
          <a href={r.source_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">open <ExternalLink className="size-3" /></a>
        </Row>
        <Row label="Extraction">{r.extraction_method.replace("_", " ").toLowerCase()}</Row>
        <Row label="Validation"><Badge tone={r.validation_status === "VERIFIED" ? "success" : "warning"}>{r.validation_status.toLowerCase()}</Badge></Row>
        {str(p.confidence) && <Row label="Confidence"><span className="tabular">{str(p.confidence)}</span></Row>}
        <Row label="LLM used">{str(p.llmProvider) ?? <span className="text-muted-foreground">no</span>}</Row>
        {str(p.sourceRecordId) && <Row label="Source record ID"><code className="text-xs break-all">{str(p.sourceRecordId)}</code></Row>}
        {Object.keys(ev).length > 0 && (
          <div className="py-2">
            <div className="mb-1 text-xs text-muted-foreground">Recorded evidence</div>
            <dl className="flex flex-col gap-1">
              {Object.entries(ev).map(([k, v]) => (
                <div key={k} className="text-xs">
                  <dt className="font-medium">{fieldLabel(k)}</dt>
                  <dd className="break-words text-muted-foreground">{String(v)}</dd>
                </div>
              ))}
            </dl>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function NewsOrganisationsCard({ r }: { r: RecordDetail }) {
  const tags = r.organisation_tags;
  return (
    <Card data-testid="news-org-tags">
      <CardHeader className="pb-1">
        <div>
          <CardTitle className="flex items-center gap-1.5"><Building2 className="size-4" /> Organisation tags</CardTitle>
          <CardDescription>Evidence-validated: each name was accepted only with a verbatim quote from this article</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        {tags.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No organisation tags. No organisation named in this article passed evidence validation, so none is stored.
          </p>
        ) : (
          <ul className="-mx-2 flex flex-col">
            {tags.map((t) => (
              <li key={t.entity_id} className="flex flex-wrap items-center justify-between gap-1.5 rounded-md px-2 py-1.5 hover:bg-surface-2">
                <Link href={`${entityHref(t.entity_id, true)}${t.needs_review ? "?tab=review" : ""}`} className="min-w-0 truncate text-sm font-medium text-accent hover:underline" title={t.name}>
                  {t.name}
                </Link>
                <span className="flex items-center gap-1">
                  {t.mapped && t.match_method && <Badge>{t.match_method.toLowerCase()}</Badge>}
                  {t.needs_review ? (
                    <Tooltip content="The entity mapping for this tag is ambiguous and awaits human review">
                      <Badge tone="warning" tabIndex={0}><ShieldAlert className="size-3" /> pending review</Badge>
                    </Tooltip>
                  ) : !t.mapped ? (
                    <Tooltip content="Tag is stored on the article but no entity mapping row exists for it">
                      <Badge tabIndex={0}>unmapped</Badge>
                    </Tooltip>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function OrganisationCard({ r }: { r: RecordDetail }) {
  if (r.type === "NEWS") return <NewsOrganisationsCard r={r} />;
  const o = r.resolved_organisation;
  return (
    <Card>
      <CardHeader className="pb-1">
        <div>
          <CardTitle className="flex items-center gap-1.5"><Building2 className="size-4" /> Resolved organisation</CardTitle>
          <CardDescription>From the existing entity-resolution mapping for this record</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        {!o ? (
          <p className="text-sm text-muted-foreground">
            {r.type === "RESEARCH_PAPER"
              ? "Not linked: this record type carries no organisation mapping in the stored data."
              : "No entity mapping exists for this record."}
          </p>
        ) : (
          <div className="divide-y divide-border/60">
            <Row label="Canonical name">
              <Link href={`${entityHref(o.canonical_name)}${o.needs_review ? "?tab=review" : ""}`} className="font-medium text-accent hover:underline">{o.canonical_name}</Link>
            </Row>
            {o.match_method && <Row label="Match method"><Badge>{o.match_method.toLowerCase()}</Badge></Row>}
            {o.confidence !== null && <Row label="Confidence"><span className="tabular">{o.confidence}</span></Row>}
            <Row label="Review status">
              {o.needs_review ? (
                <Tooltip content="Ambiguous match kept separate until a human decides">
                  <Badge tone="warning" tabIndex={0}><ShieldAlert className="size-3" /> pending review</Badge>
                </Tooltip>
              ) : (
                <Badge tone="success">resolved</Badge>
              )}
            </Row>
            {o.needs_review && (
              <p className="pt-2 text-[11px] text-muted-foreground">
                This mapping awaits human review, so the record is not counted as linked to the organisation and has no related records.
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function RelatedCard({ r }: { r: RecordDetail }) {
  return (
    <Card>
      <CardHeader className="pb-1">
        <div>
          <CardTitle>Related records</CardTitle>
          <CardDescription>
            {r.type === "NEWS"
              ? "Other articles sharing at least one verified organisation tag"
              : r.resolved_organisation ? `Other records mapped to ${r.resolved_organisation.canonical_name} (verified mappings)` : "Records sharing this record's organisation"}
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        {r.related.length === 0 ? (
          <EmptyState className="py-4" title="No related records">
            {r.type === "NEWS"
              ? r.organisation_tags.length ? "No other stored article shares a verified organisation tag with this one." : "Related articles are found through organisation tags, and this article has none."
              : r.resolved_organisation ? "No other stored record is mapped to this organisation." : "Related records are found only through entity mappings, and this record has none."}
          </EmptyState>
        ) : (
          <ul className="-mx-2 flex flex-col">
            {r.related.map((x) => (
              <li key={x.id}>
                <Link href={recordHref(x.type, x.id)} className="flex flex-col gap-0.5 rounded-md px-2 py-1.5 hover:bg-surface-2">
                  <span className="flex items-center gap-1.5 text-sm">
                    <Badge tone="accent">{TYPE_LABEL[x.type]}</Badge>
                    <span className="min-w-0 truncate">{x.name ?? "Untitled"}</span>
                  </span>
                  <RecordFacts r={x} linkOrg={false} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
      <Info className="size-3.5" /> Field origin:
      {(Object.keys(PROV) as Provenance[]).map((p) => (
        <span key={p} className="inline-flex items-center gap-1"><ProvenanceBadge p={p} /></span>
      ))}
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-busy="true" aria-label="Loading record">
      <Skeleton className="h-4 w-24" />
      <Skeleton className="h-7 w-2/3" />
      <Skeleton className="h-4 w-1/3" />
      <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
        <Card className="flex flex-col gap-3 p-4">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-4 w-full" />)}</Card>
        <Card className="flex flex-col gap-3 p-4">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-4 w-full" />)}</Card>
      </div>
    </div>
  );
}

const LIST_TITLE: Record<TypeSlug, string> = { startups: "Startups", products: "Products", papers: "Research papers", jobs: "Jobs", news: "News" };

export function RecordDetailView({ slug, id }: { slug: TypeSlug; id: string }) {
  const num = /^\d+$/.test(id) ? Number(id) : NaN;
  const { data: r, isPending, isError, error, refetch } = useRecord(slug, num);
  const { get, set } = useUrlState();
  const tab = get("tab") === "history" ? "history" : "details";
  const back = (
    <Link href={`/${slug}`} className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
      <ArrowLeft className="size-4" /> {LIST_TITLE[slug]}
    </Link>
  );
  if (!Number.isInteger(num) || num <= 0 || (error instanceof ApiError && error.status === 404)) {
    return (
      <div className="mx-auto flex max-w-3xl flex-col gap-4">
        {back}
        <Card><EmptyState title="Record not found">No {LIST_TITLE[slug].toLowerCase()} record exists with id “{id}”.</EmptyState></Card>
      </div>
    );
  }
  if (isError) {
    return (
      <div className="mx-auto flex max-w-3xl flex-col gap-4">
        {back}
        <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card>
      </div>
    );
  }
  if (isPending) return <div className="mx-auto max-w-[1400px]">{back}<div className="mt-4"><DetailSkeleton /></div></div>;

  const primary = r.url && r.url !== r.source_url ? r.url : null;
  const missing = r.fields.filter((f) => f.provenance === "missing").length;
  return (
    <article className="mx-auto flex max-w-[1400px] flex-col gap-4">
      {back}
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="accent">{TYPE_LABEL[r.type]}</Badge>
          <RecordBadges r={r} />
          <span className="text-xs text-muted-foreground">#{r.id}</span>
        </div>
        <h1 className="text-xl font-semibold tracking-tight text-balance sm:text-2xl">{r.name ?? "Untitled"}</h1>
        <RecordFacts r={r} />
        <div className="flex flex-wrap gap-2 pt-1">
          {primary && (
            <Button asChild size="sm">
              <a href={primary} target="_blank" rel="noopener noreferrer">
                {r.type === "RESEARCH_PAPER" ? "Open paper" : r.type === "JOB" ? "View posting" : r.type === "NEWS" ? "Read article" : "Visit website"} <ExternalLink />
              </a>
            </Button>
          )}
          <Button asChild variant="outline" size="sm">
            <a href={r.source_url} target="_blank" rel="noopener noreferrer">Source: {r.source_name} <ExternalLink /></a>
          </Button>
          {r.type === "RESEARCH_PAPER" && typeof r.fields.find((f) => f.key === "github_url")?.value === "string" && (
            <Button asChild variant="outline" size="sm">
              <a href={r.fields.find((f) => f.key === "github_url")?.value as string} target="_blank" rel="noopener noreferrer">
                GitHub{r.github_stars !== null ? ` · ${fmtNumber(r.github_stars)} ★` : ""} <ExternalLink />
              </a>
            </Button>
          )}
        </div>
      </header>

      <div role="tablist" aria-label="Record sections" className="-mx-1 flex gap-1 overflow-x-auto border-b border-border px-1">
        {([["details", "Details"], ["history", "History"]] as const).map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => set({ tab: k === "details" ? null : k })}
            className={cn("-mb-px shrink-0 border-b-2 px-3 py-2 text-sm whitespace-nowrap",
              tab === k ? "border-accent text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}>
            {label}
          </button>
        ))}
      </div>

      {tab === "history" ? <div role="tabpanel"><RecordHistoryPanel id={r.id} /></div> : (
      <div role="tabpanel" className="grid gap-4 lg:grid-cols-[1fr_340px]">
        <div className="flex min-w-0 flex-col gap-4">
          <Body r={r} />
          <Card>
            <CardHeader>
              <div>
                <CardTitle>All stored fields</CardTitle>
                <CardDescription>
                  {r.fields.length} fields{missing ? ` · ${missing} not available in the source` : ""} · values are shown exactly as stored
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <Legend />
              <FieldsTable fields={r.fields} bodyKeys={bodyKeysFor(r)} />
            </CardContent>
          </Card>
        </div>
        <aside className="flex min-w-0 flex-col gap-4">
          <FreshnessCard r={r} />
          <ProvenanceCard r={r} />
          <OrganisationCard r={r} />
          <RelatedCard r={r} />
        </aside>
      </div>
      )}
      <p className="text-[11px] text-muted-foreground">Record collected {fmtRelative(r.collected_at)} · {fmtDate(r.collected_at)}</p>
    </article>
  );
}
