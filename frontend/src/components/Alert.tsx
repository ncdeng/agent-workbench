import { X, AlertTriangle, CheckCircle2, Info } from 'lucide-react';

type AlertVariant = 'danger' | 'warning' | 'success' | 'info';

const variantConfig: Record<AlertVariant, { icon: React.ReactNode; className: string }> = {
  danger: {
    icon: <AlertTriangle size={16} />,
    className: 'bg-danger-subtle border-danger/25 text-danger',
  },
  warning: {
    icon: <AlertTriangle size={16} />,
    className: 'bg-warning-subtle border-warning/25 text-warning',
  },
  success: {
    icon: <CheckCircle2 size={16} />,
    className: 'bg-success-subtle border-success/25 text-success',
  },
  info: {
    icon: <Info size={16} />,
    className: 'bg-primary-subtle border-primary/25 text-primary',
  },
};

export function Alert({
  children,
  variant = 'info',
  onDismiss,
}: {
  children: React.ReactNode;
  variant?: AlertVariant;
  onDismiss?: () => void;
}) {
  const config = variantConfig[variant];
  return (
    <div className={`flex items-start gap-2.5 px-4 py-3 rounded-xl border text-sm ${config.className}`}>
      <span className="mt-0.5 flex-shrink-0">{config.icon}</span>
      <span className="flex-1 min-w-0">{children}</span>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="flex-shrink-0 p-0.5 rounded-md hover:bg-black/5 transition-colors"
          aria-label="关闭"
        >
          <X size={14} />
        </button>
      )}
    </div>
  );
}
