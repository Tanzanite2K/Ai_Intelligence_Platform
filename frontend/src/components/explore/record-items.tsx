"use client";

import Link from "next/link";
import { Building2, Calendar, ExternalLink, Globe, MapPin, Star, Tag } from "lucide-react";
import type { RecordSummary } from "@/lib/api/schemas";
import { fmtDate, fmtNumber, fmtRelative, TYPE_LABEL } from "@/lib/format";
import { recordHref } from "@/lib/records";
import { entityHref } from "@/lib/entities";
import { OrgLink } from "@/components/entities/common";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/tooltip";

function hostname(url: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

function Meta({ icon: Icon, children }: { icon: typeof Calendar; children: React.ReactNode }) {
  return (
    <span className="inline-flex min-w-0 items-center gap-1">
      <Icon className="size-3 shrink-0" />
      <span className="truncate">{children}</span>
    </span>
  );
}

/** Type-specific facts shown under a result — only fields that exist on the record. */
export function RecordFacts({ r, linkOrg = true }: { r: RecordSummary; linkOrg?: boolean }) {
  const parts: React.ReactNode[] = [];
  const org = (name: string) => (linkOrg ? <OrgLink name={name} /> : name);
  if (r.type === "RESEARCH_PAPER") {
    if (r.published_at) parts.push(<Meta key="d" icon={Calendar}>{fmtDate(r.published_at)}</Meta>);
    parts.push(
      r.github_stars !== null ? (
        <Meta key="s" icon={Star}>{fmtNumber(r.github_stars)} stars</Meta>
      ) : (
        <span key="s" className="text-muted-foreground/70">no repository</span>
      ),
    );
    if (r.authors.length) parts.push(<span key="a" className="truncate">{r.authors.slice(0, 3).join(", ")}{r.authors.length > 3 ? ` +${r.authors.length - 3}` : ""}</span>);
  } else if (r.type === "JOB") {
    if (r.organisation) parts.push(<Meta key="o" icon={Building2}>{org(r.organisation)}</Meta>);
    if (r.location) parts.push(<Meta key="l" icon={MapPin}>{r.location}</Meta>);
    if (r.published_at) parts.push(<Meta key="d" icon={Calendar}>{fmtRelative(r.published_at)}</Meta>);
  } else if (r.type === "NEWS") {
    if (r.published_at) parts.push(<Meta key="d" icon={Calendar}>{fmtRelative(r.published_at)}</Meta>);
  } else if (r.type === "STARTUP") {
    if (r.location) parts.push(<Meta key="l" icon={MapPin}>{r.location}</Meta>);
    const h = hostname(r.url);
    if (h) parts.push(<Meta key="w" icon={Globe}>{h}</Meta>);
  } else if (r.type === "PRODUCT") {
    if (r.organisation) parts.push(<Meta key="o" icon={Building2}>{org(r.organisation)}</Meta>);
  }
  parts.push(<span key="src" className="truncate">{r.source_name}</span>);
  return <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">{parts}</div>;
}

export function RecordBadges({ r, showType = false }: { r: RecordSummary; showType?: boolean }) {
  return (
    <span className="flex flex-wrap items-center gap-1">
      {showType && <Badge tone="accent">{TYPE_LABEL[r.type]}</Badge>}
      {r.type === "JOB" && r.is_remote === true && <Badge tone="info">Remote</Badge>}
      {r.type === "JOB" && r.role_family && <Badge>{r.role_family}</Badge>}
      {r.type === "PRODUCT" && r.pricing_model && (
        <Tooltip content="Pricing model as stored. Open the record to see where it came from and its evidence (FREE is derived from an open-weight licence tag on the Hub).">
          <Badge tone="success">{r.pricing_model}</Badge>
        </Tooltip>
      )}
      {r.type === "PRODUCT" && r.description && r.description.length <= 40 && <Badge>{r.description}</Badge>}
      {r.validation_status !== "VERIFIED" && (
        <Tooltip content="Some optional fields were dropped at ingestion because no source evidence supported them">
          <Badge tone="warning">{r.validation_status.toLowerCase()}</Badge>
        </Tooltip>
      )}
    </span>
  );
}

/** Long-form description; product descriptions that are only a task tag are shown as a badge instead. */
function description(r: RecordSummary): string | null {
  if (r.type === "PRODUCT" && r.description && r.description.length <= 40) return null;
  return r.description;
}

export function UntaggedBadge() {
  return (
    <Tooltip content="No organisation in this article passed evidence validation (every name needs its own verbatim quote), so it has no organisation tags.">
      <Badge tabIndex={0} data-testid="untagged">No organisation tags</Badge>
    </Tooltip>
  );
}

const MAX_TAGS = 6;

/** Stored, evidence-validated organisation tags of a news article; each links to the organisation profile. */
export function NewsTags({ tags, link = true }: { tags: string[]; link?: boolean }) {
  if (tags.length === 0) return null;
  const shown = tags.slice(0, MAX_TAGS);
  const rest = tags.slice(MAX_TAGS);
  return (
    <ul className="relative z-10 flex flex-wrap items-center gap-1" aria-label="Organisation tags">
      {shown.map((t) => (
        <li key={t}>
          {link ? (
            <Link href={entityHref(t)} className="inline-flex max-w-[220px] items-center rounded-md bg-accent-soft px-1.5 py-0.5 text-[11px] font-medium text-accent hover:underline" title={t}>
              <span className="truncate">{t}</span>
            </Link>
          ) : (
            <Badge tone="accent">{t}</Badge>
          )}
        </li>
      ))}
      {rest.length > 0 && (
        <li>
          <Tooltip content={rest.join(", ")}>
            <Badge tabIndex={0}>+{rest.length} more</Badge>
          </Tooltip>
        </li>
      )}
    </ul>
  );
}

export function RecordRow({ r, showType = false }: { r: RecordSummary; showType?: boolean }) {
  const d = description(r);
  return (
    <li className="group relative flex flex-col gap-1 border-b border-border/60 px-4 py-3 last:border-0 hover:bg-surface-2/50">
      <div className="flex items-start gap-2">
        <Link href={recordHref(r.type, r.id)} className="min-w-0 flex-1 text-sm font-medium after:absolute after:inset-0 hover:text-accent focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:outline-ring">
          <span className="line-clamp-2 break-words">{r.name ?? "Untitled"}</span>
        </Link>
        <RecordBadges r={r} showType={showType} />
      </div>
      {d && <p className="line-clamp-2 text-xs break-words text-muted-foreground">{d}</p>}
      {r.type === "NEWS" && (r.organizations.length > 0 ? <NewsTags tags={r.organizations} /> : <div className="relative z-10 flex"><UntaggedBadge /></div>)}
      {r.type === "STARTUP" && r.tags.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {r.tags.slice(0, 5).map((t) => <Badge key={t}><Tag className="size-2.5" />{t}</Badge>)}
        </div>
      )}
      <RecordFacts r={r} />
    </li>
  );
}

export function StartupCard({ r }: { r: RecordSummary }) {
  const h = hostname(r.url);
  return (
    <Card className="group relative flex flex-col gap-2 p-4 hover:border-border-strong">
      <div className="flex items-start justify-between gap-2">
        <Link href={recordHref(r.type, r.id)} className="text-sm font-semibold after:absolute after:inset-0 hover:text-accent">
          {r.name ?? "Untitled"}
        </Link>
        <Badge>{r.source_name === "Y Combinator" ? "YC" : r.source_name}</Badge>
      </div>
      {r.description ? <p className="line-clamp-3 text-xs text-muted-foreground">{r.description}</p> : <p className="text-xs text-muted-foreground/60 italic">No description in source</p>}
      {r.tags.length > 0 && (
        <div className="flex flex-wrap gap-1">{r.tags.slice(0, 4).map((t) => <Badge key={t}>{t}</Badge>)}</div>
      )}
      <div className="mt-auto flex flex-wrap gap-x-3 gap-y-0.5 pt-1 text-xs text-muted-foreground">
        {r.location && <Meta icon={MapPin}>{r.location}</Meta>}
        {h && <Meta icon={Globe}>{h}</Meta>}
      </div>
    </Card>
  );
}

export function ExternalLinkButton({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
      {children} <ExternalLink className="size-3" />
    </a>
  );
}
