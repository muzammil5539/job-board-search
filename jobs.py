#!/usr/bin/env python3
"""Job board search + ATS scoring + form-answer sheet. One driver, five subcommands.

  search   fetch Lever/Ashby/Greenhouse/WorkingNomads/RemoteOK -> jobs.json + jobs.html
  dork     build Google site: queries per board, optionally open them in Chrome
  score    heuristic ATS score of a resume against a job description
  answers  render a copy/paste form-answer sheet from a profile YAML
  prep     interview prep sheet from a job description

ponytail: no framework, no DB, no async. requests + stdlib. Files on disk are the state.
"""
import argparse, html, json, os, re, subprocess, sys, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) job-board-search/1.0"}
TIMEOUT = 25

# ---------------------------------------------------------------- fetchers

def _get(url, **kw):
    r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


def fetch_lever(slug):
    j = _get(f"https://api.lever.co/v0/postings/{slug}?mode=json").json()
    if not isinstance(j, list):
        raise ValueError(j.get("error", "bad response"))
    return [{
        "source": "lever", "company": slug, "title": p["text"],
        "location": (p.get("categories") or {}).get("location", ""),
        "url": p["hostedUrl"], "posted": p.get("createdAt"),
        "description": re.sub("<[^>]+>", " ", p.get("descriptionPlain") or p.get("description", "")),
    } for p in j]


ASHBY_Q = ("query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) "
           "{ jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) "
           "{ jobPostings { id title locationName employmentType } } }")


def fetch_ashby(slug):
    r = requests.post("https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams",
                      json={"operationName": "ApiJobBoardWithTeams",
                            "variables": {"organizationHostedJobsPageName": slug},
                            "query": ASHBY_Q}, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    board = (r.json().get("data") or {}).get("jobBoard")
    if not board:
        raise ValueError("no board")
    return [{
        "source": "ashby", "company": slug, "title": p["title"],
        "location": p.get("locationName") or "", "posted": None,
        "url": f"https://jobs.ashbyhq.com/{slug}/{p['id']}",
        "description": f"{p['title']} {p.get('employmentType','')}",
    } for p in board["jobPostings"]]


def fetch_greenhouse(slug):
    j = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true").json()
    return [{
        "source": "greenhouse", "company": slug, "title": p["title"],
        "location": (p.get("location") or {}).get("name", ""),
        "url": p["absolute_url"], "posted": p.get("updated_at"),
        "description": re.sub("<[^>]+>", " ", html.unescape(p.get("content", ""))),
    } for p in j.get("jobs", [])]


def fetch_workingnomads(_=None):
    j = _get("https://www.workingnomads.com/api/exposed_jobs/").json()
    return [{
        "source": "workingnomads", "company": p.get("company_name") or "", "title": p["title"],
        "location": p.get("location") or "Remote", "url": p["url"], "posted": p.get("pub_date"),
        "description": re.sub("<[^>]+>", " ", p.get("description", "")),
    } for p in j]


def fetch_remoteok(_=None):
    j = _get("https://remoteok.com/api").json()
    return [{
        "source": "remoteok", "company": p.get("company") or "", "title": p.get("position") or "",
        "location": p.get("location") or "Remote", "url": p.get("url") or "", "posted": p.get("date"),
        "description": re.sub("<[^>]+>", " ", p.get("description", "")) + " " + " ".join(p.get("tags") or []),
    } for p in j if p.get("position")]


ATS = {"lever": fetch_lever, "ashby": fetch_ashby, "greenhouse": fetch_greenhouse}
GLOBAL = {"workingnomads": fetch_workingnomads, "remoteok": fetch_remoteok}


def load_companies(path):
    out = []
    for line in open(path):
        line = line.split("#")[0].strip()
        if ":" in line:
            ats, slug = line.split(":", 1)
            if ats in ATS:
                out.append((ats, slug))
    return out

# ---------------------------------------------------------------- search

def _rx(term):
    """Whole-phrase match. Without \\b, "AI" hits "available" and "training"."""
    return re.compile(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])")


def matches(job, kws, locs, exclude):
    hay = f"{job['title']} {job['company']} {job['description']}".lower()
    if kws and not any(k.search(hay) for k in kws):
        return False
    if exclude and any(x.search(hay) for x in exclude):
        return False
    if locs:
        lhay = f"{job['location']} {job['title']}".lower()
        if not any(l.search(lhay) for l in locs):
            return False
    return True


def cmd_search(a):
    tasks = [(n, f, None) for n, f in GLOBAL.items() if not a.only or n in a.only]
    for ats, slug in load_companies(a.companies):
        if not a.only or ats in a.only:
            tasks.append((f"{ats}:{slug}", ATS[ats], slug))

    jobs, dead = [], []

    def run(t):
        name, fn, arg = t
        try:
            return name, fn(arg) if arg else fn()
        except Exception as e:
            return name, e

    with ThreadPoolExecutor(max_workers=8) as ex:
        for name, res in ex.map(run, tasks):
            if isinstance(res, Exception):
                dead.append(f"{name}: {type(res).__name__} {res}")
            else:
                jobs += res
                print(f"  {name}: {len(res)}", file=sys.stderr)

    split = lambda v: [t.strip() for t in v.split(",") if t.strip()] if v else []
    kws, locs, exc = split(a.keywords), split(a.location), split(a.exclude)
    hits = [j for j in jobs
            if matches(j, [_rx(k) for k in kws], [_rx(l) for l in locs], [_rx(x) for x in exc])]

    seen, uniq = set(), []
    for j in hits:
        key = (j["company"].lower().strip(), j["title"].lower().strip())
        if key not in seen:
            seen.add(key)
            uniq.append(j)
    uniq.sort(key=lambda j: (j["source"], j["company"], j["title"]))

    for j in uniq:
        j["description"] = j["description"][:4000]
    json.dump(uniq, open(a.out + ".json", "w"), indent=1)
    open(a.out + ".html", "w").write(render_html(uniq, kws, locs, dead))
    print(f"\n{len(uniq)} jobs (from {len(jobs)} fetched) -> {a.out}.json / {a.out}.html", file=sys.stderr)
    if dead:
        print("dead sources:\n  " + "\n  ".join(dead), file=sys.stderr)
    if a.open:
        subprocess.Popen(["google-chrome", os.path.abspath(a.out + ".html")],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ---------------------------------------------------------------- html UI

def fmt_date(v):
    """Lever gives epoch ms, RemoteOK an int or ISO string, the rest ISO."""
    if not v:
        return ""
    if isinstance(v, (int, float)) or str(v).isdigit():
        n = float(v)
        try:
            return f"{datetime.fromtimestamp(n / 1000 if n > 1e11 else n, timezone.utc):%Y-%m-%d}"
        except (ValueError, OSError, OverflowError):
            return ""
    return str(v)[:10]


def render_html(jobs, kws, locs, dead):
    e = html.escape
    rows = []
    for i, j in enumerate(jobs):
        snippet = e(" ".join(j["description"].split())[:400])
        rows.append(f"""<article class=j data-s="{e(j['source'])}" data-t="{e((j['title']+' '+j['company']+' '+j['location']).lower())}">
<header><a href="{e(j['url'])}" target=_blank>{e(j['title'])}</a>
<span class=tag>{e(j['source'])}</span></header>
<div class=meta>{e(j['company'])} &middot; {e(j['location'] or 'n/a')}{' &middot; ' + e(fmt_date(j.get('posted'))) if fmt_date(j.get('posted')) else ''}</div>
<details><summary>description</summary><p>{snippet}&hellip;</p></details>
<div class=act><button onclick="cp({i})">copy for /apply</button></div></article>""")
    srcs = sorted({j["source"] for j in jobs})
    chips = "".join(f'<button class=chip onclick="flt(this,\'{s}\')">{s}</button>' for s in srcs)
    return f"""<!doctype html><meta charset=utf-8><title>Job search &middot; {len(jobs)} hits</title>
<style>
:root{{--bg:#fbfaf8;--fg:#1d1b17;--mut:#6b655c;--line:#e3ded5;--card:#fff;--acc:#9a4b1f}}
@media(prefers-color-scheme:dark){{:root{{--bg:#16150f;--fg:#ece7dd;--mut:#9b958a;--line:#2e2b23;--card:#1e1c16;--acc:#d98a55}}}}
*{{box-sizing:border-box}}body{{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);
font:15px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif;max-width:1000px;margin-inline:auto}}
h1{{font-size:21px;margin:0 0 2px}}.sub{{color:var(--mut);font-size:13px;margin-bottom:16px}}
input{{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);font-size:14px}}
.chips{{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 18px}}
.chip,.act button{{border:1px solid var(--line);background:var(--card);color:var(--mut);border-radius:999px;
padding:4px 11px;font-size:12px;cursor:pointer;font-family:inherit}}
.chip.on{{background:var(--acc);border-color:var(--acc);color:#fff}}
.j{{border:1px solid var(--line);border-radius:10px;background:var(--card);padding:14px 16px;margin-bottom:10px}}
.j header{{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}}
.j a{{color:var(--fg);font-weight:600;text-decoration:none;font-size:16px}}.j a:hover{{color:var(--acc)}}
.tag{{font-size:11px;color:var(--mut);border:1px solid var(--line);border-radius:4px;padding:1px 6px}}
.meta{{color:var(--mut);font-size:13px;margin:3px 0 8px}}
summary{{cursor:pointer;color:var(--mut);font-size:13px}}details p{{color:var(--mut);font-size:13px}}
.act{{margin-top:8px}}.dead{{margin-top:28px;color:var(--mut);font-size:12px}}
</style>
<h1>{len(jobs)} jobs</h1>
<div class=sub>{e(', '.join(kws) or 'no keyword filter')}{' &middot; ' + e(', '.join(locs)) if locs else ''}
&middot; {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC</div>
<input id=q placeholder="filter by title, company or location&hellip;" oninput=flt()>
<div class=chips>{chips}</div>
<div id=list>{''.join(rows)}</div>
<div class=dead>{'<b>unreachable sources:</b><br>' + '<br>'.join(map(e, dead)) if dead else ''}</div>
<script>
const D={json.dumps([{k: j[k] for k in ('title','company','location','url','source')} for j in jobs])};
let src=null;
function flt(btn,s){{
  if(btn){{src=src===s?null:s;document.querySelectorAll('.chip').forEach(c=>c.classList.toggle('on',c.textContent===src));}}
  const q=document.getElementById('q').value.toLowerCase();
  document.querySelectorAll('.j').forEach(j=>{{
    j.hidden=(src&&j.dataset.s!==src)||(q&&!j.dataset.t.includes(q));}});
}}
function cp(i){{const j=D[i];navigator.clipboard.writeText(`${{j.title}} at ${{j.company}} (${{j.location}})\\n${{j.url}}`);
event.target.textContent='copied';setTimeout(()=>event.target.textContent='copy for /apply',1200);}}
</script>"""

# ---------------------------------------------------------------- dork

DORKS = {
    "workingnomads": "site:workingnomads.com/jobs",
    "lever": "site:jobs.lever.co",
    "ashby": "site:jobs.ashbyhq.com",
    "greenhouse": "(site:boards.greenhouse.io OR site:job-boards.greenhouse.io)",
    "remoteok": "site:remoteok.com/remote-jobs",
}


def cmd_dork(a):
    def grp(s):
        parts = [p.strip() for p in s.split(",") if p.strip()]
        return "(" + " OR ".join(f'"{p}"' for p in parts) + ")" if parts else ""

    tail = " ".join(x for x in (grp(a.keywords), grp(a.location), a.extra or "") if x)
    for name, site in DORKS.items():
        if a.only and name not in a.only:
            continue
        q = f"{site} {tail}".strip()
        url = "https://www.google.com/search?q=" + urllib.parse.quote(q)
        print(f"\n# {name}\n{q}\n{url}")
        if a.open:
            subprocess.Popen(["google-chrome", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ---------------------------------------------------------------- ATS score

STOP = set("""a an and are as at be by for from has have in is it its of on or that the to with
you your we our will who what how role team work working experience years ability strong
excellent good plus nice must should can able including etc using use used new more most
this that these those while every all any their them they you'll we're across into out up down
other others both each such only also than then when where which while within without
job jobs position candidate candidates apply applicants company companies business
help make take get look looking join joining across one two three some many much very
day days week weeks month months time times way ways thing things people person""".split())
SECTIONS = ["experience", "education", "skills"]


def read_text(path):
    if path.lower().endswith(".pdf"):
        return subprocess.run(["pdftotext", path, "-"],
                              capture_output=True, text=True, check=True).stdout
    return open(path, errors="replace").read()


def terms(text, n=60):
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9+#.\-]{1,24}", text.lower())
    freq = {}
    for w in words:
        w = w.strip(".-")
        if len(w) > 2 and w not in STOP:
            freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:n]]


def cmd_score(a):
    resume, jd = read_text(a.resume), read_text(a.jd)
    rl = resume.lower()
    jt = terms(jd, a.top)
    hit = [t for t in jt if t in rl]
    miss = [t for t in jt if t not in rl]
    kw = round(100 * len(hit) / max(len(jt), 1))

    bullets = [l.strip() for l in resume.splitlines() if re.match(r"^\s*[-•*•]", l)]
    quant = [b for b in bullets if re.search(r"\d+\s*%|\b\d[\d,.]*\s*(k|m|x|hrs?|hours?|users?|ms|s)\b|\$\d", b, re.I)]
    quant_pct = round(100 * len(quant) / max(len(bullets), 1))

    words = len(resume.split())
    sect = [s for s in SECTIONS if re.search(rf"\b{s}\b", rl)]
    contact = sum(bool(re.search(p, resume, re.I)) for p in
                  [r"[\w.+-]+@[\w-]+\.\w+", r"\+?\d[\d\s()-]{7,}", r"linkedin\.com/in/"])
    # One pipe-separated contact line is normal; three+ such lines is a real table.
    pipe_rows = sum(bool(re.search(r"\|.*\|.*\|", l)) for l in resume.splitlines())
    parse_flags = []
    if "\t" in resume or pipe_rows >= 3:
        parse_flags.append(f"table/column layout detected ({pipe_rows} rows) — ATS parsers mangle these")
    if not 400 <= words <= 1100:
        parse_flags.append(f"{words} words (aim 450-900 for one or two pages)")
    if len(sect) < 3:
        parse_flags.append(f"missing standard headers: {', '.join(set(SECTIONS) - set(sect))}")
    if contact < 3:
        parse_flags.append("contact block incomplete (want email + phone + LinkedIn)")

    parse = 100 - 15 * len(parse_flags)
    total = round(0.55 * kw + 0.20 * quant_pct + 0.25 * max(parse, 0))

    def bar(p):
        return "█" * (p // 5) + "·" * (20 - p // 5)

    print(f"""
ATS SCORE  {total}/100   {'strong' if total>=75 else 'workable' if total>=60 else 'needs work'}

  keyword coverage   {kw:3}  {bar(kw)}  ({len(hit)}/{len(jt)} JD terms present)
  quantified impact  {quant_pct:3}  {bar(quant_pct)}  ({len(quant)}/{len(bullets)} bullets carry a number)
  parseability       {max(parse,0):3}  {bar(max(parse,0))}

MISSING JD TERMS (add only where truthful):
  {', '.join(miss[:30]) or '— none —'}

PARSEABILITY FLAGS:
  {chr(10).join('  - ' + f for f in parse_flags) if parse_flags else '  - clean'}

MATCHED: {', '.join(hit[:25])}
""")
    if a.json_out:
        json.dump({"total": total, "keyword": kw, "quantified": quant_pct,
                   "parse": max(parse, 0), "missing": miss, "matched": hit,
                   "flags": parse_flags}, open(a.json_out, "w"), indent=1)

# ---------------------------------------------------------------- answers

def cmd_answers(a):
    """Render the copy/paste sheet from data/professional-profile.yaml.

    Every value is READ from the YAML. Nothing is invented here: if a field is
    absent it is omitted, never guessed. Edit the YAML, re-run, re-paste.
    """
    import yaml
    p = yaml.safe_load(open(a.profile))
    idn = p.get("identity") or {}
    loc = idn.get("location") or {}
    links = idn.get("links") or {}
    sid = idn.get("selfIdentification") or {}
    auth = p.get("workAuthorization") or {}
    comp = p.get("compensation") or {}
    st = p.get("status") or {}
    con = p.get("constraints") or {}

    L = [f"# Form answers — {idn.get('name','(name missing)')}", "",
         f"_Generated {datetime.now():%Y-%m-%d} from `{os.path.basename(a.profile)}`._",
         "_Copy/paste straight into a portal. Re-run after editing the YAML._", "",
         "## Identity", "", "| Field | Paste |", "|---|---|"]
    for label, v in [("Full name", idn.get("name")), ("Email", idn.get("email")),
                     ("Phone", idn.get("phone")), ("WhatsApp", idn.get("phone")),
                     ("Address", loc.get("address")), ("City", loc.get("city")),
                     ("Country", loc.get("country")), ("Date of birth", idn.get("dob")),
                     ("LinkedIn", links.get("linkedin")), ("GitHub", links.get("github")),
                     ("Portfolio", links.get("portfolio"))]:
        if v:
            L.append(f"| {label} | `{v}` |")

    yn = lambda b: "No" if b is False else ("Yes" if b else "")
    primary = auth.get("primary", "")
    sponsor = ", ".join(auth.get("requiresSponsorship") or [])
    pk = comp.get("pakistan") or {}
    intl = comp.get("international") or {}
    rules = comp.get("formRules") or {}
    rate = comp.get("contractPlatforms") or {}

    L += ["", "## Screeners — answer exactly this", "",
          "> Never soften these. A truthful No on an unmet gate is the correct answer.", "",
          "| Question | Answer |", "|---|---|"]
    rows = [("Authorised to work in Pakistan?", "Yes" if "Pakistan" in primary else ""),
            ("Authorised to work in the US / EU / UK?", "No" if sponsor else ""),
            ("Will you now or in future require sponsorship?", "Yes" if sponsor else ""),
            ("Countries requiring sponsorship", sponsor),
            ("Notice period / availability", st.get("availability")),
            ("Currently employed?", st.get("currentRole")),
            ("Willing to relocate?", (con.get("locationPreferences") or [""])[0]),
            ("Open to assessments / background check / drug test?", "Yes"),
            ("Expected salary (Pakistan)",
             f"PKR {pk.get('min'):,}–{pk.get('max'):,} per {pk.get('period')}" if pk.get("min") else ""),
            ("Expected salary (international / remote)",
             f"USD {intl.get('min'):,}–{intl.get('max'):,} per {intl.get('period')}" if intl.get("min") else ""),
            ("Expected salary — single number required (PK)",
             f"PKR {rules['expectedSalarySingleNumberPakistan']:,}" if rules.get("expectedSalarySingleNumberPakistan") else ""),
            ("Current salary", "Prefer not to disclose"),
            ("Current salary — number required",
             f"PKR {rules['currentSalaryRequiredNumber']:,}" if rules.get("currentSalaryRequiredNumber") else ""),
            ("Hourly rate (contract platforms)",
             f"USD {rate.get('rateFloor')}/{rate.get('period')} floor" if rate.get("rateFloor") else ""),
            ("Gender", sid.get("gender")), ("Pronouns", sid.get("pronouns")),
            ("Race / ethnicity", sid.get("ethnicity")),
            ("Hispanic or Latino?", yn(sid.get("hispanic"))),
            ("Veteran status?", yn(sid.get("veteran"))),
            ("Disability?", yn(sid.get("disability")))]
    for yrs_label, yrs_key in [("Years of professional AI/ML experience", "fullTimeAI"),
                               ("Years of production GenAI experience", "productionGenAI"),
                               ("Years of Python experience", "suidhaga")]:
        v = (p.get("yearsOfExperience") or {}).get(yrs_key)
        if v:
            rows.append((yrs_label, v))
    for lg in p.get("languages") or []:
        rows.append((f"Language — {lg['name']}", lg.get("level", "")))
    L += [f"| {q} | **{v}** |" for q, v in rows if v]

    refs = p.get("references") or {}
    if refs:
        L += ["", "## References", "", f"> {refs.get('note','')}", "",
              "| Name | Relationship | Phone | Email |", "|---|---|---|---|"]
        for key in ("primary", "secondary"):
            r = refs.get(key)
            if r:
                L.append(f"| {r.get('name','')} | {r.get('relationship','')} | "
                         f"`{r.get('phone','')}` | `{r.get('email') or 'not available'}` |")

    nc = con.get("nonCompete") or {}
    if nc:
        L += ["", "## Do not apply (non-compete)", "",
              f"Until **{nc.get('until')}** ({nc.get('reason')}): "
              + ", ".join(nc.get("prohibitedIndustries") or [])]
    if con.get("skipRules"):
        L += ["", "## Skip the posting when", ""] + [f"- {r}" for r in con["skipRules"]]

    L += ["", "## Files to upload", "",
          "| Slot | Path |", "|---|---|",
          f"| Photo / headshot | `{idn.get('photo','')}` |" if idn.get("photo") else "",
          "| Resume | tailored PDF for THIS role — never a reused master |",
          "| Cover letter | written for THIS posting |", ""]
    L += ["## Never auto-fill", "",
          "- CNIC / national ID numbers", "- CAPTCHA or \"verify you are human\" checks",
          "- Live video or photo recordings", "- Any screener answer that is not true"]
    open(a.out, "w").write("\n".join(x for x in L if x is not None) + "\n")
    print(f"wrote {a.out}", file=sys.stderr)


# ---------------------------------------------------------------- prep

TECH = set("""python java javascript typescript go golang rust ruby scala kotlin swift c c++ c#
sql nosql postgres postgresql mysql mongodb redis elasticsearch snowflake databricks bigquery
aws gcp azure docker kubernetes terraform ansible jenkins airflow kafka spark hadoop dbt
react nextjs vue angular svelte node nodejs django flask fastapi rails spring graphql rest grpc
pytorch tensorflow keras sklearn scikit-learn pandas numpy llm llms rag genai nlp
langchain langgraph embeddings transformers huggingface openai anthropic finetuning
ci cd git linux microservices api apis etl elt pipelines pipeline streaming distributed
observability prometheus grafana datadog pytest playwright selenium""".split())


def cmd_prep(a):
    jd = read_text(a.jd)
    t = terms(jd, 30)
    tech = [w for w in t if w in TECH]
    domain = [w for w in t if w not in TECH][:6]
    out = [f"# Interview prep — {a.role or 'this role'}", "", "## Technical — expect these", ""]
    if tech:
        out += [f"- Walk me through something you built with **{w}**. "
                "What broke, and how did you find it?" for w in tech[:8]]
    else:
        out.append("- _No recognised tech terms in this JD; read the posting and add your own._")
    if domain:
        out += ["", "## Domain — they will probe these", ""]
        out += [f"- The posting leans on **{w}**. Where have you actually done that, "
                "and what was the outcome?" for w in domain]
    out += ["", "## Behavioural — write one STAR each", ""]
    for q in ["A project that failed or slipped. What did you change after?",
              "Disagreeing with a senior engineer's design call.",
              "Shipping under an unclear spec.",
              "The hardest bug you have debugged.",
              "Teaching or unblocking someone on your team."]:
        out.append(f"### {q}\n- **S:**\n- **T:**\n- **A:**\n- **R:**\n")
    out += ["## Ask them", "",
            "- What does the first 90 days look like for this role?",
            "- How does work reach the team — who decides priority?",
            "- What is the review and deploy cadence?",
            "- What would make you say this hire went well at six months?",
            "", "## JD keywords to land in your answers", "", ", ".join(t)]
    open(a.out, "w").write("\n".join(out) + "\n")
    print(f"wrote {a.out} ({len(tech)} tech, {len(domain)} domain)", file=sys.stderr)


# ---------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="fetch all boards -> jobs.json + jobs.html")
    s.add_argument("-k", "--keywords", help="comma list, OR-matched against title+company+description")
    s.add_argument("-l", "--location", help="comma list, OR-matched against location+title")
    s.add_argument("-x", "--exclude", help="comma list, drops any job containing these")
    s.add_argument("--only", nargs="*", help="limit to sources: lever ashby greenhouse workingnomads remoteok")
    s.add_argument("--companies", default=os.path.join(HERE, "companies.txt"))
    s.add_argument("-o", "--out", default="jobs")
    s.add_argument("--open", action="store_true", help="open the HTML in Chrome")
    s.set_defaults(fn=cmd_search)

    d = sub.add_parser("dork", help="build Google site: queries per board")
    d.add_argument("-k", "--keywords", default="")
    d.add_argument("-l", "--location", default="")
    d.add_argument("-e", "--extra", help="raw extra query text, e.g. after:2026-10-01")
    d.add_argument("--only", nargs="*")
    d.add_argument("--open", action="store_true", help="open each query in Chrome")
    d.set_defaults(fn=cmd_dork)

    c = sub.add_parser("score", help="ATS-score a resume against a job description")
    c.add_argument("resume", help=".pdf / .md / .txt")
    c.add_argument("jd", help=".pdf / .md / .txt holding the job description")
    c.add_argument("--top", type=int, default=40, help="how many JD terms to weigh")
    c.add_argument("--json-out")
    c.set_defaults(fn=cmd_score)

    w = sub.add_parser("answers", help="render the copy/paste form-answer sheet")
    w.add_argument("profile", help="profile YAML")
    w.add_argument("-o", "--out", default="FORM_ANSWERS.md")
    w.set_defaults(fn=cmd_answers)

    p = sub.add_parser("prep", help="interview prep sheet from a job description")
    p.add_argument("jd")
    p.add_argument("--role")
    p.add_argument("-o", "--out", default="INTERVIEW_PREP.md")
    p.set_defaults(fn=cmd_prep)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
