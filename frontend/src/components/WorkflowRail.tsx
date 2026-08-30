export function WorkflowRail({ activeRound, steps }: { activeRound: number; steps: string[] }) {
  const completed = Math.min(activeRound, steps.length - 1);
  const progressPct = steps.length <= 1 ? 0 : (completed / (steps.length - 1)) * 100;

  return (
    <div className="surface p-3 relative page-section liquid-sheen">
      {/* Connecting line behind the step badges */}
      <div className="absolute top-7 left-4 right-4 h-0.5 -translate-y-1/2 z-0">
        <div className="absolute inset-0 bg-border rounded-full" />
        <div
          className="absolute top-0 left-0 bottom-0 bg-primary rounded-full transition-all duration-500"
          style={{ width: `${progressPct}%` }}
        />
      </div>

      <div className="relative z-10 grid grid-cols-5 gap-2.5">
        {steps.map((item, i) => {
          const done = i <= completed;
          return (
            <div
              key={item}
              className={`relative min-h-[80px] p-3.5 rounded-[12px] border transition-all duration-200 bg-surface ${
                done
                  ? 'border-primary/25'
                  : 'border-border'
              }`}
            >
              <span
                className={`workflow-step-index inline-flex items-center justify-center w-7 h-7 text-[0.72rem] font-mono font-semibold border transition-all duration-200 ${
                  done
                    ? 'is-done text-white bg-primary border-primary rounded-lg'
                    : 'is-pending text-text-muted border-border bg-elevated rounded-lg'
                }`}
              >
                {String(i + 1).padStart(2, '0')}
              </span>
              <strong
                className={`block mt-3 text-[0.92rem] leading-tight ${
                  done ? 'text-text font-semibold' : 'text-text-secondary'
                }`}
              >
                {item}
              </strong>
            </div>
          );
        })}
      </div>
    </div>
  );
}
