import type { HTMLAttributes, ReactNode } from 'react';

export function Card({
  children,
  className = '',
  hover = false,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  hover?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      className={`surface ${hover ? 'surface-hover transition-all duration-200' : ''} ${className}`}
      {...props}
    >
      {children}
    </div>
  );
}
