export function pct(v: number | null | undefined, digits = 0) {
  return v == null ? "—" : `${v.toFixed(digits)}%`;
}

export function score(v: number | null | undefined) {
  return v == null ? "—" : Math.round(v).toString();
}

export function shortDate(iso: string) {
  return new Date(iso.length === 10 ? iso + "T00:00:00Z" : iso).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    timeZone: iso.length === 10 ? "UTC" : "Asia/Kolkata",
  });
}

export function dateTime(iso: string) {
  return new Date(iso).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Kolkata",
  });
}
