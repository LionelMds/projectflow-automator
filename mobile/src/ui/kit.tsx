// Petites briques visuelles de la maquette (cadre blueprint, cases à cocher, icônes Lucide 1.5).
import type { CSSProperties, ReactNode } from 'react';

export function Blueprint({ children, className = '', style, as: Tag = 'div', ...rest }: {
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
  as?: 'div' | 'button';
  onClick?: () => void;
  type?: 'button';
}) {
  return (
    <Tag className={`blueprint ${className}`} style={style} {...rest}>
      <i className="corner tl" />
      <i className="corner tr" />
      <i className="corner bl" />
      <i className="corner br" />
      {children}
    </Tag>
  );
}

export function SectionHead({ num, title, right, mb = 14 }: { num?: string; title: string; right?: ReactNode; mb?: number }) {
  return (
    <div className="pf-head" style={{ marginBottom: mb }}>
      {num && <span className="pf-num">{num}</span>}
      <span className="pf-title">{title}</span>
      {right && <span style={{ marginLeft: 'auto' }}>{right}</span>}
    </div>
  );
}

export function Check({ on }: { on: boolean }) {
  return (
    <span className={`pf-check${on ? ' on' : ''}`} aria-hidden>
      {on && <Icon d="M20 6 9 17l-5-5" size={14} stroke={2} />}
    </span>
  );
}

export function CheckRow({ on, onToggle, title, sub, ruled, disabled, minHeight, titleSize }: {
  on: boolean;
  onToggle: () => void;
  title: ReactNode;
  sub?: ReactNode;
  ruled?: boolean;
  disabled?: boolean;
  minHeight?: number;
  titleSize?: number;
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={on}
      aria-disabled={disabled || undefined}
      className={`pf-row${ruled ? ' ruled' : ''}`}
      style={minHeight ? { minHeight } : undefined}
      onClick={onToggle}
    >
      <Check on={on} />
      <span style={{ flex: 1, minWidth: 0 }}>
        <span className="pf-row-title" style={{ display: 'block', fontSize: titleSize }}>{title}</span>
        {sub && <span className="pf-row-sub" style={{ display: 'block' }}>{sub}</span>}
      </span>
    </button>
  );
}

export function Icon({ d, size = 20, stroke = 1.5, children, style }: { d?: string; size?: number; stroke?: number; children?: ReactNode; style?: CSSProperties }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" style={style} aria-hidden>
      {d && <path d={d} />}
      {children}
    </svg>
  );
}

export const ICONS = {
  sliders: 'M21 4h-7M10 4H3M21 12h-9M8 12H3M21 20h-5M12 20H3M14 2v4M8 10v4M16 18v4',
  check: 'M20 6 9 17l-5-5',
  close: 'M18 6 6 18M6 6l12 12',
  chevron: 'm9 18 6-6-6-6',
  creer: 'M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2ZM8 12h8M12 8v8',
  sortie: 'm18 9-6-6-6 6M12 3v14M5 21h14',
  rep: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  refresh: 'M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.2L3 16M3 21v-5h5',
};

export function MailIcon({ size = 18 }: { size?: number }) {
  return (
    <Icon size={size}>
      <rect x="2" y="4" width="20" height="16" rx="2" />
      <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
    </Icon>
  );
}

export function DotsIcon() {
  return (
    <Icon>
      <circle cx="12" cy="12" r="1" />
      <circle cx="19" cy="12" r="1" />
      <circle cx="5" cy="12" r="1" />
    </Icon>
  );
}

export function SearchIcon() {
  return (
    <Icon size={18} style={{ position: 'absolute', left: 10, top: 13, color: 'var(--color-neutral-700)' }}>
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.3-4.3" />
    </Icon>
  );
}
