"use client";

import { ChevronLeft, ChevronRight, Loader2, Search, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { fmtNumber } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export const inputCls =
  "h-9 w-full rounded-md border border-border bg-surface-2/60 px-2.5 text-sm placeholder:text-muted-foreground/70 focus:border-border-strong focus:outline-none";

/** Debounced keyword input bound to the URL (`q`). */
export function SearchInput({ value, onChange, placeholder, busy }: { value: string; onChange: (v: string) => void; placeholder: string; busy?: boolean }) {
  const [text, setText] = useState(value);
  const [prev, setPrev] = useState(value);
  // external URL change (back button, reset) -> reflect in the box (React "derive state from props" pattern)
  if (value !== prev) {
    setPrev(value);
    if (text.trim() !== value) setText(value);
  }
  useEffect(() => {
    if (text.trim() === value) return;
    const t = setTimeout(() => onChange(text.trim()), 350);
    return () => clearTimeout(t);
  }, [text, value, onChange]);
  return (
    <div className="relative w-full">
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
      <input
        type="search"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        maxLength={200}
        className={cn(inputCls, "pr-8 pl-8")}
      />
      <span className="absolute top-1/2 right-2 -translate-y-1/2">
        {busy ? (
          <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-label="Loading" />
        ) : text ? (
          <button type="button" aria-label="Clear search" onClick={() => setText("")} className="text-muted-foreground hover:text-foreground">
            <X className="size-3.5" />
          </button>
        ) : null}
      </span>
    </div>
  );
}

export function Field({ label, htmlFor, children, hint }: { label: string; htmlFor: string; children: ReactNode; hint?: string }) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={htmlFor} className="text-xs font-medium text-muted-foreground">{label}</label>
      {children}
      {hint && <p className="text-[11px] text-muted-foreground/80">{hint}</p>}
    </div>
  );
}

export function Select({ id, value, onChange, options, placeholder }: { id: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; placeholder?: string }) {
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={cn(inputCls, "cursor-pointer pr-7")}>
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}

/** Text filter that commits on blur / Enter (no request per keystroke). */
export function TextFilter({ id, value, onCommit, placeholder, list }: { id: string; value: string; onCommit: (v: string) => void; placeholder?: string; list?: string }) {
  const [text, setText] = useState(value);
  const [prev, setPrev] = useState(value);
  if (value !== prev) {
    setPrev(value);
    setText(value);
  }
  const commit = () => text.trim() !== value && onCommit(text.trim());
  return (
    <input
      id={id}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && commit()}
      onInput={(e) => {
        // picking a datalist suggestion should apply immediately (no blur needed)
        const v = (e.target as HTMLInputElement).value;
        const kind = (e.nativeEvent as InputEvent).inputType;
        if (list && (kind === undefined || kind === "insertReplacementText") && v.trim() !== value) onCommit(v.trim());
      }}
      list={list}
      placeholder={placeholder}
      maxLength={200}
      className={inputCls}
    />
  );
}

export const PAGE_SIZES = [24, 48, 96];

export function Pagination({ page, pageSize, total, totalPages, onPage, onPageSize }: { page: number; pageSize: number; total: number; totalPages: number; onPage: (p: number) => void; onPageSize: (s: number) => void }) {
  if (total === 0) return null;
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  const pages = new Set([1, totalPages, page - 1, page, page + 1].filter((p) => p >= 1 && p <= totalPages));
  const list = [...pages].sort((a, b) => a - b);
  return (
    <nav aria-label="Pagination" className="flex flex-wrap items-center justify-between gap-3 text-sm">
      <span className="tabular text-xs text-muted-foreground">
        {from > total ? "No results on this page" : `${fmtNumber(from)}–${fmtNumber(to)} of ${fmtNumber(total)}`}
      </span>
      <div className="flex items-center gap-1">
        <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page">
          <ChevronLeft />
        </Button>
        {list.map((p, i) => (
          <span key={p} className="flex items-center">
            {i > 0 && p - (list[i - 1] ?? p) > 1 && <span className="px-1 text-muted-foreground">…</span>}
            <Button variant={p === page ? "default" : "ghost"} size="sm" onClick={() => onPage(p)} aria-current={p === page ? "page" : undefined} className="tabular min-w-8">
              {fmtNumber(p)}
            </Button>
          </span>
        ))}
        <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => onPage(page + 1)} aria-label="Next page">
          <ChevronRight />
        </Button>
      </div>
      <label className="flex items-center gap-2 text-xs text-muted-foreground">
        Per page
        <select value={pageSize} onChange={(e) => onPageSize(Number(e.target.value))} className={cn(inputCls, "h-8 w-auto cursor-pointer")}>
          {PAGE_SIZES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </label>
    </nav>
  );
}
