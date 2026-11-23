export function Logo({ collapsed = false }: { collapsed?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <svg viewBox="0 0 32 32" className="size-7 shrink-0" aria-hidden>
        <rect width="32" height="32" rx="8" className="fill-accent-soft" />
        <path d="M9 22 16 8l7 14" className="stroke-accent" strokeWidth="2.4" fill="none" strokeLinejoin="round" strokeLinecap="round" />
        <path d="M11.8 17h8.4" className="stroke-accent" strokeWidth="2.4" strokeLinecap="round" />
        <circle cx="16" cy="8" r="1.9" className="fill-accent" />
      </svg>
      {!collapsed && (
        <div className="leading-tight">
          <div className="text-sm font-semibold tracking-tight">FrontierAtlas</div>
          <div className="text-[11px] text-muted-foreground">AI Intelligence</div>
        </div>
      )}
    </div>
  );
}
