"""Offline tests for scraper.py and the scrape pipeline.

No network, no browser: every HTTP call is monkeypatched and every page is a
frozen fixture.  python3 tests/test_scraper.py   (or python3 -m pytest tests/ -q)
"""
import json, os, sys, types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scraper as S
import jobs as J


# ---------------------------------------------------------------- ATS detection

def test_lever_urls():
    assert S.detect_ats_from_url("https://jobs.lever.co/ro") == ("lever", "ro")
    assert S.detect_ats_from_url("https://jobs.lever.co/ro/3910baa9") == ("lever", "ro")


def test_ashby_urls():
    assert S.detect_ats_from_url("https://jobs.ashbyhq.com/ramp") == ("ashby", "ramp")
    assert S.detect_ats_from_url(
        "https://jobs.ashbyhq.com/ramp/1515fe6d-1d8e-475b") == ("ashby", "ramp")


def test_greenhouse_urls_both_hosts_and_embed():
    assert S.detect_ats_from_url("https://boards.greenhouse.io/stripe") == ("greenhouse", "stripe")
    assert S.detect_ats_from_url(
        "https://job-boards.greenhouse.io/databricks/jobs/123") == ("greenhouse", "databricks")
    assert S.detect_ats_from_url(
        "https://boards.greenhouse.io/embed/job_board?for=airbnb") == ("greenhouse", "airbnb")


def test_employer_hosted_greenhouse_has_no_slug():
    assert S.detect_ats_from_url("https://stripe.com/jobs/search?gh_jid=8172508") == \
        ("greenhouse", None)


def test_workingnomads_url():
    assert S.detect_ats_from_url(
        "https://www.workingnomads.com/jobs/go/1944774/") == ("workingnomads", None)
    assert S.detect_ats_from_url("https://www.workingnomads.com/about") == (None, None)


def test_unknown_and_malformed_urls():
    for bad in ["https://example.com/careers", "", None, 12345, "not a url",
                "https://boards.greenhouse.io/embed"]:
        ats, slug = S.detect_ats_from_url(bad)
        assert (ats, slug) != ("greenhouse", "embed"), "reserved segment leaked as a slug"
        if bad in ("https://example.com/careers", "", None, 12345):
            assert (ats, slug) == (None, None), bad


def test_scheme_relative_and_uppercase_host():
    assert S.detect_ats_from_url("JOBS.LEVER.CO/Ro") == ("lever", "ro")


# ---------------------------------------------------------------- JSON-LD

JOB_HTML = """<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting",
 "title":"Senior Backend Engineer",
 "datePosted":"2026-10-01",
 "employmentType":"FULL_TIME",
 "hiringOrganization":{"@type":"Organization","name":"Acme Corp"},
 "jobLocation":[{"@type":"Place","address":{"addressLocality":"Dublin",
   "addressCountry":"IE"}}],
 "jobLocationType":"TELECOMMUTE",
 "baseSalary":{"@type":"MonetaryAmount","currency":"EUR",
   "value":{"minValue":70000,"maxValue":90000,"unitText":"YEAR"}},
 "description":"<p>Build <b>services</b> at scale.</p>"}
</script></head><body>ignored</body></html>"""


def test_jsonld_extracts_core_fields():
    row = S.jobposting_from_html(JOB_HTML, "https://jobs.lever.co/acme/1")
    assert row["title"] == "Senior Backend Engineer"
    assert row["company"] == "Acme Corp"
    assert row["source"] == "lever"
    assert row["posted"] == "2026-10-01"
    assert row["employment_type"] == "FULL_TIME"


def test_jsonld_location_includes_remote_and_dedupes():
    row = S.jobposting_from_html(JOB_HTML, "https://jobs.lever.co/acme/1")
    assert "Dublin" in row["location"] and "Remote" in row["location"]


def test_jsonld_salary_range_formatting():
    assert S.jobposting_from_html(JOB_HTML, "")["salary"] == "EUR 70000-90000 per year"


def test_jsonld_description_is_stripped_of_html():
    d = S.jobposting_from_html(JOB_HTML, "")["description"]
    assert d == "Build services at scale." and "<" not in d


def test_page_without_jobposting_returns_none():
    assert S.jobposting_from_html("<html><body>no ld+json here</body></html>") is None
    assert S.jobposting_from_html(
        '<script type="application/ld+json">{"@type":"Organization"}</script>') is None


def test_malformed_jsonld_does_not_raise():
    assert S.jobposting_from_html('<script type="application/ld+json">{nope</script>') is None


def test_jsonld_inside_graph_is_found():
    h = ('<script type="application/ld+json">{"@graph":[{"@type":"WebSite"},'
         '{"@type":"JobPosting","title":"X","hiringOrganization":"Y"}]}</script>')
    assert S.jobposting_from_html(h)["title"] == "X"


def test_jsonld_type_as_list():
    h = ('<script type="application/ld+json">'
         '{"@type":["JobPosting","Thing"],"title":"Z"}</script>')
    assert S.jobposting_from_html(h)["title"] == "Z"


# ---------------------------------------------------------------- SERP parsing

SERP_HTML = """<div id="search">
<a href="https://jobs.lever.co/acme/abc-123"><h3>Backend Engineer - Acme</h3></a>
<a href="https://www.google.com/preferences"><h3>Settings</h3></a>
<a href="https://jobs.ashbyhq.com/beta/xyz&amp;sa=U"><h3>AI Engineer &amp; Lead</h3></a>
<a href="https://jobs.lever.co/acme/abc-123"><h3>Backend Engineer - Acme</h3></a>
</div>"""


def test_serp_parsing_extracts_results_and_titles():
    r = S.parse_serp_html(SERP_HTML)
    assert [x["url"] for x in r] == ["https://jobs.lever.co/acme/abc-123",
                                     "https://jobs.ashbyhq.com/beta/xyz"]
    assert r[1]["title"] == "AI Engineer & Lead"


def test_serp_parsing_drops_google_internal_links_and_dupes():
    assert all("google.com" not in x["url"] for x in S.parse_serp_html(SERP_HTML))
    assert len(S.parse_serp_html(SERP_HTML)) == 2


def test_serp_parsing_respects_max_results():
    assert len(S.parse_serp_html(SERP_HTML, max_results=1)) == 1


def test_serp_parsing_of_empty_page():
    assert S.parse_serp_html("") == [] and S.parse_serp_html(None) == []


# ---------------------------------------------------------------- query building

def test_build_queries_shape_matches_documented_form():
    qs = dict(S.build_queries("AI,Forward Deployed", "Remote,Anywhere"))
    assert qs["workingnomads"] == (
        'site:workingnomads.com/jobs ("AI" OR "Forward Deployed") ("Remote" OR "Anywhere")')
    assert qs["greenhouse"].startswith("(site:boards.greenhouse.io OR site:job-boards")


def test_build_queries_sites_filter_and_extra():
    qs = S.build_queries("AI", "", "after:2026-10-01", sites=["lever"])
    assert len(qs) == 1 and qs[0][0] == "lever" and qs[0][1].endswith("after:2026-10-01")


# ------------------------------------------------- workingnomads normalisation

class FakeResp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


def test_workingnomads_normalisation():
    payload = [
        {"title": "Remote Python Dev", "company_name": "Acme",
         "location": "Anywhere", "url": "https://workingnomads.com/jobs/go/1",
         "pub_date": "2026-10-01", "description": "<p>Write <b>Python</b></p>"},
        {"title": "", "company_name": "Ghost", "url": "x"},          # dropped: no title
        {"title": "No Location Job", "url": "https://wn/2"},          # defaults to Remote
    ]
    orig = S.requests.get
    S.requests.get = lambda *a, **k: FakeResp(payload)
    try:
        rows = S.scrape_workingnomads()
    finally:
        S.requests.get = orig
    assert len(rows) == 2, "a row with no title must be dropped"
    assert rows[0]["source"] == "workingnomads"
    assert "Python" in rows[0]["description"]
    assert "<" not in rows[0]["description"], "html must be stripped"
    assert rows[1]["location"] == "Remote", "missing location defaults to Remote"


def test_lever_board_normalisation():
    payload = [{"text": "Backend Eng", "categories": {"location": "NYC"},
                "hostedUrl": "https://jobs.lever.co/acme/1", "createdAt": 1791484410000,
                "descriptionPlain": "Build things"}]
    orig = S.requests.get
    S.requests.get = lambda *a, **k: FakeResp(payload)
    try:
        rows = S.scrape_lever_board("acme")
    finally:
        S.requests.get = orig
    assert rows[0] == {"source": "lever", "company": "acme", "title": "Backend Eng",
                       "location": "NYC", "url": "https://jobs.lever.co/acme/1",
                       "posted": 1791484410000, "description": "Build things"}


def test_lever_board_raises_on_dead_slug():
    orig = S.requests.get
    S.requests.get = lambda *a, **k: FakeResp({"ok": False, "error": "Document not found"})
    try:
        try:
            S.scrape_lever_board("nope")
            assert False, "a dead slug must raise, not return []"
        except ValueError as e:
            assert "not found" in str(e).lower()
    finally:
        S.requests.get = orig


# ---------------------------------------------------------------- merge / dedup

def test_merge_dedups_on_url_and_keeps_existing():
    base = [{"url": "a", "title": "old"}, {"url": "b", "title": "two"}]
    new = [{"url": "a", "title": "NEW VERSION"}, {"url": "c", "title": "three"}]
    merged, added = J.merge_by_url(base, new)
    assert added == 1
    assert [m["url"] for m in merged] == ["a", "b", "c"]
    assert merged[0]["title"] == "old", "existing row must win on a url collision"


def test_merge_skips_rows_without_a_url():
    merged, added = J.merge_by_url([], [{"title": "no url"}, {"url": "", "title": "blank"}])
    assert added == 0 and merged == []


def test_merge_into_empty_base():
    merged, added = J.merge_by_url([], [{"url": "a"}, {"url": "a"}, {"url": "b"}])
    assert added == 2 and len(merged) == 2


def test_merged_rows_stay_renderable_by_render_html():
    """scraped rows carry extra keys (salary, employment_type); render_html
    must ignore them rather than KeyError."""
    row = S.jobposting_from_html(JOB_HTML, "https://jobs.lever.co/acme/1")
    merged, _ = J.merge_by_url([], [row])
    out = J.render_html(merged, ["AI"], [], [])
    assert "Senior Backend Engineer" in out and "<!doctype html>" in out.lower()


# ---------------------------------------------------------------- engine guards

def test_unknown_engine_raises():
    try:
        S.google_search("q", engine="bing")
        assert False, "unknown engine must raise"
    except RuntimeError as e:
        assert "bing" in str(e)


def test_browser_engine_without_a_page_raises():
    try:
        S.google_search("q", engine="browser", page=None)
        assert False, "browser engine must require a page"
    except RuntimeError as e:
        assert "page" in str(e).lower()


def test_cse_engine_without_credentials_raises():
    saved = {k: os.environ.pop(k, None) for k in ("GOOGLE_CSE_KEY", "GOOGLE_CSE_CX")}
    try:
        try:
            S.google_search("q", engine="cse")
            assert False, "cse without credentials must raise"
        except RuntimeError as e:
            assert "GOOGLE_CSE_KEY" in str(e)
    finally:
        os.environ.update({k: v for k, v in saved.items() if v})


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    bad = 0
    for n, f in fns:
        try:
            f(); print(f"  PASS {n}")
        except AssertionError as e:
            bad += 1; print(f"  FAIL {n}: {e}")
        except Exception as e:
            bad += 1; print(f"  ERROR {n}: {type(e).__name__} {e}")
    print(f"\n{len(fns)-bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)
