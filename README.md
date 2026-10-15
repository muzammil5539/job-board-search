# job-board-search

A single-file job search tool and [Claude Code](https://claude.com/claude-code) skill.
Searches **Lever, Ashby, Greenhouse, WorkingNomads and RemoteOK** through their public
JSON APIs, renders the results as a filterable HTML page, resolves a company name to its
job board, and scores a resume against a job description.

No framework, no database, no dev server. `requests` + the standard library.

```bash
python3 jobs.py search -k "AI,Backend,Python" -l "remote,anywhere" --open
```

## Install

```bash
git clone https://github.com/muzammil5539/job-board-search
cd job-board-search
pip install requests pyyaml          # PDF scoring also needs: apt-get install poppler-utils
python3 tests/test_jobs.py           # 23 offline tests, no network
```

As a Claude Code skill, drop the directory into `.claude/skills/` in any project and ask
Claude to "find remote backend jobs" or "score my resume for this posting".

## Subcommands

| Command | Does |
|---|---|
| `companies` | Resolve a company name or careers URL to an `<ats>:<slug>` board |
| `search` | Fetch all boards, filter, write `jobs.json` + a self-contained `jobs.html` |
| `dork` | Build `site:` Google queries per board, optionally open them in Chrome |
| `score` | Heuristic ATS score of a resume against a job description |
| `answers` | Render a copy/paste application-form answer sheet from a profile YAML |
| `prep` | Interview prep sheet (technical + domain questions, STAR frames) from a JD |

```bash
python3 jobs.py companies "Scale AI" --add     # -> greenhouse:scaleai (179 postings, NEW)
python3 jobs.py score resume.pdf jd.txt        # -> ATS SCORE 45/100
```

Keywords match on **whole words** — `-k "AI"` does not hit *available* or *training*.

## Documentation

**[SKILL.md](SKILL.md)** is the full guide: six phases, worked examples, failure modes
per phase, gotchas and troubleshooting.

Deeper reference:
[output schema](references/output-schema.md) ·
[board coverage and limits](references/boards.md) ·
[how the ATS score works](references/ats-scoring.md)

## Limitations

- **No global ATS search.** Lever, Ashby and Greenhouse are per-company; coverage equals
  `companies.txt`. Use `jobs.py companies` to grow it.
- **Cannot search** LinkedIn, Indeed, Glassdoor or FlexJobs — no public API, and scraping
  breaches their terms. `dork` gives you a manual Google query instead.
- **Cannot filter** by funding stage, company size or salary. No API exposes them.
- **RemoteOK caps at 99 results.** No pagination anywhere.
- **The ATS score is a heuristic, not Google's.** Use it to compare two drafts of the same
  resume, not as a verdict.
- **Does not submit applications.** Finding and preparing only.

RemoteOK's API terms request a link back if you republish their listings.

## License

MIT
