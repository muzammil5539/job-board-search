---
name: job-board-search
description: >
  Find and shortlist job openings, then prepare the application. Searches Lever, Ashby,
  Greenhouse, WorkingNomads and RemoteOK for roles by title, seniority, stack or location
  (backend, frontend, fullstack, AI/ML, data, DevOps, product; remote, Europe, US, APAC,
  worldwide), renders them as a filterable HTML list, resolves a company name or careers
  URL to its job board, scrapes job postings and discovers new company job boards from
  search results, builds Google site: queries for boards it cannot call, scores a
  resume against a job description for ATS keyword coverage, generates a copy/paste
  application-form answer sheet, and drafts an interview prep sheet. Use when asked to
  find/search/list/scrape jobs or openings, discover or add company job boards, check which
  board a company uses, open job tabs, score or tailor a
  resume for a posting, or prep for an interview. Does NOT search LinkedIn, Indeed,
  Glassdoor or FlexJobs, does not submit applications, and cannot filter by funding stage,
  company size or salary.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, AskUserQuestion
---

# job-board-search

One driver, `jobs.py`, with seven subcommands arranged as a pipeline. Boards are reached
through their **public JSON APIs**, not by scraping rendered pages. Chrome is used for
*viewing* — the HTML result list and Google queries — never for fetching.

Paths are relative to the repo root. Run `python3 jobs.py --help` for the full flag list.

**Deeper reference:** [output schema](references/output-schema.md) ·
[board coverage and limits](references/boards.md) ·
[how the ATS score works](references/ats-scoring.md)

---

## Phase 0 — Resolve companies

**Goal** Turn a company name or careers URL into an `<ats>:<slug>` line.
**Input** One or more names or URLs.
**When** Before Phase 1, whenever the user names an employer not already in `companies.txt`.

Lever, Ashby and Greenhouse have **no global search** — coverage is exactly what
`companies.txt` lists. Grow it first.

```bash
python3 jobs.py companies "Ramp" "Scale AI" "https://boards.greenhouse.io/airbnb"
```

```
  ok Ramp: ashby:ramp  (164 postings, already present)
  ok Scale AI: greenhouse:scaleai  (179 postings, NEW)
  ok https://boards.greenhouse.io/airbnb: greenhouse:airbnb  (163 postings, already present)
```

Add `--add` to append new hits to `companies.txt`. It probes every candidate slug against
all three ATSes and verifies by posting count, so a reported hit is a live board.

**Output** Printed `<ats>:<slug>` lines; appended to `companies.txt` with `--add`.

**Failure modes**
- *No board found* — the employer is not on these three ATSes. The tool names the slugs
  it tried. Open their careers page; if the URL is not `jobs.lever.co`,
  `jobs.ashbyhq.com` or `boards.greenhouse.io`, this tool cannot reach it. Go to Phase 2.
- *Ambiguous: several boards match* — all are printed with posting counts. Pick by count;
  near-zero is usually a stale board.
- *Resolves but 0 postings* — the board exists and is empty (`ashby:deel` behaves this
  way). Not an error, not worth adding.

---

## Phase 1 — Search

**Goal** A deduplicated, filtered job list on disk.
**Input** Keywords, locations, exclusions.

```bash
python3 jobs.py search \
  -k "AI,Machine Learning,LLM,Backend,Fullstack,Full Stack,Python,Forward Deployed" \
  -l "remote,anywhere,worldwide" \
  -x "Director,Account Executive,Sales,Recruiter" \
  --open
```

```
73 jobs (from 3,296 fetched, 0 exact duplicates collapsed) -> jobs.json / jobs.html
```

| Flag | Effect |
|---|---|
| `-k` | Keywords, OR-matched against title + company + description |
| `-l` | Locations, OR-matched against location + title |
| `-x` | Exclusions; drops any job containing the term |
| `--only` | Limit sources: `lever ashby greenhouse workingnomads remoteok` |
| `-o` | Output basename (default `jobs`) |
| `--open` | Open `jobs.html` in Chrome |

All three match on **whole words**: `-k "AI"` does not hit *available* or *training*.
Without that the same run returned 288 jobs instead of 73.

**Output** `jobs.json` + a self-contained `jobs.html` — see
[output-schema.md](references/output-schema.md). The HTML has a live text filter,
per-source chips, collapsible descriptions and a copy button per job.

**Failure modes**
- *No results* — the run says so and suggests widening. Drop `-l` first (it is the
  strictest filter), then shorten `-k`, then check `-x` is not eating matches.
- *Too many results* — add `-x` terms for the noise you see, or narrow `-l`. The HTML
  filter box is faster than re-running for exploratory narrowing.
- *Unknown `--only` value* — exits with the valid list. It no longer returns a silent
  `0 jobs`, which was indistinguishable from a genuine no-match.
- *Malformed `companies.txt`* — bad lines print as `WARN <file>:<line>` and are skipped;
  the run continues on the valid ones.
- *`dead sources:` at the end* — those slugs 404'd. Prune them from `companies.txt`.
- *Rate limited* — `_get` retries 3× with backoff on 429/502/503/504. A source that still
  fails is listed as dead; re-run it alone with `--only`.

---

## Phase 2 — Reach boards the APIs cannot

**Goal** Google queries for sources with no usable API.
**Input** The same keywords and locations as Phase 1.

```bash
python3 jobs.py dork \
  -k "AI,Forward Deployed,Backend,Frontend,Fullstack" \
  -l "Remote,Anywhere" -e "after:2026-10-01" --open
```

```
site:workingnomads.com/jobs ("AI" OR "Forward Deployed" OR "Backend") ("Remote" OR "Anywhere") after:2026-10-01
```

`--open` opens each query in Chrome. `-e` appends raw query text (`after:`, `intitle:`,
`-intern`).

**Output** One query + URL per board, printed. Nothing is fetched — you read the results.

**Failure mode** Google may serve a CAPTCHA on rapid repeat queries. Space them out;
never attempt to solve one programmatically.

---

## Phase 2b — Scrape postings and discover new boards

**Goal** Turn search results into structured jobs **and** into new `companies.txt` slugs.
**Input** Keywords, or a file of posting URLs.
**Why** This is the only phase that grows ATS coverage automatically. Phase 0 needs you to
name a company; this one finds companies you did not know to name.

```bash
python3 jobs.py scrape --urls urls.txt --merge jobs.json --add
```

```
  7 url(s) from urls.txt
  scraped 6/7 posting(s)
  NEW ashby:linear (31 postings)
  NEW greenhouse:scaleai (179 postings)
  NEW lever:ro (55 postings)
  appended 3 slug(s) to companies.txt
6 job(s) -> scraped_jobs.json
  merged into jobs.json: 6 new, 9 total
  re-rendered jobs.html
```

Postings are read from **`JobPosting` JSON-LD** over plain HTTP — Lever, Ashby and
employer-hosted Greenhouse all serve it without JavaScript, so no browser is involved.
Greenhouse's own board pages are the exception (see Gotchas) and fall back to the
Greenhouse job API.

| Flag | Effect |
|---|---|
| `--urls FILE` | Posting URLs, one per line; skips the search step entirely |
| `--engine` | `auto` (default), `cse`, `brave`, `browser`, `manual` |
| `--add` | Append verified discovered slugs to `companies.txt`, date-stamped |
| `--merge FILE` | Merge into that `jobs.json` (dedup by URL) and re-render its HTML |
| `--max-queries` | Hard cap on search queries, default 10 |
| `--max-postings` | Cap on postings fetched, default 50 |
| `--sites` | Limit queries to `lever ashby greenhouse workingnomads` |

### Choosing a search engine

| Engine | Needs | Status |
|---|---|---|
| `cse` | `GOOGLE_CSE_KEY` + `GOOGLE_CSE_CX` | **Recommended.** Google's own Programmable Search JSON API, 100 queries/day free, never CAPTCHAs |
| `brave` | `BRAVE_API_KEY` | Brave Search API, free tier available |
| `browser` | `pip install playwright` | Drives your **installed Chrome** (`channel="chrome"`), persistent profile at `~/.cache/job-board-search/chrome-profile`. **Verified to hit a reCAPTCHA on query #1 from this network** |
| `manual` | nothing | Opens each query in Chrome; you copy URLs into a file and use `--urls` |
| `auto` | — | `cse` → `brave` → `manual`, by which credentials exist |

Getting CSE credentials: create a Programmable Search Engine at
`programmablesearchengine.google.com` (set it to search the whole web), then an API key
at `console.cloud.google.com` with the Custom Search API enabled.

**Output** `scraped_jobs.json` (same row schema as `jobs.json`, plus `salary`,
`employment_type` and `apply_url`), new lines in `companies.txt`, optionally a merged
`jobs.json` and re-rendered `jobs.html`.

**Failure modes**
- *No posting URLs* — the run exits naming all four ways to supply them. `auto` with no
  credentials falls through to `manual`, which opens tabs and returns nothing to scrape.
- *Google CAPTCHA on `--engine browser`* — a screenshot is written to `captcha_debug.png`
  and the run stops. **This tool never solves CAPTCHAs.** Either solve it yourself in the
  visible window (the persistent profile remembers it) or switch to `--engine cse`.
- *`skip <url>: no JobPosting JSON-LD and no API fallback`* — that page carries no
  structured data. Logged, skipped, the run continues.
- *`dead <ats>:<slug> (404 on its API)`* — the URL looked like a board but the slug does
  not resolve. Never added.
- *Slug discovery still works when the posting scrape fails* — the two are independent, so
  a page you could not parse can still contribute a company.

---

## Phase 3 — Score the resume

**Goal** A keyword-coverage and parseability read on one resume against one posting.
**Input** A resume (`.pdf`/`.md`/`.txt`) and a JD in a text file.

```bash
python3 -c "
import json;d=json.load(open('jobs.json'))
j=[x for x in d if 'engineer' in x['title'].lower() and len(x['description'])>800][0]
open('jd.txt','w').write(j['title']+'\n'+j['company']+'\n'+j['description'])"

python3 jobs.py score resume.pdf jd.txt --json-out score.json
```

```
ATS SCORE  45/100   needs work

  keyword coverage    32  ██████··············  (13/40 JD terms present)
  quantified impact   14  ██··················  (2/14 bullets carry a number)
  parseability       100  ████████████████████
```

Weighted 55% keyword coverage, 20% quantified impact, 25% parseability, then it lists
missing JD terms and parse flags. Full rubric: [ats-scoring.md](references/ats-scoring.md).

**Output** The report, plus `score.json` with keys
`total, keyword, quantified, parse, missing, matched, flags`.

**This is a heuristic, not Google's ATS.** Google publishes no scorer. Use it to compare
two drafts of the same resume, not as a verdict. Add missing terms **only where truthful** —
the scorer cannot tell a real skill from a planted one; you can.

**Failure modes**
- *Score near 0 with a clean-looking PDF* — image-only scan, no text layer. Check with
  `pdftotext resume.pdf - | wc -w`.
- *`pdftotext: not found`* — `sudo apt-get install -y poppler-utils`.

---

## Phase 4 — Generate form answers

**Goal** A copy/paste sheet for portal fields.
**Input** A profile YAML with `identity`, `status`, `workAuthorization`, `compensation`,
`constraints`, `languages`, `yearsOfExperience`, `references`.

```bash
python3 jobs.py answers profile.yaml -o FORM_ANSWERS.md
```

**Output** `FORM_ANSWERS.md`: identity table, ~25 screener Q&As (work authorisation,
sponsorship, salary in both currencies, years of experience, self-identification,
languages), references, non-compete window, skip rules, upload slots.

**Every value is read from the YAML — nothing is invented.** Absent fields are omitted,
never guessed. Edit the YAML and re-run; never hand-edit the output.

The sheet ends with a **Never auto-fill** list: national ID numbers, CAPTCHAs, live video,
and any screener answer that is not true. Those stay manual, always.

**Failure mode** *Near-empty output* — the YAML uses different top-level key names. The
expected keys are listed above.

---

## Phase 5 — Interview prep

**Goal** A question scaffold for one posting.

```bash
python3 jobs.py prep jd.txt --role "Sr. Forward Deployed Engineer @ Databricks" -o INTERVIEW_PREP.md
```

```
wrote INTERVIEW_PREP.md (1 tech, 6 domain)
```

Splits JD terms into **technical** (matched against a ~90-word vocabulary) and **domain**,
asks a differently-shaped question for each, then five blank STAR frames and four
questions to ask them.

**Output** `INTERVIEW_PREP.md`. The `(1 tech, 6 domain)` count is itself a signal — a JD
light on tech terms is likely a light technical interview.

**Failure mode** *No recognised tech terms* — says so in the file rather than inventing
questions. Read the posting and add your own.

---

## Worked examples

### "Find me remote backend and AI roles I can actually apply to"

```bash
python3 jobs.py search -k "Backend,AI,Machine Learning,Python" \
  -l "remote,anywhere,worldwide" -x "Director,Sales,Recruiter" --open
```
→ `73 jobs (from 3,296 fetched, 0 exact duplicates collapsed)`, opened in Chrome.

### "Does Scale AI use Greenhouse? Add them and search."

```bash
python3 jobs.py companies "Scale AI" --add
python3 jobs.py search --only greenhouse -k "AI,ML,Backend" -l remote
```
→ `ok Scale AI: greenhouse:scaleai (179 postings, NEW)`, appended, then searched.

### "Search job boards for backend roles in Europe"

```bash
python3 jobs.py search -k "Backend,Platform,Infrastructure" \
  -l "Europe,EU,Berlin,Amsterdam,London,Dublin,Remote Europe"
python3 jobs.py dork -k "Backend" -l "Europe,Remote Europe"
```
Location is free-text matching, so **name the cities** — there is no geocoding. Run
`dork` too: WorkingNomads and RemoteOK skew heavily US.

### "Get me 20 recent data engineer jobs from LinkedIn and Indeed"

Not supported. LinkedIn and Indeed have no public jobs API and scraping them breaches
their terms. Do this instead:

```bash
python3 jobs.py search -k "Data Engineer,Analytics Engineer,ETL" -l remote
python3 jobs.py dork -k "Data Engineer" -l "Remote" -e "after:2026-10-01"
```
The `dork` output includes a Google query you open in Chrome and read yourself.

### "Score my CV for this posting and tell me what to fix"

```bash
python3 jobs.py score resume.pdf jd.txt --json-out score.json
```
→ `45/100` — keyword 32, quantified 14, parse 100. The actionable finding is usually
`quantified`: bullets carrying no number. Fix that before chasing keywords.

---

## Limitations / out of scope

**Cannot search:** LinkedIn, Indeed, Glassdoor, FlexJobs, Otta, Wellfound. No public API,
or scraping breaches their terms. `dork` gives you a manual Google query instead.

**Cannot filter by** funding stage ("Series B"), company size, industry, salary band, or
visa-sponsorship status. None of the five APIs expose these. A request like *"jobs at
Series B startups"* needs a funding dataset this tool does not have — resolve the
companies yourself with Phase 0, then search.

**No global ATS search.** Lever/Ashby/Greenhouse coverage equals `companies.txt`. The
current list is 11 companies. This is the single biggest limit on recall.

**No pagination.** RemoteOK caps at 99 results. Treat it as a sample of recent listings.

**Does not submit applications.** It finds postings, scores resumes and prints answers.
Filling and submitting forms stays with a human.

**Google SERP scraping does not work.** Verified: a Playwright-driven Chrome — headless
*and* visible, bundled Chromium *and* your installed Chrome 151 — was served a reCAPTCHA
image grid on **query #1**, not at some rate limit. A plain `requests` GET returns 200 and
92KB with **zero** extractable result URLs. The working paths are the `cse` and `brave`
APIs, or `manual`. Defeating the block would mean bot-detection evasion, which this tool
does not do.

**Playwright is optional and not recommended.** It is only used by `--engine browser`. It
is a heavy dependency, and on the evidence above it buys nothing the CSE API does not do
better. When installed it drives your existing Chrome, so the ~300MB Chromium download is
not required.

**Discovery needs a search engine.** Checked and ruled out: RemoteOK and WorkingNomads
listings contain **0** outbound Lever/Ashby/Greenhouse links (they rewrite apply URLs), so
new slugs cannot be harvested from the global boards for free.

**Never:** solve a CAPTCHA, fabricate a screener answer, or invent a job that was not
returned by a board. Every job in `jobs.json` came from a real API response and carries
its source URL.

---

## Gotchas

- **Substring keyword matching is a trap.** `-k "AI"` matched *available*, *training* and
  *retail*, returning 288 jobs where 70 were real — including "Account Director". Matching
  now uses `(?<![a-z0-9])term(?![a-z0-9])`. Keep the boundaries or precision collapses.
- **Dedup includes location on purpose.** A `(company, title)` key dropped 57 of Stripe's
  728 postings, including one title listed separately in `US-CA` and `Singapore`.
- **Slugs rot.** `lever:voiceflow` and `greenhouse:doordash` both 404'd within weeks. The
  run reports them under `dead sources:`.
- **`ashby:deel` returns 0 postings, not an error.** A zero is not necessarily a bug.
- **Lever dates are epoch milliseconds**, RemoteOK's epoch seconds, the rest ISO.
  `fmt_date()` sniffs by magnitude (`> 1e11` means ms). Raw ints rendered as
  `1791484410` before this was handled.
- **A pipe-separated contact line is not a table.** The parse check flagged
  `phone | email | github | linkedin` and docked 15 points off a clean resume. It now
  needs 3+ pipe rows.
- **`pdftotext` runs without `-layout`** on purpose — `-layout` preserves columns with
  tabs, tripping the very table check you want meaningful.
- **Greenhouse's own board pages carry no JSON-LD.** `job-boards.greenhouse.io/<slug>/jobs/<id>`
  renders client-side and has zero `ld+json`, while the *employer-hosted* version
  (`stripe.com/jobs?gh_jid=...`) has a full `JobPosting`. Both the slug and job id are in
  the URL, so `greenhouse_job_api()` calls the board API instead of rendering the page.
  This took the end-to-end scrape from 4/7 postings to 6/7.
- **A fresh browser profile is the CAPTCHA trigger, not the binary.** Real Chrome 151 was
  blocked identically to bundled Chromium. The persistent profile exists so that a
  consent screen or CAPTCHA you clear once is remembered.
- **Chrome hands off to a running instance.** `google-chrome <url>` opens a tab in your
  existing Chrome and exits, so the URL never appears in `ps`. A clean exit is the only
  confirmation; look at your browser.

---

## Prerequisites

```bash
python3 -c "import requests, yaml" && which pdftotext google-chrome
```

If `pdftotext` is missing (needed only for `score` on a PDF):

```bash
sudo apt-get install -y poppler-utils
```

## Tests

```bash
python3 tests/test_jobs.py && python3 tests/test_scraper.py
```

54 offline tests, no network and no browser. `test_jobs.py` (23): whole-word matching, dedup keying, date sniffing,
`companies.txt` parsing, slug candidates, term extraction, and the three scoring bands.
`test_scraper.py` (31): ATS URL detection for every board shape, JSON-LD extraction
(nested `@graph`, `@type` lists, malformed blocks, salary ranges), SERP parsing, query
building, WorkingNomads/Lever normalisation with mocked HTTP, URL-dedup merging, and the
engine credential guards. Both run under `python3 -m pytest tests/ -q`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `unknown source(s): linkedin` | `--only` takes `lever ashby greenhouse workingnomads remoteok`. LinkedIn is not supported. |
| `WARN companies.txt:2: unknown ATS 'levers'` | Typo. Format is `<ats>:<slug>`, one per line. |
| `HTTPError 404 ... api.lever.co/v0/postings/<slug>` | Dead or wrong slug. Confirm with `python3 jobs.py companies "<Name>"`, then prune. |
| `ValueError: no board` from Ashby | Same, for Ashby. The org name in the URL is the slug, not the display name. |
| `search` returns far more than expected | A short keyword is substring-matching. Confirm you are on the boundary build: `grep -n "(?<!\[a-z0-9\])" jobs.py` hits `_rx`. |
| `0 jobs` with a sane query | `-l` is the strictest filter — drop it first. Remember ATS coverage is only `companies.txt`. |
| `score` crashes on a PDF | `pdftotext` missing → `sudo apt-get install -y poppler-utils`. |
| `answers` writes a near-empty file | YAML key names differ. See Phase 4. |
| `--open` does nothing visible | Chrome opened a background tab, or `google-chrome` is not on PATH. Open `jobs.html` by hand. |
| `scrape` exits "No posting URLs" | `auto` found no search credentials. Set `GOOGLE_CSE_KEY`+`GOOGLE_CSE_CX`, or collect URLs yourself and pass `--urls`. |
| `Google served a CAPTCHA / block page` | Expected for `--engine browser`. See `captcha_debug.png`. Use `--engine cse`. |
| `playwright not installed` | Only `--engine browser` needs it: `pip install playwright`. It will use your installed Chrome. |
| `skip <url>: no JobPosting JSON-LD and no API fallback` | That page has no structured data. Normal; the run continues. |
| Browser pane cannot screenshot `jobs.html` | It refuses `file://`. Serve it: `python3 -m http.server 8891`, then `http://localhost:8891/jobs.html`. |
