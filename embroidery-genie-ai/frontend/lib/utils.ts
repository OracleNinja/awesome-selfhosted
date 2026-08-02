import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatNumber(value: number | null | undefined, fallback = "—") {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback;
  return new Intl.NumberFormat("en-US").format(value);
}

export function formatMoney(value: number | null | undefined, currency = "USD") {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatMinutes(minutes: number | null | undefined) {
  if (!minutes && minutes !== 0) return "—";
  if (minutes < 1) return `${Math.round(minutes * 60)}s`;
  const whole = Math.floor(minutes);
  const seconds = Math.round((minutes - whole) * 60);
  if (whole < 60) return seconds ? `${whole}m ${seconds}s` : `${whole}m`;
  return `${Math.floor(whole / 60)}h ${whole % 60}m`;
}

export function formatMm(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(1)} mm`;
}

export function formatDate(value: string | null | undefined) {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function relativeTime(value: string | null | undefined) {
  if (!value) return "—";
  const then = new Date(value).getTime();
  const diff = Date.now() - then;
  const minutes = Math.round(diff / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return formatDate(value);
}

/** Readable label from a snake_case key. */
export function humanize(value: string) {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

/** Pick black or white text for a given background so labels stay legible. */
export function contrastText(hex: string) {
  const value = hex.replace("#", "");
  if (value.length < 6) return "#000000";
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  // Rec. 709 luma.
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 150 ? "#000000" : "#FFFFFF";
}
