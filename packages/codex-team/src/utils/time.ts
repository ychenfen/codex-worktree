function pad2(value: number): string {
  return value.toString().padStart(2, "0");
}

export function messageTimestamp(now = new Date()): string {
  const y = now.getFullYear();
  const m = pad2(now.getMonth() + 1);
  const d = pad2(now.getDate());
  const hh = pad2(now.getHours());
  const mm = pad2(now.getMinutes());
  return `${y}${m}${d}-${hh}${mm}`;
}

export function isoTimestamp(now = new Date()): string {
  return now.toISOString();
}
