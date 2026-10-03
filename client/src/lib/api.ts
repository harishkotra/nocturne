import type { ConnectionTest, DiffResult, Health, MemoryPayload } from "./types";

export class ApiError extends Error {
  status?: number;
  body?: string;
  constructor(message: string, status?: number, body?: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError(res.status >= 400 ? `HTTP ${res.status}` : "server returned a non-JSON body", res.status, text.slice(0, 400));
  }
  if (res.status >= 400) {
    const msg = (data as { error?: string })?.error || `HTTP ${res.status}`;
    throw new ApiError(msg, res.status, text.slice(0, 400));
  }
  return data as T;
}

export const getHealth = () => fetch("/api/health").then(jsonOrThrow<Health>);
export const getMemory = () => fetch("/api/memory").then(jsonOrThrow<MemoryPayload>);
export const getMutations = (pass?: string) =>
  fetch(`/api/mutations${pass ? `?pass_name=${pass}` : ""}`).then(jsonOrThrow<{ mutations: unknown[] }>);

export async function postJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  return jsonOrThrow<T>(res);
}

export const testConnection = (provider: unknown) =>
  postJson<ConnectionTest>("/api/test-connection", { provider });

export const restoreSnapshot = (snapshotId: string) =>
  postJson<{ restoredFrom: string; snapshotId: string; activeCount: number; memory: MemoryPayload }>("/api/restore", {
    snapshotId,
  });

export const fetchDiff = (from: string, to: string) => postJson<DiffResult>("/api/diff", { from, to });

export const resetAll = (hard = false) =>
  postJson<{ ok: boolean; wipe: string; restoreTo?: string; memory: MemoryPayload }>("/api/reset", { hard });

/** POST + read a server-sent-events stream; EventSource cannot POST bodies. */
export async function postSse(
  url: string,
  body: unknown,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body ?? {}),
      signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(`cannot reach ${url}: ${(err as Error).message}`);
  }
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new ApiError(`HTTP ${res.status} from ${url}`, res.status, text.slice(0, 400));
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = "message";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trimStart();
      }
      if (!data) continue;
      try {
        onEvent(event, JSON.parse(data) as Record<string, unknown>);
      } catch {
        onEvent(event, { raw: data });
      }
    }
  }
}
