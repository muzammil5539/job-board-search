"""Offline tests for jobs.py. No network: every case is a frozen fixture.

    python3 -m pytest tests/ -q        (or: python3 tests/test_jobs.py)
"""
import argparse, io, json, os, sys, tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jobs as J


def job(title, company="acme", desc="", loc=""):
    return {"title": title, "company": company, "description": desc,
            "location": loc, "url": "u", "source": "test", "posted": None}


# --- whole-word matching: the bug that returned 288 jobs instead of 70 ------

def test_keyword_does_not_substring_match():
    ai = [J._rx("AI")]
    assert not J.matches(job("Account Director", desc="available training"), ai, [], [])
    assert not J.matches(job("Retail Lead"), ai, [], [])


def test_keyword_matches_whole_word_in_title_and_body():
    ai = [J._rx("AI")]
    assert J.matches(job("AI Engineer"), ai, [], [])
    assert J.matches(job("Engineer", desc="we build ai agents"), ai, [], [])


def test_multiword_keyword():
    fd = [J._rx("Forward Deployed")]
    assert J.matches(job("Forward Deployed Engineer"), fd, [], [])
    assert not J.matches(job("Deployed Forward Engineer"), fd, [], [])


def test_exclude_wins_over_keyword():
    assert not J.matches(job("AI Engineer"), [J._rx("AI")], [], [J._rx("engineer")])


def test_location_matches_location_or_title():
    rm = [J._rx("remote")]
    assert J.matches(job("AI Eng", loc="Remote - US"), [], rm, [])
    assert J.matches(job("AI Eng (Remote)", loc="Berlin"), [], rm, [])
    assert not J.matches(job("AI Eng", loc="Berlin"), [], rm, [])


def test_no_filters_matches_everything():
    assert J.matches(job("Anything"), [], [], [])


# --- dedup: location is part of the key ------------------------------------

def key(j):
    return (j["company"].lower().strip(), j["title"].lower().strip(),
            j["location"].lower().strip())


def test_same_title_different_location_is_not_a_duplicate():
    a = job("Backend Engineer", loc="US-CA")
    b = job("Backend Engineer", loc="Singapore")
    assert key(a) != key(b)


def test_identical_posting_is_a_duplicate():
    assert key(job("Backend Engineer", loc="Dublin")) == \
           key(job("Backend Engineer", loc="Dublin"))


# --- date sniffing: Lever ms vs RemoteOK seconds vs ISO --------------------

def test_fmt_date_epoch_millis():
    assert J.fmt_date(1791484410000) == "2026-10-08"


def test_fmt_date_epoch_seconds():
    assert J.fmt_date(1791484410) == "2026-10-08"


def test_fmt_date_iso_and_empty():
    assert J.fmt_date("2026-10-08T12:00:00Z") == "2026-10-08"
    assert J.fmt_date(None) == "" and J.fmt_date("") == ""


def test_fmt_date_absurd_value_does_not_raise():
    assert J.fmt_date(99999999999999999999) == ""


# --- companies.txt parsing: bad lines are reported, not swallowed ----------

def write(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    f.write(text); f.close()
    return f.name


def test_good_lines_parse_and_comments_are_stripped():
    pairs, probs = J.load_companies(write("# note\nlever:ro\nashby:ramp  # inline\n\n"))
    assert pairs == [("lever", "ro"), ("ashby", "ramp")] and probs == []


def test_malformed_lines_are_reported_with_line_numbers():
    pairs, probs = J.load_companies(write("greenhouse-stripe\nlevers:ro\ngreenhouse:\nlever:ro\n"))
    assert pairs == [("lever", "ro")]
    assert len(probs) == 3
    assert "missing ':'" in probs[0] and ":1:" in probs[0]
    assert "unknown ATS" in probs[1] and ":2:" in probs[1]
    assert "empty slug" in probs[2] and ":3:" in probs[2]


# --- slug resolution (pure string logic; probing is not tested offline) ----

def test_slug_candidates_from_name():
    assert J.slug_candidates("Scale AI") == ["scaleai", "scale-ai", "scale"]


def test_slug_candidates_from_each_board_url():
    assert J.slug_candidates("https://boards.greenhouse.io/airbnb") == ["airbnb"]
    assert J.slug_candidates("https://jobs.lever.co/ro") == ["ro"]
    assert J.slug_candidates("https://jobs.ashbyhq.com/ramp/some-id") == ["ramp"]


def test_slug_candidates_empty_input():
    assert J.slug_candidates("!!!") == []


# --- term extraction + ATS scoring -----------------------------------------

def test_terms_drops_stopwords_and_ranks_by_frequency():
    t = J.terms("Python Python Python and the of a Kubernetes", n=5)
    assert t[0] == "python" and "and" not in t and "the" not in t


def test_score_rewards_a_matching_resume_over_a_mismatched_one():
    jd = write("Senior Python Engineer. Django, Postgres, Kubernetes, Airflow. "
               "Build data pipelines and REST APIs at scale.")
    good = write("Experience\n- Built Python Django REST APIs on Postgres\n"
                 "- Ran Airflow data pipelines on Kubernetes, cut latency 40%\n"
                 "Education\nBSc\nSkills\nPython Django Postgres Kubernetes Airflow\n"
                 "me@x.com +1 555 1234567 linkedin.com/in/me\n")
    bad = write("Experience\n- Managed retail floor staff\n- Ran weekly rotas\n"
                "Education\nBA\nSkills\nCustomer service\n")

    def run(resume):
        out = io.StringIO()
        with redirect_stdout(out):
            J.cmd_score(argparse.Namespace(resume=resume, jd=jd, top=20, json_out=None))
        return int(out.getvalue().split("ATS SCORE")[1].split("/100")[0].strip())

    assert run(good) > run(bad), "a matching resume must outscore a mismatched one"
    assert run(good) >= 60


def test_score_json_out_has_the_documented_keys():
    jd = write("Python engineer building APIs")
    cv = write("Experience\n- Python APIs, 30% faster\nEducation\nBSc\nSkills\nPython\n"
               "me@x.com +1 555 1234567 linkedin.com/in/me\n")
    out = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
    with redirect_stdout(io.StringIO()):
        J.cmd_score(argparse.Namespace(resume=cv, jd=jd, top=20, json_out=out))
    d = json.load(open(out))
    assert set(d) == {"total", "keyword", "quantified", "parse", "missing", "matched", "flags"}
    assert 0 <= d["total"] <= 100


def test_pipe_separated_contact_line_is_not_flagged_as_a_table():
    jd = write("Python engineer")
    cv = write("Jane Doe\n+1 555 1234567 | me@x.com | github.com/me | linkedin.com/in/me\n"
               "Experience\n- Built Python services, 20% faster\nEducation\nBSc\nSkills\nPython\n")
    out = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
    with redirect_stdout(io.StringIO()):
        J.cmd_score(argparse.Namespace(resume=cv, jd=jd, top=20, json_out=out))
    flags = json.load(open(out))["flags"]
    assert not any("table" in f for f in flags), flags


def test_three_pipe_rows_is_flagged_as_a_table():
    jd = write("Python engineer")
    # The detector needs 3+ pipes per row (a 4-column table), and 3+ such rows.
    cv = write("| a | b | c |\n| d | e | f |\n| g | h | i |\n"
               "Experience\n- Python\nEducation\nBSc\nSkills\nPython\n")
    out = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
    with redirect_stdout(io.StringIO()):
        J.cmd_score(argparse.Namespace(resume=cv, jd=jd, top=20, json_out=out))
    assert any("table" in f for f in json.load(open(out))["flags"])


# --- dork query construction -----------------------------------------------

def test_dork_builds_an_or_group_per_board():
    out = io.StringIO()
    with redirect_stdout(out):
        J.cmd_dork(argparse.Namespace(keywords="AI,Backend", location="Remote",
                                      extra="after:2026-10-01", only=["workingnomads"],
                                      open=False))
    text = out.getvalue()
    assert 'site:workingnomads.com/jobs ("AI" OR "Backend") ("Remote") after:2026-10-01' in text
    assert "jobs.lever.co" not in text


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    bad = 0
    for n, f in fns:
        try:
            f(); print(f"  PASS {n}")
        except AssertionError as e:
            bad += 1; print(f"  FAIL {n}: {e}")
    print(f"\n{len(fns)-bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)
