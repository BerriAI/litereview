export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function api(path: string, options: RequestInit = {}) {
  const response = await fetch("/api" + path, {
    ...options,
    headers: {
      ...(options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...options.headers,
    },
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && path !== "/login")
      window.dispatchEvent(new Event("litereview:unauthorized"));
    throw new ApiError(
      typeof data?.detail === "string"
        ? data.detail
        : `Request failed (${response.status}). Try again.`,
      response.status,
    );
  }
  return data;
}

export const send = (method: string, body?: unknown) => ({
  method,
  ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
});
