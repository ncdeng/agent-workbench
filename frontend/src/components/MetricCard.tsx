type Tone = 'good' | 'warn' | 'bad' | 'neutral';

const toneBorder: Record<Tone, string> = {
  good: 'border-success/30 bg-success-subtle',
  warn: 'border-warning/30 bg-warning-subtle',
  bad: 'border-danger/30 bg-danger-subtle',
  neutral: 'border-border bg-surface',
};

const toneText: Record<Tone, string> = {
  good: 'text-success',
  warn: 'text-warning',
  bad: 'text-danger',
  neutral: 'text-text',
};

export function MetricCard({
  label,
  value,
  caption,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  caption?: string;
  tone?: Tone;
}) {
  return (
    <article className={`min-h-[96px] px-4 py-3.5 rounded-lg border ${toneBorder[tone]}`}>
      <span className="block text-text-muted text-xs uppercase tracking-[0.1em] font-semibold">
        {label}
      </span>
      <strong className={`block mt-2 text-[clamp(1.35rem,1.6vw,1.8rem)] leading-none tracking-tight font-bold ${toneText[tone]}`}>
        {value}
      </strong>
      {caption ? (
        <p className="mt-1.5 text-text-secondary text-sm truncate">{caption}</p>
      ) : null}
    </article>
  );
}
