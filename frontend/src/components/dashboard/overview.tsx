"use client";

import dynamic from "next/dynamic";
import { RefreshCw } from "lucide-react";
import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { KpiGrid } from "./kpis";
import { RecentList, RecentRuns, SourceHealthOverview } from "./lists";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ChartSkeleton } from "@/components/common/states";

// Recharts is client-only and heavy: lazy-load the chart bundle.
const loading = () => (
  <Card className="p-4">
    <ChartSkeleton />
  </Card>
);
const RecordsByCategory = dynamic(() => import("./charts").then((m) => m.RecordsByCategory), { ssr: false, loading });
const IngestionOverTime = dynamic(() => import("./charts").then((m) => m.IngestionOverTime), { ssr: false, loading });
const SourceContributionChart = dynamic(() => import("./charts").then((m) => m.SourceContributionChart), { ssr: false, loading });
const FreshnessChart = dynamic(() => import("./charts").then((m) => m.FreshnessChart), { ssr: false, loading });

export function OverviewDashboard() {
  const qc = useQueryClient();
  const fetching = useIsFetching({ queryKey: ["dashboard"] }) > 0;
  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Overview</h1>
          <p className="text-sm text-muted-foreground">What the platform has collected, how fresh it is, and how ingestion is running.</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void qc.invalidateQueries({ queryKey: ["dashboard"] })} disabled={fetching}>
          <RefreshCw className={fetching ? "animate-spin" : undefined} /> Refresh
        </Button>
      </div>

      <KpiGrid />

      <div className="grid gap-4 lg:grid-cols-3">
        <RecordsByCategory />
        <IngestionOverTime />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <SourceContributionChart />
        <FreshnessChart />
        <SourceHealthOverview />
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <RecentList kind="startups" />
        <RecentList kind="papers" />
        <RecentList kind="news" />
        <RecentList kind="jobs" />
      </div>

      <RecentRuns />
    </div>
  );
}
