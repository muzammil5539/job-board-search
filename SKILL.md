---
name: job-board-search
description: >
  Search Lever, Ashby, Greenhouse, WorkingNomads and RemoteOK for jobs, list them in a
  filterable HTML UI in Chrome, ATS-score a resume against a job description, generate a
  copy/paste form-answer sheet, and build an interview prep sheet. Use for: search jobs,
  find jobs, job board, remote jobs, lever, ashby, greenhouse, workingnomads, remoteok,
  google dork jobs, ATS score, score my resume, resume keywords, form answers, what do I
  put in this field, interview prep, screenshot the job list.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, AskUserQuestion
---

# job-board-search

One driver — `jobs.py` — with five subcommands. No dev server, no framework.
It hits each board's **public JSON API** rather than scraping rendered pages, so it is
fast and does not break on CSS changes. Chrome is used for *viewing* (the HTML UI and
Google dork queries), not for scraping.

Paths below are relative to `~/Desktop/Job Application/`. The driver lives at
`.claude/skills/job-board-search/jobs.py`.

## Prerequisites

Everything needed is already on this machine. Verified present:

```bash
python3 -c "import requests, yaml" && which pdftotext google-chrome
```

If `pdftotext` is missing (needed only for `score` on a PDF):

```bash
sudo apt-get install -y poppler-utils
```

## Run: search a board set

```bash
cd ~/Desktop/Job\ Application
python3 .claude/skills/job-board-search/jobs.py search \
  -k "AI,Machine Learning,LLM,Backend,Fullstack,Full Stack,Python,Forward Deployed" \
  -l "remote,anywhere,worldwide" \
  -x "Director,Account Executive,Sales,Recruiter" \
  --open
```

Writes `jobs.json` + `jobs.html` and opens the HTML in Chrome. Last verified run:
**70 jobs kept from 3,298 fetched**, no dead sources.

- `-k` keywords, OR-matched against title + company + description
- `-l` locations, OR-matched against location + title
- `-x` exclusions, drops any job containing the term
- `--only lever ashby greenhouse workingnomads remoteok` limits sources
- `-o NAME` changes the output basename (default `jobs`)

All three flags match on **whole words**, so `-k "AI"` does not hit *available* or
*training*. Without that, a run returns ~4x the junk — see Gotchas.

The HTML UI has a live text filter, per-source chips, collapsible descriptions and a
**copy for /apply** button per job. It is a single self-contained file: light/dark aware,
no network calls, safe to email or keep.

### Adding companies

Lever, Ashby and Greenhouse are **per-company** boards — there is no global search.
`companies.txt` holds the slugs:

```bash
cat .claude/skills/job-board-search/companies.txt
```

Add one line per company, `<ats>:<slug>`. The slug is the last path segment of a careers
URL: `jobs.lever.co/<slug>`, `jobs.ashbyhq.com/<slug>`, `boards.greenhouse.io/<slug>`.
Dead slugs are **skipped and listed** at the end of the run, not fatal — prune them then.

## Run: Google dork queries

For boards whose API you cannot hit, or to catch postings the APIs miss:

```bash
python3 .claude/skills/job-board-search/jobs.py dork \
  -k "AI,Forward Deployed,Backend,Frontend,Fullstack" \
  -l "Remote,Anywhere" -e "after:2026-10-01" --open
```

Prints one query + Google URL per board, e.g.

```
site:workingnomads.com/jobs ("AI" OR "Forward Deployed" OR "Backend") ("Remote" OR "Anywhere") after:2026-10-01
```

`--open` opens each in Chrome. `-e` appends raw query text (`after:`, `intitle:`, `-intern`).

## Run: ATS-score a resume

```bash
python3 .claude/skills/job-board-search/jobs.py score \
  resume.pdf jd.txt --json-out score.json
```

Both arguments accept `.pdf`, `.md` or `.txt`. Save the JD by pasting the posting into a
text file, or pull one out of a search run:

```bash
python3 -c "
import json;d=json.load(open('jobs.json'))
j=[x for x in d if 'engineer' in x['title'].lower() and len(x['description'])>800][0]
open('jd.txt','w').write(j['title']+'\n'+j['company']+'\n'+j['description'])"
```

Output is a 0-100 score over three weighted bands:

| Band | Weight | What it measures |
|---|---|---|
| keyword coverage | 55% | share of the JD's top-N terms present in the resume |
| quantified impact | 20% | share of bullets carrying a number, %, or $ |
| parseability | 25% | tables/tabs, length, standard headers, contact block |

It then lists **missing JD terms** and **parseability flags**. Feed the missing terms into
the tailoring step — but only where they are *truthful*. The scorer cannot tell a real
skill from a planted keyword; you can.

**This is a documented heuristic, not Google's actual ATS.** Google does not publish a
scorer and no tool can reproduce one. Treat the number as a relative signal between two
drafts of the same resume, not an absolute verdict. Verified behaviour: the same resume
scored **38** against a manager JD and **45** against a matching IC JD — the gap is the
useful part.

## Run: form-answer sheet

```bash
python3 .claude/skills/job-board-search/jobs.py answers \
  data/professional-profile.yaml -o FORM_ANSWERS.md
```

Renders every field from `data/professional-profile.yaml` into a copy/paste markdown
sheet: identity table, ~25 screener Q&As (work authorisation, sponsorship, salary in both
currencies, years of experience, self-identification, languages), references, any
non-compete window, skip rules, and the upload slots.

**Every value is read from the YAML — nothing is invented.** Absent fields are omitted,
never guessed. To change an answer, edit the YAML and re-run; never hand-edit the output,
it is regenerated.

The sheet ends with a **Never auto-fill** list: CNIC, CAPTCHAs, live video, and any
screener answer that is not true. Those stay manual, always.

## Run: interview prep sheet

```bash
python3 .claude/skills/job-board-search/jobs.py prep jd2.txt \
  --role "Sr. Forward Deployed Engineer @ Databricks" -o INTERVIEW_PREP.md
```

Splits the JD's terms into **technical** (matched against a ~90-word tech vocabulary) and
**domain**, asks a differently-shaped question for each, then lays out five blank STAR
frames and four questions to ask them. It prints how many of each it found — `(1 tech,
6 domain)` means the JD was light on tech, which is itself a signal about the interview.

For the STAR content and the full prep framework, use the existing
`~/.claude/skills/job-application-assistant/07-interview-prep.md`. This subcommand builds
the *scaffold*; that file holds the material.

## Gotchas

- **Substring keyword matching is a trap.** `-k "AI"` matched *available*, *training* and
  *retail*, returning 288 jobs where 70 were real — including "Account Director". The
  driver now uses `(?<![a-z0-9])term(?![a-z0-9])`. If you add matching logic, keep the
  boundaries or precision collapses.
- **Lever/Ashby/Greenhouse have no global search.** They are per-company only. Coverage
  equals `companies.txt`. Treat `dork` as the discovery path and the API search as the
  monitoring path for companies you already care about.
- **Slugs rot.** `lever:voiceflow` and `greenhouse:doordash` both 404'd on the seed list
  and were removed. Expect this every few months; the run reports them.
- **`ashby:deel` returns 0 postings, not an error.** The board exists but serves nothing
  through the public GraphQL endpoint. A zero is not necessarily a bug.
- **Lever dates are epoch milliseconds**, RemoteOK's are epoch seconds, the rest are ISO.
  `fmt_date()` sniffs by magnitude (`> 1e11` means ms). Raw ints rendered as
  `1791484410` in the UI before this was handled.
- **A pipe-separated contact line is not a table.** The parseability check flagged
  `phone | email | github | linkedin` as a table layout and docked 15 points off a clean
  resume. It now needs 3+ pipe rows before flagging.
- **`pdftotext` runs without `-layout`** on purpose. `-layout` preserves columns with tabs,
  which then trip the very table check you want to be meaningful.
- **Chrome hands off to a running instance.** `google-chrome <url>` opens a tab in your
  existing Chrome and exits immediately, so the URL never appears in `ps`. A clean exit is
  the only confirmation available; look at your browser.
- **FlexJobs is not supported.** It returns `000` to scripted clients and its listings sit
  behind a paid subscription. `remoteworkjobs.com` is a parked domain that redirects to
  `/lander` — RemoteOK is the working equivalent and is wired in instead.
- **RemoteOK's API terms ask for a link back** if you republish their listings. Fine for
  personal search; relevant if this output ever becomes a public page.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `HTTPError 404 ... api.lever.co/v0/postings/<slug>` | Dead or wrong slug. Open `jobs.lever.co/<slug>` in Chrome; if it 404s there too, delete the line from `companies.txt`. |
| `ValueError: no board` from Ashby | Same thing for Ashby — the org name in the URL is the slug, not the display name. |
| `search` returns far more than expected | A short keyword is substring-matching. Confirm you are on the boundary-matching build: `grep -n "(?<!\[a-z0-9\])" jobs.py` should hit `_rx`. |
| `score` crashes on a PDF | `pdftotext` missing → `sudo apt-get install -y poppler-utils`. On an image-only scan it returns empty text and the score is meaningless; export a text PDF. |
| `answers` writes a near-empty file | The YAML key names changed. It targets `identity`, `status`, `workAuthorization`, `compensation`, `constraints`, `languages`, `yearsOfExperience`, `references`. |
| `--open` does nothing visible | Chrome opened a background tab in an existing window, or `google-chrome` is not on PATH. Open `jobs.html` by hand to confirm the file is good. |
| Browser pane cannot screenshot `jobs.html` | It refuses `file://`. Serve it: `python3 -m http.server 8891` in the output directory, then load `http://localhost:8891/jobs.html`. |

## What this does not do

It does **not** submit applications. Filling and submitting portal forms stays with
`job-application-assistant` and `job-swarm`, which already hold the hard rules
(no CAPTCHA solving, no fabricated screener answers, the correct home city, tailored
resume per application). This skill feeds those: it finds the posting, scores the resume,
and prints the answers to paste.
