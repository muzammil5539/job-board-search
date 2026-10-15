# Boards: coverage, limits and what is deliberately excluded

## Supported

| Board | Scope | Endpoint | Observed ceiling |
|---|---|---|---|
| Lever | per company | `api.lever.co/v0/postings/<slug>?mode=json` | none seen (`ro` = 55) |
| Ashby | per company | `jobs.ashbyhq.com/api/non-user-graphql` POST | none seen (`openai` = 806) |
| Greenhouse | per company | `boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true` | none seen (`databricks` = 898) |
| WorkingNomads | global | `workingnomads.com/api/exposed_jobs/` | ~51 returned |
| RemoteOK | global | `remoteok.com/api` | **99 — a hard cap** |

### Per-company is the central constraint

Lever, Ashby and Greenhouse have **no global search**. There is no endpoint that asks
"all Greenhouse boards, any company, backend roles." Coverage is exactly the slugs in
`companies.txt`. This is why `jobs.py companies` and `jobs.py dork` exist: the first grows
the list, the second reaches postings the list does not cover.

### Pagination

None of the five fetchers paginate. For the three ATS boards this is fine — they return
the whole board in one response. For RemoteOK it is a real ceiling: the API returns ~100
entries including a legal header row, so `fetch_remoteok()` yields 99 and there is no
`?page=` to follow. Treat RemoteOK as a sample of the newest listings, not a census.

### Rate limits

No board publishes one. The fan-out is 8 concurrent requests, and `_get` retries up to 3
times on 429/502/503/504 with exponential backoff, honouring `Retry-After` when sent and
capping any single wait at 30s. If you raise `max_workers` past 8, expect 429s.

## Not supported, and why

| Source | Reason |
|---|---|
| **LinkedIn** | No public jobs API. Scraping breaches their User Agreement and is actively blocked. |
| **Indeed** | Publisher API retired for new keys; scraping breaches their ToS. |
| **FlexJobs** | Returns `000` (connection refused) to scripted clients; listings sit behind a paid subscription. |
| **remoteworkjobs.com** | A parked domain that redirects to `/lander`. RemoteOK is the working board of that name. |

For these, use `jobs.py dork` to build a Google query and open it in Chrome. You read the
results yourself; nothing is fetched programmatically.

## Slug rot

Slugs die. `lever:voiceflow` and `greenhouse:doordash` both 404'd within weeks of being
added. A dead slug is reported at the end of a run under `dead sources:` and is never
fatal. `ashby:deel` is different — it returns **0 postings without erroring**, so a zero
is not necessarily a bug.

## RemoteOK attribution

RemoteOK's API terms request a link back if you republish their listings. Irrelevant for
personal search; relevant if `jobs.html` ever becomes a public page.
