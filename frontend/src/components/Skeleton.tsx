export function Skeleton({ className = '' }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded-md bg-text-muted/15 ${className}`}
      aria-hidden="true"
    />
  );
}

export function SkeletonMetricCard() {
  return (
    <div className="min-h-[88px] p-4 rounded-xl border border-border bg-surface space-y-3">
      <Skeleton className="h-3 w-16" />
      <Skeleton className="h-7 w-24" />
      <Skeleton className="h-3 w-28" />
    </div>
  );
}

export function SkeletonChart({ height = 336 }: { height?: number }) {
  return (
    <div
      className="flex flex-col justify-center items-center rounded-xl border border-border bg-surface"
      style={{ height }}
    >
      <Skeleton className="w-full h-full" />
    </div>
  );
}
