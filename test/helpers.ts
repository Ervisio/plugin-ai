/** Canned HTTP responses for the provider tests. */

export function sse(events: { event?: string; data: unknown }[]): Response {
  const text = events.map((e) => (e.event ? `event: ${e.event}\n` : '') + `data: ${typeof e.data === 'string' ? e.data : JSON.stringify(e.data)}\n\n`).join('');
  return streamOf([text]);
}

/** A response whose body arrives in the given pieces (to test chunk boundaries). */
export function streamOf(pieces: string[], init: ResponseInit = { status: 200, headers: { 'content-type': 'text/event-stream' } }): Response {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const p of pieces) c.enqueue(enc.encode(p));
      c.close();
    },
  });
  return new Response(body, init);
}

export function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', ...headers } });
}

export interface Seen {
  url: string;
  init: RequestInit;
  body: any;
}

/** A fetch that answers from a queue and remembers what it was asked. */
export function fakeFetch(answers: (Response | Error)[]): { f: (u: string, i?: RequestInit) => Promise<Response>; seen: Seen[] } {
  const seen: Seen[] = [];
  const queue = [...answers];
  return {
    seen,
    f: async (url, init = {}) => {
      seen.push({ url, init, body: typeof init.body === 'string' ? JSON.parse(init.body) : undefined });
      const a = queue.shift();
      if (!a) throw new Error('no more canned answers');
      if (a instanceof Error) throw a;
      return a;
    },
  };
}
