# Source health: audit of 2026-11-22

Six sources were unhealthy on the Source Health page (18 sources, 12 healthy). Root causes were established from the
`source_runs` history, stored records, the run logs under `docs/` and live probes, not from HTTP status codes alone.
Source Health shows the **latest recorded run**, so a source turns healthy only after its next normal run.
Run `RUN_SOURCE_CHECK.cmd` (read-only, no database writes) to verify the sources live from this PC.

| Source | Root cause | Type | Fix | Manual action |
|---|---|---|---|---|
| `venturebeat_ai` | `/category/ai/feed/` is a frozen legacy feed (newest item 2026-08-27); `/feed/` is the live feed. **Live checks on 2026-11-22 (after `USER_AGENT` was set to a real contact): robots.txt HTTP 200, `/feed/` HTTP 429 with no Retry-After.** The 429 does not depend on the URL. | External rate limiting | Feed URL updated. At most 2 requests per run after a 429, then an escalating cross-run cooldown (below). Source and history kept. | None possible on our side; no identity rotation, proxies or other circumvention. Reported as **Rate limited**. |
| `producthunt` | Product Hunt rejects the configured `PRODUCTHUNT_TOKEN`: `HTTP 401, error code invalid_oauth_token` (live 2026-11-22). The API itself is reachable. | Invalid credential | Error code and remedy in the run record. A rejected token is remembered by a one-way fingerprint and **not sent again** (scheduled runs and the diagnostic) until `PRODUCTHUNT_TOKEN` changes. | **Yes**: new Developer Token (below). |
| `the_decoder` | robots.txt has `Disallow: */feed/` for all user agents. Python's `urllib.robotparser` before the recent CPython change treats `*` literally, so the check passed and the request got HTTP 403. | Source-side restriction | RFC 9309 robots.txt matcher (wildcards, `$`, longest match). The source is **disabled**: not scheduled, not requested, shown as "Disabled". | Only with the publisher's written permission or a licensed feed. |
| `arstechnica_ai` | Feed works (live 2026-11-22: HTTP 200, `application/rss+xml`, 20 items). The article page that failed on every run answers **HTTP 405 with a "Human Verification" page** (bot challenge). | Source-side restriction on article pages | Errors keep the HTTP status; the article is stored with its feed summary (`fullTextAvailable=false`); the challenge is not bypassed. A run that needs such a page is PARTIAL. | None. |
| `research_papers` | GitHub quota exhausted at 09:28 after **60** calls (26 in run 3 + 34 in run 28), reset exactly one hour after the first call: the signature of the **unauthenticated** 60/h limit. The configured token is valid now (5000/h, checked 2026-11-22). | Temporary / configuration at that time | Free `/rate_limit` check at the start of each run (flags a missing/rejected token or an empty quota before any repo call); secondary-limit handling with Retry-After; the error is reported once; papers left out are counted as deferred. | None while `GITHUB_TOKEN` stays valid. |
| `yc_startups` | `industries/artificial-intelligence.json` never existed in the yc-oss mirror (no commit touches that path; YC has no such industry). The four `tags/*` endpoints work. | Incorrect endpoint | Endpoint removed. | None. |

## Product Hunt: getting a valid token

1. Sign in at https://www.producthunt.com and open the API dashboard: https://www.producthunt.com/v2/oauth/applications
   (also linked as https://api.producthunt.com/v2/oauth/applications).
2. Create an application (any name; a redirect URI such as `https://localhost/callback` is fine for script use) or open the existing one.
3. On the application page, create/copy the **Developer Token**. Product Hunt describes it as non-expiring and linked to your account.
   Do **not** use the API Key or API Secret: those are OAuth client credentials and give `invalid_oauth_token` when sent as a bearer token.
4. Put it in `.env` as `PRODUCTHUNT_TOKEN=<token>` (no quotes, no spaces). If you regenerate the token later, the old one stops working.
5. Run `RUN_SOURCE_CHECK.cmd --only producthunt`. Expected verdict: `PASS`.

Terms: the Product Hunt API "must not be used for commercial purposes"; business use requires contacting hello@producthunt.com.

## Health categories (Source Health page and dashboard)

| Category | Meaning |
|---|---|
| Healthy | The latest run succeeded. |
| Rate limited | A cooldown is active, or the source answered HTTP 429 within the last 24 h (latest run or a newer live check). Shows when the next request is allowed. |
| Credential required | The source refused the configured credential; the rejected token is not re-sent. Shows the action to take. |
| Partial | The latest run stored data but some requests failed. |
| Failed | The latest run collected nothing, for another reason. |
| Disabled | Excluded by configuration (e.g. robots.txt). Never contacted; earlier history is shown muted. |

Each row also shows the raw status of the latest run and the latest **live check** (newest `docs/source_check_*.json`).
Live checks do not create runs, so a category changes only after the next real run (or, for rate limiting, a newer live 429).

## Rate-limit handling (all sources)

* Retries: up to `HTTP_MAX_RETRIES` with exponential backoff and jitter. `Retry-After` is honoured when it is at most
  `HTTP_MAX_RETRY_AFTER_S` (default 60 s). After a 429, at most `HTTP_MAX_429_RETRIES` (default 1) further request in the
  same run, so a run sends at most 2 requests to a source that keeps answering 429.
* A longer `Retry-After`, or a 429 after that retry, ends the source's run and stores a **cooldown** in its crawl checkpoint:
  15 min, 30 min, 1 h, 2 h, ... capped at 24 h, or the source's own `Retry-After` if longer. Scheduled runs during the cooldown
  make no request and write no run row (the news schedule ticks every 15 min). The first normal answer resets it.
* `RUN_SOURCE_CHECK.cmd` applies the same cooldown and the rejected-token memory to its own requests
  (`data/source_check_state.json`, no secrets). `--force` overrides both; `--probe-ars-article` re-requests the Ars article page.
* robots.txt is evaluated by the project's own RFC 9309 matcher (same result on every Python version).

## User-Agent

`USER_AGENT` in `.env` identifies the crawler with a project URL and contact e-mail (updated 2026-11-22). VentureBeat still
answers 429 with it.
