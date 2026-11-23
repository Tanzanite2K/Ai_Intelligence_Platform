import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-none whitespace-nowrap", {
  variants: {
    tone: {
      neutral: "border-border bg-surface-2 text-muted-foreground",
      accent: "border-transparent bg-accent-soft text-accent",
      success: "border-transparent bg-success/12 text-success",
      warning: "border-transparent bg-warning/12 text-warning",
      danger: "border-transparent bg-danger/12 text-danger",
      info: "border-transparent bg-info/12 text-info",
    },
  },
  defaultVariants: { tone: "neutral" },
});

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
