/** Small formatters shared across the workstation. */

export function ms(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(1)} s`;
  return `${Math.floor(value / 60_000)}m ${Math.round((value % 60_000) / 1000)}s`;
}

export function countdown(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  return m ? `${m}m ${s % 60}s` : `${s}s`;
}

/** Epoch seconds -> "in 24 minutes" / "3 minutes ago". */
export function relativeTime(epochSeconds: number): string {
  if (!epochSeconds) return "—";
  const delta = epochSeconds * 1000 - Date.now();
  const abs = Math.abs(delta);
  const units: [number, Intl.RelativeTimeFormatUnit][] = [
    [60_000, "second"], [3_600_000, "minute"], [86_400_000, "hour"], [Infinity, "day"],
  ];
  const divisors = [1000, 60_000, 3_600_000, 86_400_000];
  const idx = units.findIndex(([limit]) => abs < limit);
  const fmt = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  return fmt.format(Math.round(delta / divisors[idx]), units[idx][1]);
}

export function timeOfDay(epochSeconds: number): string {
  if (!epochSeconds) return "—";
  return new Date(epochSeconds * 1000).toLocaleString(undefined, {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

export function initialsOf(name: string): string {
  const parts = name.replace(/[^a-zA-Z ]/g, " ").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return (parts.length === 1 ? parts[0].slice(0, 2) : parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}
