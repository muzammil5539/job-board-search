# Output schema

## `jobs.json`

A JSON array. Every element has exactly these eight keys, every key always present:

| Key | Type | Notes |
|---|---|---|
| `source` | string | One of `lever`, `ashby`, `greenhouse`, `workingnomads`, `remoteok` |
| `company` | string | For Lever/Ashby/Greenhouse this is the **slug**, not the display name |
| `title` | string | As the board gives it, untrimmed |
| `location` | string | Free text. `""` when the board omits it. Part of the dedup key |
| `url` | string | Direct apply/detail link |
| `posted` | string, int or null | **Not normalised.** Lever = epoch ms, RemoteOK = epoch s or ISO, Greenhouse/WorkingNomads = ISO. Pass through `fmt_date()` before display |
| `description` | string | HTML tags stripped, **truncated to 4000 chars** |
| `employmentType` | — | *Not* emitted. Ashby returns it but it is folded into `description` |

Example element:

```json
{
 "source": "greenhouse",
 "company": "stripe",
 "title": "Backend Engineer, Developer & End-user Experience",
 "location": "Toronto Canada, San Francisco, Remote in US",
 "url": "https://stripe.com/jobs/search?gh_jid=8172487",
 "posted": "2026-10-08T00:00:00-04:00",
 "description": "Stripe is a financial infrastructure platform ..."
}
```

### Ordering and dedup

Sorted by `(source, company, title)`. Deduplicated on
`(company.lower().strip(), title.lower().strip(), location.lower().strip())`.

Location is in the key deliberately. With a `(company, title)` key, Stripe's 728
postings collapsed to 671 — and the 57 lost included one title listed separately in
`US-CA` and `Singapore`, which are different jobs. The run prints how many exact
duplicates it collapsed.

## `jobs.html`

One self-contained file. No network calls, no external CSS or JS, safe to email or
archive. Light/dark via `prefers-color-scheme`. Carries a trimmed copy of the data in an
inline `const D = [...]` (only `title`, `company`, `location`, `url`, `source`) to drive
the copy button.

## `score.json` (from `score --json-out`)

```json
{
 "total": 45, "keyword": 32, "quantified": 14, "parse": 100,
 "missing": ["databricks", "migrations"],
 "matched": ["python", "engineering"],
 "flags": ["412 words (aim 450-900 for one or two pages)"]
}
```

`total` is `round(0.55*keyword + 0.20*quantified + 0.25*parse)`, clamped only by its
inputs. `flags` is empty when the resume parses cleanly.
