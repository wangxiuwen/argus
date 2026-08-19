// Thin fetch layer over the Mira bridge (same origin, ui.py proxies /mira/*).
export async function mediaJson<T = any>(url: string, options?: RequestInit): Promise<T> {
  const r = await fetch(url, options);
  const body = await r.json();
  if (!r.ok) throw Error(body.error || `HTTP ${r.status}`);
  return body as T;
}

export function mediaPost<T = any>(kind: string, action: string, body: any = {}): Promise<T> {
  return mediaJson<T>(`/mira/${kind}/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export const delay = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));
