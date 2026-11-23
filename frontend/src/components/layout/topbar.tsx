"use client";

import { ChevronRight, Menu } from "lucide-react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { useState } from "react";
import { breadcrumbFor } from "@/lib/nav";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";
import { ApiStatus } from "./api-status";
import { Logo } from "./logo";
import { QuickSearch } from "./quick-search";
import { SidebarNav } from "./sidebar";
import { ThemeToggle } from "./theme-toggle";

export function Topbar() {
  const pathname = usePathname();
  const crumbs = breadcrumbFor(pathname);
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-background/85 px-3 backdrop-blur sm:px-5">
      <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
        <SheetTrigger asChild>
          <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open navigation">
            <Menu />
          </Button>
        </SheetTrigger>
        <SheetContent title="Navigation">
          <div className="flex h-14 items-center border-b border-border px-4">
            <Logo />
          </div>
          <div className="h-[calc(100dvh-3.5rem)] overflow-y-auto">
            <SidebarNav onNavigate={() => setMenuOpen(false)} />
          </div>
        </SheetContent>
      </Sheet>

      <nav aria-label="Breadcrumb" className="hidden min-w-0 items-center gap-1 text-sm md:flex">
        {crumbs.map((c, i) => (
          <span key={`${c.label}-${i}`} className="flex items-center gap-1">
            {i > 0 && <ChevronRight className="size-3.5 text-muted-foreground" />}
            {c.href ? (
              <Link href={c.href} className="text-muted-foreground hover:text-foreground">{c.label}</Link>
            ) : (
              <span className={i === crumbs.length - 1 ? "font-medium" : "text-muted-foreground"}>{c.label}</span>
            )}
          </span>
        ))}
      </nav>

      <div className="flex flex-1 justify-end md:justify-center">
        <QuickSearch />
      </div>
      <div className="flex items-center gap-1.5">
        <ApiStatus />
        <ThemeToggle />
      </div>
    </header>
  );
}
