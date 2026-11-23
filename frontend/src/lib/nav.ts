import {
  Activity, AlertOctagon, BarChart3, Boxes, Briefcase, Building2, FileText, Gauge, History, LayoutDashboard, Network, Newspaper, Radar,
  GitCompareArrows, ListChecks, Rocket, ScrollText, Search, Settings, ShieldCheck, Waypoints, type LucideIcon,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  /** Phase in which the page is built. Items for later phases are shown disabled — never as dead links. */
  phase: number;
  /** Active only on this exact path (for section roots whose sub-pages have their own items). */
  exact?: boolean;
}

export function isActive(item: NavItem, pathname: string): boolean {
  if (pathname === item.href) return true;
  return !item.exact && item.href !== "/" && pathname.startsWith(`${item.href}/`);
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const CURRENT_PHASE = 7;

export const NAV: NavGroup[] = [
  {
    label: "Main",
    items: [
      { label: "Overview", href: "/", icon: LayoutDashboard, phase: 2 },
      { label: "Explore Intelligence", href: "/explore", icon: Search, phase: 3 },
      { label: "Startups", href: "/startups", icon: Rocket, phase: 3 },
      { label: "Products", href: "/products", icon: Boxes, phase: 3 },
      { label: "Research Papers", href: "/papers", icon: FileText, phase: 3 },
      { label: "Jobs", href: "/jobs", icon: Briefcase, phase: 3 },
      { label: "News", href: "/news", icon: Newspaper, phase: 3 },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { label: "Organisations", href: "/organisations", icon: Building2, phase: 4 },
      { label: "Entity Relationships", href: "/relationships", icon: Network, phase: 4 },
      { label: "Data Quality", href: "/data-quality", icon: ShieldCheck, phase: 4 },
    ],
  },
  {
    label: "Frontier Intelligence",
    items: [
      { label: "Intelligence Brief", href: "/intelligence", icon: ScrollText, phase: 6, exact: true },
      { label: "Trend Radar", href: "/intelligence/trends", icon: Radar, phase: 6 },
      { label: "Market Signals", href: "/intelligence/signals", icon: BarChart3, phase: 6 },
      { label: "Innovation Map", href: "/intelligence/map", icon: Waypoints, phase: 6 },
      { label: "Historical Trends", href: "/intelligence/history", icon: History, phase: 6 },
      { label: "Change Feed", href: "/intelligence/changes", icon: ListChecks, phase: 7 },
      { label: "Historical Changes", href: "/intelligence/comparisons", icon: GitCompareArrows, phase: 7 },
    ],
  },
  {
    label: "Operations",
    items: [
      { label: "Ingestion Monitor", href: "/operations/ingestion", icon: Activity, phase: 5 },
      { label: "Source Health", href: "/operations/sources", icon: Gauge, phase: 5 },
      { label: "Source Reliability", href: "/operations/reliability", icon: Activity, phase: 7 },
      { label: "Dead Letter Queue", href: "/operations/dead-letters", icon: AlertOctagon, phase: 5 },
    ],
  },
  { label: "Settings", items: [{ label: "Settings", href: "/settings", icon: Settings, phase: 5 }] },
];

export function isAvailable(item: NavItem): boolean {
  return item.phase <= CURRENT_PHASE;
}

export function breadcrumbFor(pathname: string): { label: string; href?: string }[] {
  if (pathname.startsWith("/intelligence/trends/")) return [{ label: "Frontier Intelligence" }, { label: "Trend Radar", href: "/intelligence/trends" }, { label: "Topic" }];
  if (pathname.startsWith("/organisations/")) return [{ label: "Intelligence" }, { label: "Organisations", href: "/organisations" }, { label: "Profile" }];
  const detail = /^\/(startups|products|papers|jobs|news)\/([^/]+)$/.exec(pathname);
  if (detail) {
    const item = NAV[0]?.items.find((i) => i.href === `/${detail[1]}`);
    return [{ label: "Main" }, { label: item?.label ?? "", href: `/${detail[1]}` }, { label: `#${detail[2]}` }];
  }
  let best: { g: NavGroup; i: NavItem } | null = null;  // longest matching href wins (/intelligence vs /intelligence/trends)
  for (const g of NAV) {
    for (const i of g.items) {
      if ((i.href === pathname || (pathname.startsWith(`${i.href}/`) && i.href !== "/")) && (!best || i.href.length > best.i.href.length)) best = { g, i };
    }
  }
  if (best) return pathname === "/" ? [{ label: "Overview" }] : [{ label: best.g.label }, { label: best.i.label }];
  return [{ label: "Overview", href: "/" }];
}
