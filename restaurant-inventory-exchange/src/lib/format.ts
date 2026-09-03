/** Formats a quantity without trailing zeroes: 2, 2.5, 0.25. */
export function quantity(value: number | string): string {
  const n = typeof value === 'string' ? Number(value) : value;
  if (!Number.isFinite(n)) return String(value);
  return String(Math.round(n * 100) / 100);
}

/** "2 sleeves", "1 sleeve", "3 each". */
export function amount(value: number | string, unit: string): string {
  const n = typeof value === 'string' ? Number(value) : value;
  const label = Math.abs(n) === 1 || unit === 'each' ? unit : pluralise(unit);
  return `${quantity(n)} ${label}`;
}

function pluralise(unit: string): string {
  if (/(s|x|ch|sh)$/i.test(unit)) return `${unit}es`;
  return `${unit}s`;
}

const TIME = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });
const DAY = new Intl.DateTimeFormat(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' });
const DATE = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });
const FULL = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
});

/** "Today 3:42 PM", "Tue 9:10 AM", "Mar 4". */
export function when(iso: string, now = new Date()): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const days = daysApart(date, now);
  if (days === 0) return `Today ${TIME.format(date)}`;
  if (days === 1) return `Yesterday ${TIME.format(date)}`;
  if (days < 7) return DAY.format(date);
  if (date.getFullYear() === now.getFullYear()) return DATE.format(date);
  return DATE.format(date) + ', ' + date.getFullYear();
}

export function fullWhen(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : FULL.format(date);
}

function daysApart(a: Date, b: Date): number {
  const dayA = new Date(a.getFullYear(), a.getMonth(), a.getDate()).getTime();
  const dayB = new Date(b.getFullYear(), b.getMonth(), b.getDate()).getTime();
  return Math.round((dayB - dayA) / 86_400_000);
}

/** "Active 2 minutes ago", or null when we have never seen them. */
export function lastSeen(iso: string | null, now = new Date()): string | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const minutes = Math.floor((now.getTime() - then) / 60_000);
  if (minutes < 1) return 'Active now';
  if (minutes < 60) return `Active ${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `Active ${hours} hr ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return 'Active yesterday';
  if (days < 30) return `Active ${days} days ago`;
  return 'Not active recently';
}

/** Someone is "here now" if they used the app in the last five minutes. */
export function isOnline(iso: string | null, now = new Date()): boolean {
  if (!iso) return false;
  const then = new Date(iso).getTime();
  return !Number.isNaN(then) && now.getTime() - then < 5 * 60_000;
}

export const ROLE_LABEL: Record<string, string> = {
  admin: 'Admin',
  manager: 'Manager',
  employee: 'Employee',
};

export const STATUS_LABEL: Record<string, string> = {
  pending: 'Pending',
  active: 'Active',
  disabled: 'Disabled',
};
