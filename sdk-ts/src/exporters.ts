/**
 * Exporters ship finished runs off the hot path. All of them swallow their
 * own failures: observability must never be the reason an agent breaks.
 */

import { appendFile } from 'node:fs/promises';
import type { AgentRun, RunData } from './models.js';

export interface Exporter {
  export(run: AgentRun): void;
  exportEvent?(event: Record<string, unknown>): void;
  flush?(timeoutMs?: number): Promise<void>;
}

export class ConsoleExporter implements Exporter {
  export(run: AgentRun): void {
    const d = run.toJSON();
    console.log(
      `[agentlens] run=${d.name} status=${d.status} spans=${d.spans.length} ` +
        `tokens=${d.total_tokens} cost=$${d.total_cost_usd.toFixed(4)} duration=${d.duration_ms}ms`,
    );
  }
}

export class MemoryExporter implements Exporter {
  readonly runs: RunData[] = [];
  readonly events: Record<string, unknown>[] = [];

  export(run: AgentRun): void {
    this.runs.push(run.toJSON());
  }

  exportEvent(event: Record<string, unknown>): void {
    this.events.push(event);
  }
}

export class FileExporter implements Exporter {
  private chain: Promise<void> = Promise.resolve();

  constructor(private readonly path: string) {}

  export(run: AgentRun): void {
    const line = `${JSON.stringify(run.toJSON())}\n`;
    // serialize writes so concurrent runs can't interleave a line
    this.chain = this.chain.then(() => appendFile(this.path, line, 'utf8')).catch(() => {});
  }

  async flush(): Promise<void> {
    await this.chain;
  }
}

/**
 * POSTs runs to an AgentLens server. Requests are fired without awaiting so
 * the agent never blocks on the network; `flush()` waits for them when a
 * short-lived process needs to before exiting.
 */
export class HttpExporter implements Exporter {
  readonly url: string;
  private readonly eventUrl: string;
  private inFlight = new Set<Promise<void>>();

  constructor(
    endpoint = 'http://localhost:7430',
    private readonly options: { apiKey?: string; timeoutMs?: number; stream?: boolean } = {},
  ) {
    const base = endpoint.replace(/\/+$/, '');
    this.url = `${base}/api/ingest/run`;
    this.eventUrl = `${base}/api/ingest/event`;
  }

  export(run: AgentRun): void {
    this.post(this.url, run.toJSON());
  }

  exportEvent(event: Record<string, unknown>): void {
    if (this.options.stream) this.post(this.eventUrl, event);
  }

  private post(url: string, body: unknown): void {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (this.options.apiKey) headers['Authorization'] = `Bearer ${this.options.apiKey}`;
    const task = fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(this.options.timeoutMs ?? 5000),
    })
      .then(() => undefined)
      .catch(() => undefined)
      .finally(() => {
        this.inFlight.delete(task);
      });
    this.inFlight.add(task);
  }

  async flush(timeoutMs = 10_000): Promise<void> {
    await Promise.race([
      Promise.allSettled([...this.inFlight]),
      new Promise((resolve) => setTimeout(resolve, timeoutMs)),
    ]);
  }
}

/** Fan one run out to several exporters; one failing never blocks the rest. */
export class MultiExporter implements Exporter {
  private readonly exporters: Exporter[];

  constructor(...exporters: Exporter[]) {
    this.exporters = exporters;
  }

  export(run: AgentRun): void {
    for (const e of this.exporters) {
      try {
        e.export(run);
      } catch {
        /* keep going */
      }
    }
  }

  exportEvent(event: Record<string, unknown>): void {
    for (const e of this.exporters) {
      try {
        e.exportEvent?.(event);
      } catch {
        /* keep going */
      }
    }
  }

  async flush(timeoutMs?: number): Promise<void> {
    await Promise.allSettled(this.exporters.map((e) => e.flush?.(timeoutMs) ?? Promise.resolve()));
  }
}
