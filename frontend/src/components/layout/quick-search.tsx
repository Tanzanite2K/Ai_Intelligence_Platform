"use client";

import { ArrowRight, Loader2, Search, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { recordHref } from "@/lib/records";
import { entityHref } from "@/lib/entities";
import { useQuickSearch } from "@/lib/api/hooks";
import { Badge } from "@/components/ui/badge";
import { TYPE_LABEL, fmtNumber } from "@/lib/format";

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/**
 * Global quick search over /api/v1/search (debounced, 6 results).
 * Phase 2: results open the record's original source; in-app detail pages arrive in Phase 3.
 */
export function QuickSearch() {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const q = useDebounced(text.trim(), 300);
  const { data, isFetching, isError, error } = useQuickSearch(q);
  const inputRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const router = useRouter();
  const go = (href: string) => {
    setOpen(false);
    router.push(href);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
      }
    };
    const onClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onClick);
    };
  }, []);

  const show = open && q.length >= 2;
  return (
    <div ref={boxRef} className="relative w-full max-w-md">
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
      <input
        ref={inputRef}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
          if (e.key === "Enter" && text.trim()) go(`/explore?q=${encodeURIComponent(text.trim())}`);
        }}
        placeholder="Search startups, products, papers, jobs, news…"
        aria-label="Search all intelligence"
        aria-expanded={show}
        aria-controls={listId}
        role="combobox"
        maxLength={200}
        className="h-9 w-full rounded-md border border-border bg-surface-2/60 pr-16 pl-8 text-sm placeholder:text-muted-foreground/70 focus:border-border-strong focus:outline-none"
      />
      <div className="absolute top-1/2 right-2 flex -translate-y-1/2 items-center gap-1">
        {isFetching && <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-label="Searching" />}
        {text ? (
          <button type="button" aria-label="Clear search" onClick={() => setText("")} className="rounded p-0.5 text-muted-foreground hover:text-foreground">
            <X className="size-3.5" />
          </button>
        ) : (
          <kbd className="hidden rounded border border-border px-1 text-[10px] text-muted-foreground md:inline">Ctrl K</kbd>
        )}
      </div>

      {show && (
        <div id={listId} role="listbox" className="absolute top-11 right-0 left-0 z-50 overflow-hidden rounded-lg border border-border-strong bg-surface shadow-2xl">
          {isError ? (
            <p className="p-3 text-sm text-danger">{error instanceof Error ? error.message : "Search failed"}</p>
          ) : !data ? (
            <p className="p-3 text-sm text-muted-foreground">Searching…</p>
          ) : data.meta.total === 0 && data.organisations.length === 0 ? (
            <p className="p-3 text-sm text-muted-foreground">No records match “{q}”.</p>
          ) : (
            <>
              <div className="flex flex-wrap gap-1.5 border-b border-border px-3 py-2 text-[11px] text-muted-foreground">
                <span className="tabular">{fmtNumber(data.meta.total)} records</span>
                {Object.entries(data.counts_by_type).map(([t, n]) => (
                  <Badge key={t}>{TYPE_LABEL[t] ?? t} {fmtNumber(n)}</Badge>
                ))}
              </div>
              <ul className="max-h-80 overflow-y-auto py-1">
                {data.data.map((r) => (
                  <li key={r.id}>
                    <Link
                      href={recordHref(r.type, r.id)}
                      onClick={() => setOpen(false)}
                      className="flex items-start gap-2 px-3 py-2 hover:bg-surface-2 focus:bg-surface-2 focus:outline-none"
                    >
                      <Badge tone="accent" className="mt-0.5 w-14 justify-center">{TYPE_LABEL[r.type]}</Badge>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm">{r.name ?? "Untitled"}</span>
                        <span className="block truncate text-xs text-muted-foreground">
                          {[r.organisation, r.source_name].filter(Boolean).join(" · ")}
                        </span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
              {data.organisations.length > 0 && (
                <div className="border-t border-border px-3 py-2">
                  <div className="mb-1 text-[11px] text-muted-foreground">Matching organisations</div>
                  <div className="flex flex-wrap gap-1.5">
                    {data.organisations.map((o) => (
                      <Link key={o.id} href={entityHref(o.id, true)} onClick={() => setOpen(false)}>
                        <Badge tone={o.needs_review ? "warning" : "neutral"} className="hover:text-foreground">
                          {o.canonical_name}
                          {o.needs_review && " · review"}
                        </Badge>
                      </Link>
                    ))}
                  </div>
                </div>
              )}
              <Link
                href={`/explore?q=${encodeURIComponent(q)}`}
                onClick={() => setOpen(false)}
                className="flex items-center justify-between border-t border-border px-3 py-2 text-xs text-accent hover:bg-surface-2"
              >
                See all {fmtNumber(data.meta.total)} results <ArrowRight className="size-3.5" />
              </Link>
            </>
          )}
        </div>
      )}
    </div>
  );
}
