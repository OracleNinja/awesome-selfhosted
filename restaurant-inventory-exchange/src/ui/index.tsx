import { useId, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';

/* Small, boring building blocks. Everything else in the app is composed from
   these so that spacing and touch targets stay consistent. */

export function Screen({
  title,
  back,
  action,
  children,
}: {
  title?: string;
  back?: string | (() => void);
  action?: ReactNode;
  children: ReactNode;
}) {
  const navigate = useNavigate();
  const goBack = () => {
    if (typeof back === 'function') back();
    else if (typeof back === 'string') navigate(back);
  };

  return (
    <div className="screen">
      {(title || back || action) && (
        <header className={`topbar${title ? ' topbar--bordered' : ''}`}>
          <div className="topbar__side">
            {back !== undefined && (
              <button type="button" className="linkbtn" onClick={goBack}>
                <BackChevron /> Back
              </button>
            )}
          </div>
          {title && <h1 className="topbar__title">{title}</h1>}
          <div className="topbar__side topbar__side--right">{action}</div>
        </header>
      )}
      <div className="screen__body">{children}</div>
    </div>
  );
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return <h2 className="section-label">{children}</h2>;
}

export function List({ children }: { children: ReactNode }) {
  return <div className="list">{children}</div>;
}

export function Row({
  title,
  subtitle,
  value,
  onClick,
  disabled,
  trailing,
  href,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  value?: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  trailing?: ReactNode;
  href?: string;
}) {
  const navigate = useNavigate();
  const interactive = Boolean(onClick || href);
  const content = (
    <>
      <span className="list__main">
        <span className="list__title">{title}</span>
        {subtitle && <span className="list__sub">{subtitle}</span>}
      </span>
      {value && <span className="list__value">{value}</span>}
      {trailing}
      {interactive && !trailing && <Chevron />}
    </>
  );

  if (!interactive) {
    return <div className="list__row list__row--static">{content}</div>;
  }
  return (
    <button
      type="button"
      className="list__row"
      disabled={disabled}
      onClick={() => (href ? navigate(href) : onClick?.())}
    >
      {content}
    </button>
  );
}

export function Action({
  label,
  hint,
  onClick,
  badge,
}: {
  label: string;
  hint?: string;
  onClick: () => void;
  badge?: ReactNode;
}) {
  return (
    <button type="button" className="action" onClick={onClick}>
      <span className="action__label">
        {label}
        {hint && <span className="action__hint">{hint}</span>}
      </span>
      {badge}
      <Chevron />
    </button>
  );
}

export function Field({
  label,
  ...input
}: { label: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <label className="field" htmlFor={id}>
      <span className="field__label">{label}</span>
      <input id={id} className="field__input" {...input} />
    </label>
  );
}

export function SelectField({
  label,
  children,
  ...select
}: { label: string; children: ReactNode } & React.SelectHTMLAttributes<HTMLSelectElement>) {
  const id = useId();
  return (
    <label className="field" htmlFor={id}>
      <span className="field__label">{label}</span>
      <select id={id} className="select" {...select}>
        {children}
      </select>
    </label>
  );
}

export function Search({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}) {
  return (
    <div className="search">
      <SearchIcon />
      <input
        type="search"
        value={value}
        placeholder={placeholder}
        aria-label={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

export function Stepper({
  value,
  unit,
  onChange,
  min = 1,
  max = 999,
}: {
  value: number;
  unit: string;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
}) {
  const step = (delta: number) => onChange(Math.min(max, Math.max(min, value + delta)));
  return (
    <div className="stepper">
      <button
        type="button"
        className="stepper__btn"
        aria-label="Decrease quantity"
        disabled={value <= min}
        onClick={() => step(-1)}
      >
        &minus;
      </button>
      <div className="stepper__value">
        <div className="stepper__number" aria-live="polite">
          {value}
        </div>
        <span className="stepper__unit">{unit}</span>
      </div>
      <button
        type="button"
        className="stepper__btn"
        aria-label="Increase quantity"
        disabled={value >= max}
        onClick={() => step(1)}
      >
        +
      </button>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function Spinner() {
  return <div className="spinner" role="status" aria-label="Loading" />;
}

export function Notice({ children, tone = 'error' }: { children: ReactNode; tone?: 'error' | 'info' }) {
  return (
    <p className={`notice${tone === 'info' ? ' notice--info' : ''}`} role={tone === 'error' ? 'alert' : undefined}>
      {children}
    </p>
  );
}

export function Tag({
  children,
  tone = 'default',
}: {
  children: ReactNode;
  tone?: 'default' | 'pending' | 'ok' | 'void' | 'danger';
}) {
  return <span className={`tag${tone === 'default' ? '' : ` tag--${tone}`}`}>{children}</span>;
}

export function Route({ from, to }: { from: string; to: string }) {
  return (
    <span className="route">
      <strong>{from}</strong>
      <span className="route__arrow" aria-label="to">
        &rarr;
      </span>
      <strong>{to}</strong>
    </span>
  );
}

/* ------------------------------------------------------------------ icons -- */

export function Chevron() {
  return (
    <svg className="chevron" width="8" height="13" viewBox="0 0 8 13" aria-hidden="true">
      <path
        d="M1.5 1.5 6.5 6.5 1.5 11.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function BackChevron() {
  return (
    <svg width="9" height="15" viewBox="0 0 8 13" aria-hidden="true">
      <path
        d="M6.5 1.5 1.5 6.5 6.5 11.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" style={{ color: 'var(--faint)' }}>
      <circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M11 11l4 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

export function CheckMark() {
  return (
    <svg width="34" height="34" viewBox="0 0 34 34" aria-hidden="true">
      <path
        d="M7 18l7 7 13-15"
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
