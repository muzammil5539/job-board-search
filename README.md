# job-board-search

A single-file job search tool and [Claude Code](https://claude.com/claude-code) skill.
Searches **Lever, Ashby, Greenhouse, WorkingNomads and RemoteOK** through their public
JSON APIs, renders the results as a filterable HTML page, and scores a resume against a
job description.

No framework, no database, no dev server. `requests` + the standard library.

```bash
python3 jobs.py search -k "AI,Backend,Python" -l "remote,anywhere" --open
```


## Install

```bash
git clone https://github.com/muzammil5539/job-board-search
cd job-board-search
pip install requests pyyaml          # pdftotext for PDF scoring: apt-get install poppler-utils
```

As a Claude Code skill, drop the directory into `.claude/skills/` in any project and ask
Claude to "search jobs" or "score my resume".

## Subcommands

| Command | Does |
|---|---|
| `search` | Fetch all boards, filter, write `jobs.json` + a self-contained `jobs.html` |
| `dork` | Build `site:` Google queries per board, optionally open them in Chrome |
| `score` | Heuristic ATS score of a resume against a job description |
| `answers` | Render a copy/paste application-form answer sheet from a profile YAML |
| `prep` | Interview prep sheet (technical + domain questions, STAR frames) from a JD |

### search

```bash
python3 jobs.py search \
  -k "AI,Machine Learning,LLM,Backend,Fullstack,Python" \
  -l "remote,anywhere,worldwide" \
  -x "Director,Account Executive,Sales,Recruiter" \
  --open
```

Keywords match on **whole words** — `-k "AI"` does not hit *available* or *training*.
A representative run kept 70 jobs out of 3,297 fetched.

The HTML output is one self-contained file: live text filter, per-source chips,
collapsible descriptions, a copy button per job, light/dark aware, zero network calls.

### Company slugs

Lever, Ashby and Greenhouse are **per-company** boards — there is no global search across
them. `companies.txt` holds the list:

```
lever:ro
ashby:ramp
greenhouse:stripe
```

The slug is the last path segment of a careers URL (`jobs.lever.co/<slug>`,
`jobs.ashbyhq.com/<slug>`, `boards.greenhouse.io/<slug>`). Dead slugs are reported at the
end of a run, not fatal.

### score

```bash
python3 jobs.py score resume.pdf jd.txt --json-out score.json
```

```
ATS SCORE  45/100   needs work

  keyword coverage    32  ██████··············  (13/40 JD terms present)
  quantified impact   14  ██··················  (2/14 bullets carry a number)
  parseability       100  ████████████████████
```

Weighted 55% keyword coverage, 20% quantified impact, 25% parseability, then it lists the
missing JD terms and any parse flags.

**This is a documented heuristic, not Google's ATS.** Google publishes no scorer and no
tool can reproduce one. Use it to compare two drafts of the same resume, not as a verdict.
Add missing keywords only where they are true — the scorer cannot tell a real skill from
a planted one.

## Not supported

- **FlexJobs** — returns `000` to scripted clients and requires a paid subscription.
- **remoteworkjobs.com** — a parked domain redirecting to `/lander`. RemoteOK is the
  working equivalent and is wired in instead.
- **Submitting applications.** This tool finds postings and prepares answers. Filling and
  submitting forms is deliberately left to a human.

RemoteOK's API terms request a link back if you republish their listings.

## License

MIT
