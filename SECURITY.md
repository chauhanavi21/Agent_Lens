# Security policy

## Reporting a vulnerability

Please report security issues privately through
[GitHub's advisory form](https://github.com/chauhanavi21/Agent_Lens/security/advisories/new)
rather than opening a public issue. I'll acknowledge within a few days.

## What's in scope

AgentLens handles agent traces, which routinely contain more sensitive data
than anyone intended to log. The areas most worth scrutiny:

- **Redaction bypasses** — a construction that reaches an exporter unscrubbed
  while `redact=True`. Both false negatives (a secret that survives) and the
  paths around the redactor (streaming events, MCP server spans, OTLP
  export) count.
- **Ingest authentication** — anything that lets an unauthenticated caller
  write runs, alert rules, or scores when `AGENTLENS_API_KEY` is set.
- **Webhook handling** — alert rules take a URL, so SSRF against internal
  services is a real concern for a self-hosted deployment.
- **Replay** — cassettes are loaded from disk and their contents flow back
  into an agent as tool results.

## Known limitations, by design

These are documented rather than hidden — see
[ARCHITECTURE.md](ARCHITECTURE.md) for the full list:

- **Authentication is a single shared bearer token.** That's adequate for a
  self-hosted deployment behind a VPN and *not* adequate for multi-tenant
  use. There is no per-user authorization.
- **Redaction is best-effort.** Pattern matching cannot catch every secret;
  a short random API key is indistinguishable from any other string, which
  is why field-name rules exist alongside the patterns. When content isn't
  needed, `capture_content=False` is strictly safer than scrubbing it.
- **The server trusts its ingest payloads.** Anything that can reach the
  ingest endpoint can write arbitrary span content.

## Deploying safely

- Set `AGENTLENS_API_KEY` to a strong random value.
- Set `AGENTLENS_HASH_SECRET` so redaction fingerprints aren't guessable.
- Restrict `CORS_ORIGINS` to your UI's domain.
- Terminate TLS in front of the server; it speaks plain HTTP.
- Keep the ingest endpoint off the public internet if you can.
