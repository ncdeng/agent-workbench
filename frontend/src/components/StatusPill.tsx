type Tone = 'good' | 'warn' | 'bad' | 'neutral';

const dotClass: Record<Tone, string> = {
  good: 'bg-success',
  warn: 'bg-warning',
  bad: 'bg-danger',
  neutral: 'bg-text-muted',
};

const textClass: Record<Tone, string> = {
  good: 'text-success',
  warn: 'text-warning',
  bad: 'text-danger',
  neutral: 'text-text-secondary',
};

export function StatusPill({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: Tone }) {
  return (
    <span
      className={`inline-flex items-center gap-2 min-h-[30px] px-2.5 py-1 rounded-full text-[0.82rem] font-semibold border border-border bg-elevated ${textClass[tone]}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${dotClass[tone]}`} />
      {children}
    </span>
  );
}
