const nf = new Intl.NumberFormat("en-US");
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

export const fmtNumber = (n: number | null | undefined): string => (n === null || n === undefined ? "—" : nf.format(n));
export const fmtCompact = (n: number): string => (Math.abs(n) >= 10_000 ? compact.format(n) : nf.format(n));

export function fmtDate(iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = { dateStyle: "medium" }): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : new Intl.DateTimeFormat("en-GB", opts).format(d);
}

export function fmtDateTime(iso: string | null | undefined): string {
  return fmtDate(iso, { dateStyle: "medium", timeStyle: "short" });
}

/** "3 h ago" style relative time against the current clock. */
export function fmtRelative(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const s = Math.round((now - t) / 1000);
  const abs = Math.abs(s);
  const fut = s < 0;
  const unit = abs < 60 ? `${abs}s` : abs < 3600 ? `${Math.round(abs / 60)} min` : abs < 86_400 ? `${Math.round(abs / 3600)} h` : `${Math.round(abs / 86_400)} d`;
  return fut ? `in ${unit}` : `${unit} ago`;
}

export const TYPE_LABEL: Record<string, string> = {
  startups: "Startups",
  products: "Products",
  papers: "Research papers",
  jobs: "Jobs",
  news: "News",
  STARTUP: "Startup",
  PRODUCT: "Product",
  RESEARCH_PAPER: "Paper",
  JOB: "Job",
  NEWS: "News",
};

export const TYPE_ORDER = ["startups", "products", "papers", "jobs", "news"] as const;
