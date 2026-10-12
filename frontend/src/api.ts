export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

type Options = RequestInit & { background?: boolean; label?: string };
type Pending = { id: number; label: string; started: number };
let pending: Pending[] = [];
let sequence = 0;
const listeners = new Set<() => void>();
const reads = new Map<string, Promise<any>>();
export const requestState = {
  subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
  getSnapshot: () => pending,
};
function notify() { listeners.forEach(listener => listener()); }
function requestLabel(path: string, method: string) {
  if (path.endsWith("/test")) return "Checking connection…";
  if (method === "DELETE") return "Removing item…";
  if (method !== "GET") return path.includes("generate") ? "Creating agent draft…" : "Saving changes…";
  if (path.startsWith("/runs/")) return "Loading review…";
  return `Loading ${path.startsWith("/workspace") ? "workspace" : path.startsWith("/spend") ? "spend" : path.startsWith("/runs") ? "runs" : "settings"}…`;
}

export function api(path: string, options: Options = {}): Promise<any> {
  const method = options.method || "GET";
  const deduplicate = method === "GET" && !options.signal;
  if (deduplicate && reads.has(path)) return reads.get(path)!;
  const operation = perform(path, options, method);
  if (deduplicate) {
    reads.set(path, operation);
    void operation.finally(() => { if (reads.get(path) === operation) reads.delete(path); }).catch(() => {});
  }
  return operation;
}

async function perform(path: string, options: Options, method: string) {
  const { background, label, ...init } = options;
  const id = ++sequence;
  if (!background) { pending = [...pending, { id, label: label || requestLabel(path, method), started: Date.now() }]; notify(); }
  const controller = new AbortController();
  const abort = () => controller.abort();
  init.signal?.addEventListener("abort", abort, { once: true });
  if (init.signal?.aborted) controller.abort();
  // Reads are safe to retry. Never automatically retry or time out a mutation whose outcome is unknown.
  const timeout = method === "GET" ? setTimeout(abort, 20000) : undefined;
  try {
    const response = await fetch("/api" + path, {
      ...init, signal: controller.signal,
      headers: { ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...options.headers },
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      if (response.status === 401 && path !== "/login") window.dispatchEvent(new Event("litereview:unauthorized"));
      throw new ApiError(typeof data?.detail === "string" ? data.detail : `Request failed (${response.status}). Try again.`, response.status);
    }
    if (data === null && response.status !== 204) throw new ApiError("The server returned an unreadable response. Try again.", response.status);
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (init.signal?.aborted) throw error;
    throw new ApiError(controller.signal.aborted ? "The server took too long to respond. Retry to load the latest data."
      : method === "GET" ? "Couldn’t reach LiteReview. Check your connection and retry."
      : "Couldn’t confirm the change. Check the saved value before trying again.", 0);
  } finally {
    clearTimeout(timeout); init.signal?.removeEventListener("abort", abort);
    if (!background) { pending = pending.filter(request => request.id !== id); notify(); }
  }
}
export const send = (method: string, body?: unknown) => ({ method, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
