"""Discovery and posting extraction for job-board-search.

Two empirical findings shape this module, both verifiable with the probes in
tests/test_scraper.py and the commands in SKILL.md:

1. Lever, Ashby and employer-hosted Greenhouse posting pages all serve a
   `JobPosting` JSON-LD block to a plain HTTP GET. No JavaScript rendering is
   required, so `scrape_job_posting()` needs no browser.
2. Google's SERP markup does not expose result hrefs to a plain GET (200 OK,
   92KB, zero extractable URLs). Programmatic search therefore goes through a
   search API, not through parsing google.com/search.

`playwright` is imported lazily and only by the `browser` engine. Every other
code path in this file runs on `requests` + the standard library.
"""
import html as htmllib
import json
import os
import random
import re
import subprocess
import time
import urllib.parse

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
TIMEOUT = 25

# Google allows 10 queries/run by policy of this tool, spaced out. See SKILL.md.
MAX_QUERIES_PER_RUN = 10
QUERY_DELAY = (5.0, 10.0)      # seconds between search queries
NAV_DELAY = (1.0, 3.0)         # seconds between posting fetches


def _nap(span):
    time.sleep(random.uniform(*span))


# ---------------------------------------------------------------- ATS detection

# Path segments that are never a company slug.
_RESERVED = {"embed", "search", "jobs", "api", "static", "assets", ""}

_ATS_PATTERNS = [
    ("lever",      re.compile(r"^(?:www\.)?jobs\.lever\.co$"),                     1),
    ("ashby",      re.compile(r"^(?:www\.)?jobs\.ashbyhq\.com$"),                  1),
    ("greenhouse", re.compile(r"^(?:www\.)?(?:boards|job-boards)\.greenhouse\.io$"), 1),
]


def detect_ats_from_url(url):
    """Return (ats_name, slug), or (None, None) when the URL is not a known board.

    Handles the four shapes seen in the wild:
        jobs.lever.co/<slug>[/<id>]
        jobs.ashbyhq.com/<slug>[/<uuid>]
        boards.greenhouse.io/<slug>  |  job-boards.greenhouse.io/<slug>
        boards.greenhouse.io/embed/job_board?for=<slug>
    WorkingNomads has no per-company board, so it returns a slug of None.
    An employer-hosted Greenhouse posting (stripe.com/jobs?gh_jid=...) is
    identifiable as greenhouse but carries no slug.
    """
    if not url or not isinstance(url, str):
        return (None, None)
    try:
        p = urllib.parse.urlsplit(url if "//" in url else "https://" + url)
    except ValueError:
        return (None, None)
    host = (p.netloc or "").lower().split(":")[0]
    parts = [seg for seg in (p.path or "").split("/") if seg]
    qs = urllib.parse.parse_qs(p.query or "")

    if host.endswith("workingnomads.com"):
        return ("workingnomads", None) if parts and parts[0] == "jobs" else (None, None)

    for name, host_rx, idx in _ATS_PATTERNS:
        if not host_rx.match(host):
            continue
        # boards.greenhouse.io/embed/job_board?for=<slug>
        if qs.get("for"):
            return (name, qs["for"][0].lower())
        if len(parts) >= idx and parts[idx - 1].lower() not in _RESERVED:
            return (name, parts[idx - 1].lower())
        return (name, None)

    # Employer-hosted Greenhouse: identified by the gh_jid parameter.
    if qs.get("gh_jid"):
        return ("greenhouse", None)
    return (None, None)


# ---------------------------------------------------------------- JSON-LD

_LD_RX = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.S | re.I)


def extract_jsonld(page_html):
    """Every parseable ld+json object on the page, flattened out of @graph/lists."""
    found = []
    for block in _LD_RX.findall(page_html or ""):
        try:
            data = json.loads(block.strip())
        except (ValueError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("@graph"), list):
                stack.extend(item["@graph"])
                continue
            found.append(item)
    return found


def _is_jobposting(obj):
    t = obj.get("@type")
    return t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t)


def _text(v):
    """JSON-LD fields arrive as str, dict, or list depending on the board."""
    if v is None:
        return ""
    if isinstance(v, str):
        return re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", htmllib.unescape(v))).strip()
    if isinstance(v, dict):
        for k in ("name", "value", "@value", "description"):
            if v.get(k):
                return _text(v[k])
        return ""
    if isinstance(v, list):
        return ", ".join(x for x in (_text(i) for i in v) if x)
    return str(v)


def _location(jp):
    loc = jp.get("jobLocation")
    items = loc if isinstance(loc, list) else [loc]
    out = []
    for it in items:
        if not isinstance(it, dict):
            out.append(_text(it))
            continue
        addr = it.get("address") if isinstance(it.get("address"), dict) else it
        bits = [_text(addr.get(k)) for k in
                ("addressLocality", "addressRegion", "addressCountry")]
        joined = ", ".join(b for b in bits if b)
        out.append(joined or _text(it.get("name")))
    if jp.get("jobLocationType") == "TELECOMMUTE":
        out.append("Remote")
    seen, uniq = set(), []
    for x in out:
        if x and x.lower() not in seen:
            seen.add(x.lower())
            uniq.append(x)
    return " / ".join(uniq)


def _salary(jp):
    bs = jp.get("baseSalary")
    if not isinstance(bs, dict):
        return _text(bs)
    val = bs.get("value") if isinstance(bs.get("value"), dict) else {}
    lo, hi = val.get("minValue"), val.get("maxValue")
    one = val.get("value")
    cur = _text(bs.get("currency") or val.get("currency"))
    unit = _text(val.get("unitText"))
    amount = (f"{lo}-{hi}" if lo and hi else str(one or lo or hi or "")).strip()
    if not amount:
        return ""
    return " ".join(x for x in (cur, amount, f"per {unit.lower()}" if unit else "") if x)


def jobposting_from_html(page_html, url=""):
    """Normalise the first JobPosting block into the jobs.json row shape.

    Returns None when the page carries no JobPosting. Extra keys (salary,
    employment_type, apply_url) ride along; render_html() ignores unknown keys,
    so a merged dataset stays renderable.
    """
    jp = next((o for o in extract_jsonld(page_html) if _is_jobposting(o)), None)
    if jp is None:
        return None
    ats, slug = detect_ats_from_url(url)
    org = jp.get("hiringOrganization")
    company = _text(org.get("name") if isinstance(org, dict) else org) or slug or ""
    return {
        "source": ats or "scrape",
        "company": company,
        "title": _text(jp.get("title")),
        "location": _location(jp),
        "url": _text(jp.get("url")) or url,
        "posted": jp.get("datePosted"),
        "description": _text(jp.get("description"))[:4000],
        "salary": _salary(jp),
        "employment_type": _text(jp.get("employmentType")),
        "apply_url": _text((jp.get("applicationContact") or {}).get("url")
                           if isinstance(jp.get("applicationContact"), dict) else "") or url,
    }


_GH_JOB_RX = re.compile(
    r"^(?:www\.)?(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]+)/jobs/(\d+)")


def greenhouse_job_api(url):
    """Greenhouse's OWN board pages render client-side and carry no JSON-LD
    (employer-hosted ones do). The slug and job id are both in the URL, so the
    board API answers directly - better than rendering the page in a browser.
    """
    p = urllib.parse.urlsplit(url)
    m = _GH_JOB_RX.match((p.netloc or "").lower() + (p.path or ""))
    if not m:
        return None
    slug, jid = m.groups()
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}",
                     headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    return {
        "source": "greenhouse",
        "company": slug,
        "title": d.get("title") or "",
        "location": (d.get("location") or {}).get("name", ""),
        "url": d.get("absolute_url") or url,
        "posted": d.get("updated_at") or d.get("first_published"),
        "description": _text(d.get("content", ""))[:4000],
        "salary": "",
        "employment_type": "",
        "apply_url": d.get("absolute_url") or url,
    }


def scrape_job_posting(url, page=None, session=None):
    """Fetch one posting and return its normalised row, or None.

    `page` is an optional Playwright page. It is NOT needed for Lever, Ashby or
    employer-hosted Greenhouse, all of which serve JSON-LD to a plain GET; pass
    one only for a board that genuinely requires JavaScript.
    """
    try:
        if page is not None:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            body = page.content()
        else:
            r = (session or requests).get(url, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            body = r.text
    except Exception as e:
        print(f"    skip {url}: {type(e).__name__} {e}")
        return None
    row = jobposting_from_html(body, url)
    if row is None:
        try:
            row = greenhouse_job_api(url)
        except Exception:
            row = None
        if row is None:
            print(f"    skip {url}: no JobPosting JSON-LD and no API fallback")
    return row


# ---------------------------------------------------------------- board fetchers


def scrape_workingnomads():
    """The exposed_jobs JSON API. No scraping, no browser."""
    r = requests.get("https://www.workingnomads.com/api/exposed_jobs/",
                     headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return [{
        "source": "workingnomads",
        "company": p.get("company_name") or "",
        "title": p.get("title") or "",
        "location": p.get("location") or "Remote",
        "url": p.get("url") or "",
        "posted": p.get("pub_date"),
        "description": re.sub("<[^>]+>", " ", p.get("description", ""))[:4000],
    } for p in r.json() if p.get("title")]


def scrape_lever_board(slug):
    """Full Lever board for one slug. Raises on a dead slug (404)."""
    r = requests.get(f"https://api.lever.co/v0/postings/{slug}?mode=json",
                     headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise ValueError(data.get("error", "unexpected response"))
    return [{
        "source": "lever",
        "company": slug,
        "title": p.get("text") or "",
        "location": (p.get("categories") or {}).get("location", ""),
        "url": p.get("hostedUrl") or "",
        "posted": p.get("createdAt"),
        "description": re.sub("<[^>]+>", " ",
                              p.get("descriptionPlain") or p.get("description", ""))[:4000],
    } for p in data]


# ---------------------------------------------------------------- search


def build_queries(keywords, locations="", extra="", sites=None):
    """site:-restricted query per board, in the shape SKILL.md documents."""
    def grp(s):
        parts = [p.strip() for p in (s or "").split(",") if p.strip()]
        return "(" + " OR ".join(f'"{p}"' for p in parts) + ")" if parts else ""

    tail = " ".join(x for x in (grp(keywords), grp(locations), extra or "") if x)
    default = {
        "workingnomads": "site:workingnomads.com/jobs",
        "lever": "site:jobs.lever.co",
        "ashby": "site:jobs.ashbyhq.com",
        "greenhouse": "(site:boards.greenhouse.io OR site:job-boards.greenhouse.io)",
    }
    return [(name, f"{site} {tail}".strip())
            for name, site in default.items() if not sites or name in sites]


def parse_serp_html(page_html, max_results=30):
    """Pull {title, url, snippet} out of rendered SERP HTML.

    Only used by the `browser` engine, where a real browser has executed the
    page's JavaScript. A plain GET of google.com/search returns markup with no
    extractable result hrefs, which is why the API engines exist.
    """
    results, seen = [], set()
    # Rendered results are <a href="http..."><h3>Title</h3></a>
    for m in re.finditer(
            r'<a[^>]+href="(https?://[^"#]+)"[^>]*>.*?<h3[^>]*>(.*?)</h3>',
            page_html or "", re.S | re.I):
        url, title = m.group(1), re.sub("<[^>]+>", "", m.group(2))
        if "google.com" in urllib.parse.urlsplit(url).netloc:
            continue
        url = url.split("&")[0]
        if url in seen:
            continue
        seen.add(url)
        results.append({"title": htmllib.unescape(title).strip(),
                        "url": url, "snippet": ""})
        if len(results) >= max_results:
            break
    return results


def _search_cse(query, max_results, key, cx):
    """Google Programmable Search JSON API - Google's own endpoint for this."""
    out = []
    for start in range(1, min(max_results, 100), 10):
        r = requests.get("https://www.googleapis.com/customsearch/v1",
                         params={"key": key, "cx": cx, "q": query,
                                 "start": start, "num": 10},
                         headers=UA, timeout=TIMEOUT)
        if r.status_code == 429:
            print("    CSE daily quota exhausted (100/day on the free tier)")
            break
        r.raise_for_status()
        items = r.json().get("items") or []
        out += [{"title": i.get("title", ""), "url": i.get("link", ""),
                 "snippet": i.get("snippet", "")} for i in items]
        if len(items) < 10:
            break
    return out[:max_results]


def _search_brave(query, max_results, key):
    r = requests.get("https://api.search.brave.com/res/v1/web/search",
                     params={"q": query, "count": min(max_results, 20)},
                     headers={**UA, "X-Subscription-Token": key,
                              "Accept": "application/json"}, timeout=TIMEOUT)
    r.raise_for_status()
    return [{"title": i.get("title", ""), "url": i.get("url", ""),
             "snippet": re.sub("<[^>]+>", "", i.get("description", ""))}
            for i in (r.json().get("web", {}).get("results") or [])][:max_results]


def _search_browser(query, max_results, page):
    """Visible-browser search. Requires a caller-supplied Playwright page.

    No bot-detection evasion is performed. Google may serve a consent wall or a
    CAPTCHA; both are handed back to you with a screenshot rather than defeated.
    """
    url = "https://www.google.com/search?q=" + urllib.parse.quote(query)
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_selector("div#search, div#rso, form[action*='sorry']",
                               timeout=15000)
    except Exception:
        pass
    body = page.content()
    if "/sorry/" in page.url or "unusual traffic" in body.lower():
        shot = os.path.abspath("captcha_debug.png")
        try:
            page.screenshot(path=shot, full_page=True)
        except Exception:
            pass
        raise RuntimeError(
            f"Google served a CAPTCHA / block page. Screenshot: {shot}\n"
            "    This tool does not solve CAPTCHAs. Either solve it yourself in "
            "the open browser window and re-run, or switch to --engine cse "
            "(Google's own Programmable Search JSON API), which is not rate-blocked.")
    return parse_serp_html(body, max_results)


def google_search(query, max_results=30, engine="auto", page=None):
    """Return [{title, url, snippet}] for one site:-restricted query.

    engines:
      cse      Google Programmable Search JSON API  (GOOGLE_CSE_KEY + GOOGLE_CSE_CX)
      brave    Brave Search API                     (BRAVE_API_KEY)
      browser  visible Playwright Chromium, no evasion, CAPTCHA hands back
      manual   open the query in Chrome for you to read; returns []
      auto     cse -> brave -> manual, by which credentials are present
    """
    key, cx, brave = (os.environ.get("GOOGLE_CSE_KEY"),
                      os.environ.get("GOOGLE_CSE_CX"),
                      os.environ.get("BRAVE_API_KEY"))
    if engine == "auto":
        engine = "cse" if (key and cx) else "brave" if brave else "manual"

    if engine == "cse":
        if not (key and cx):
            raise RuntimeError("--engine cse needs GOOGLE_CSE_KEY and GOOGLE_CSE_CX")
        return _search_cse(query, max_results, key, cx)
    if engine == "brave":
        if not brave:
            raise RuntimeError("--engine brave needs BRAVE_API_KEY")
        return _search_brave(query, max_results, brave)
    if engine == "browser":
        if page is None:
            raise RuntimeError("--engine browser needs a Playwright page (see open_browser)")
        return _search_browser(query, max_results, page)
    if engine == "manual":
        subprocess.Popen(["google-chrome",
                          "https://www.google.com/search?q=" + urllib.parse.quote(query)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return []
    raise RuntimeError(f"unknown engine {engine!r}")


class open_browser:
    """Context manager yielding a page driven by your INSTALLED Google Chrome.

    channel="chrome" drives /usr/bin/google-chrome, not Playwright's bundled
    Chromium. `profile_dir` gives Chrome a persistent profile, so a consent
    screen you clear or an account you sign into once is remembered on later
    runs - the same state a human browser accumulates.

    No bot-detection evasion is performed here. Playwright is imported in this
    class and nowhere else, so the rest of the tool runs without it installed.
    """

    DEFAULT_PROFILE = os.path.expanduser("~/.cache/job-board-search/chrome-profile")

    def __init__(self, headless=False, profile_dir=None, channel="chrome"):
        self.headless = headless
        self.profile_dir = profile_dir
        self.channel = channel
        self._pw = self._browser = self._ctx = None

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("  playwright not installed - run: pip install playwright\n"
                  "  (channel='chrome' uses your installed Chrome; no chromium "
                  "download needed)")
            return None
        self._pw = sync_playwright().start()
        opts = {"headless": self.headless, "channel": self.channel}
        try:
            if self.profile_dir:
                os.makedirs(self.profile_dir, exist_ok=True)
                self._ctx = self._pw.chromium.launch_persistent_context(
                    self.profile_dir, viewport={"width": 1400, "height": 900}, **opts)
                return self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
            self._browser = self._pw.chromium.launch(**opts)
            self._ctx = self._browser.new_context(
                user_agent=UA["User-Agent"], viewport={"width": 1400, "height": 900})
            return self._ctx.new_page()
        except Exception as e:
            print(f"  could not launch Chrome (channel={self.channel!r}): {e}")
            self.__exit__()
            return None

    def __exit__(self, *exc):
        for obj, meth in ((self._ctx, "close"), (self._browser, "close"),
                          (self._pw, "stop")):
            try:
                if obj is not None:
                    getattr(obj, meth)()
            except Exception:
                pass
        return False
