/** A Server-Sent Events reader for a fetch() response body. */

export interface SseEvent {
  event: string;
  data: string;
}

/** Split a byte stream into SSE events. Handles \n, \r\n and \r line ends, comments, and data split over several lines. */
export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let event = '';
  let data: string[] = [];
  const flush = (): SseEvent | null => {
    if (!data.length && !event) return null;
    const out = { event: event || 'message', data: data.join('\n') };
    event = '';
    data = [];
    return out;
  };
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: !done });
      if (done) buffer += decoder.decode();
      const lines = buffer.split(/\r\n|\n|\r/);
      buffer = done ? '' : (lines.pop() ?? '');
      for (const line of lines) {
        if (line === '') {
          const ev = flush();
          if (ev) yield ev;
        } else if (line.startsWith(':')) {
          // comment / keep-alive
        } else {
          const i = line.indexOf(':');
          const field = i < 0 ? line : line.slice(0, i);
          let val = i < 0 ? '' : line.slice(i + 1);
          if (val.startsWith(' ')) val = val.slice(1);
          if (field === 'event') event = val;
          else if (field === 'data') data.push(val);
        }
      }
      if (done) {
        const ev = flush();
        if (ev) yield ev;
        return;
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // already released
    }
  }
}
