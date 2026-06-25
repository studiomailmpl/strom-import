/**
 * API client for the FastAPI backend.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface FetchOptions extends RequestInit {
  token?: string;
}

/**
 * Typed API client with timeout, auth, and user-friendly error messages.
 */
export async function apiFetch<T = unknown>(
  path: string,
  options: FetchOptions = {}
): Promise<T> {
  const { token, headers, signal, ...rest } = options;

  // Default 120s timeout unless caller provides an AbortSignal
  const controller = !signal ? new AbortController() : undefined;
  const timeoutId = controller ? setTimeout(() => controller.abort(), 120_000) : undefined;

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...headers,
      },
      signal: signal || controller?.signal,
      ...rest,
    });

    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(error.detail || `API error: ${res.status}`);
    }

    // 204 No Content or 205 Reset Content — no body to parse
    if (res.status === 204 || res.status === 205) {
      return null as unknown as T;
    }

    // Guard against empty bodies on other 2xx responses
    const text = await res.text();
    if (!text) {
      return null as unknown as T;
    }

    return JSON.parse(text) as T;
  } catch (err) {
    // Enhance error messages for common network failures
    if (err instanceof TypeError && err.message === "Failed to fetch") {
      throw new Error(
        "Kan ikke nå serveren — tjek din internetforbindelse eller prøv igen om lidt."
      );
    }
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(
        "Forespørgslen tog for lang tid. Prøv igen."
      );
    }
    throw err;
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
  }
}

export async function apiUpload<T = unknown>(
  path: string,
  file: File,
  token?: string,
  extraFields?: Record<string, string>
): Promise<T> {
  const formData = new FormData();
  formData.append("file", file);
  if (extraFields) {
    for (const [key, value] of Object.entries(extraFields)) {
      formData.append(key, value);
    }
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 120_000); // 2 min for uploads

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: formData,
      signal: controller.signal,
    });

    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(error.detail || `Upload error: ${res.status}`);
    }

    return res.json();
  } catch (err) {
    if (err instanceof TypeError && err.message === "Failed to fetch") {
      throw new Error(
        "Kan ikke nå serveren — tjek din internetforbindelse eller prøv igen om lidt."
      );
    }
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(
        "Upload tog for lang tid. Prøv igen med en mindre fil."
      );
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

/** Upload multiple files in a single FormData request */
export async function apiUploadMultiple<T = unknown>(
  path: string,
  files: File[],
  token?: string,
  extraFields?: Record<string, string>
): Promise<T> {
  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file);
  }
  if (extraFields) {
    for (const [key, value] of Object.entries(extraFields)) {
      formData.append(key, value);
    }
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 120_000); // 2 min for multi-upload

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: formData,
      signal: controller.signal,
    });

    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(error.detail || `Upload error: ${res.status}`);
    }

    return res.json();
  } catch (err) {
    if (err instanceof TypeError && err.message === "Failed to fetch") {
      throw new Error(
        "Kan ikke nå serveren — tjek din internetforbindelse eller prøv igen om lidt."
      );
    }
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(
        "Upload tog for lang tid. Prøv igen med færre eller mindre filer."
      );
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

/** SSE event from the analyse stream */
export interface SSEEvent {
  type: "file_start" | "log" | "progress" | "file_done" | "done" | "error";
  file_name?: string;
  file_index?: number;
  total_files?: number;
  message?: string;
  timestamp?: string;
  percent?: number;
  current_file?: number;
  products_found?: number;
  total_products?: number;
}

/**
 * Connect to an SSE stream and call onEvent for each parsed event.
 * Returns an abort function.
 */
export function apiStream(
  path: string,
  token: string,
  onEvent: (event: SSEEvent) => void,
  onError?: (error: Error) => void
): () => void {
  const controller = new AbortController();

  (async () => {
    try {
      const res = await fetch(`${API_BASE}${path}`, {
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "text/event-stream",
        },
        signal: controller.signal,
      });

      if (!res.ok) {
        throw new Error(`Stream error: ${res.status}`);
      }

      const reader = res.body?.getReader();
      if (!reader) throw new Error("No readable stream");

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (trimmed.startsWith("data:")) {
            const jsonStr = trimmed.slice(5).trim();
            if (jsonStr) {
              try {
                const event = JSON.parse(jsonStr) as SSEEvent;
                onEvent(event);
              } catch {
                // Skip malformed JSON
              }
            }
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        onError?.(err as Error);
      }
    }
  })();

  return () => controller.abort();
}

export { API_BASE };
