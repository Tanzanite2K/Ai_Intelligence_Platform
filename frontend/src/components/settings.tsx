"use client";

import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";
import { Laptop, Moon, Sun } from "lucide-react";
import { API_BASE_URL } from "@/lib/api/client";
import { useHealth } from "@/lib/api/hooks";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const useMounted = () => useSyncExternalStore(() => () => {}, () => true, () => false);

export function SettingsPage() {
  const { theme, setTheme } = useTheme();
  const mounted = useMounted();
  const health = useHealth();
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">Preferences stored in this browser, and the connection this app uses.</p>
      </div>
      <Card>
        <CardHeader><div><CardTitle>Appearance</CardTitle><CardDescription>Saved in this browser only</CardDescription></div></CardHeader>
        <CardContent>
          <div role="radiogroup" aria-label="Theme" className="grid grid-cols-3 gap-2 sm:max-w-md">
            {([["dark", "Dark", Moon], ["light", "Light", Sun], ["system", "System", Laptop]] as const).map(([v, l, I]) => (
              <button key={v} type="button" role="radio" aria-checked={mounted && theme === v} onClick={() => setTheme(v)}
                className={cn("flex flex-col items-center gap-1.5 rounded-md border px-3 py-3 text-sm", mounted && theme === v ? "border-accent/60 bg-accent-soft text-accent" : "border-border hover:bg-surface-2")}>
                <I className="size-4" /> {l}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><div><CardTitle>API connection</CardTitle><CardDescription>Set with NEXT_PUBLIC_API_BASE_URL at build/start time</CardDescription></div></CardHeader>
        <CardContent className="flex flex-col gap-2 text-sm">
          <div className="flex items-center justify-between gap-2"><span className="text-muted-foreground">Base URL</span><code className="text-xs break-all">{API_BASE_URL}</code></div>
          <div className="flex items-center justify-between gap-2"><span className="text-muted-foreground">Status</span>
            {health.isPending ? <Badge>checking</Badge> : health.isError ? <Badge tone="danger">unreachable</Badge> : <Badge tone="success">online</Badge>}
          </div>
          <p className="text-[11px] text-muted-foreground">
            The web app only reads data. It holds no provider keys, and administrative operations (crawls, entity decisions) are not available from the browser.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
