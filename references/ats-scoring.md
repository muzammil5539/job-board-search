# ATS scoring: what the number means

## It is not Google's ATS

Google publishes no resume scorer, and no third-party tool reproduces one. This is a
documented heuristic with published weights. Its value is **relative**: run it on two
drafts of the same resume against the same JD and trust the direction of change. Do not
read an absolute 45/100 as "this application will fail."

Observed on one real resume: **38** against a manager JD, **45** against a matching IC JD
at the same company. That 7-point gap is the signal.

## The formula

```
total = round(0.55 * keyword + 0.20 * quantified + 0.25 * parse)
```

### keyword (55%)

Share of the JD's top-N terms (default 40, `--top`) that appear anywhere in the resume.
Terms come from `terms()`: words of 3+ chars, stopword-filtered, ranked by frequency in
the JD. Matching is a plain lowercase substring test on the resume text.

**The known weakness:** this rewards planting keywords. The scorer cannot tell a real
skill from a word you pasted in. The `MISSING JD TERMS` list is headed *"add only where
truthful"* for that reason. Treat it as a checklist of things to *check whether you can
honestly claim*, not a list to paste.

### quantified (20%)

Share of bullet lines (lines starting `-`, `*`, `•`) that contain a number, percentage,
currency, or a unit like `k`/`m`/`x`/`hrs`/`users`/`ms`. This is the band most resumes
fail and the easiest to fix honestly: you did the work, you just did not write the
number down.

### parse (25%)

Starts at 100, loses 15 per flag:

| Flag | Trigger |
|---|---|
| table/column layout | A tab character, **or** 3+ lines each containing 3+ pipes |
| length | Outside 400-1100 words |
| missing headers | Fewer than 3 of `experience`, `education`, `skills` present |
| contact block | Fewer than 3 of email / phone / `linkedin.com/in/` present |

The pipe threshold is 3 rows because a single `phone | email | github | linkedin` contact
line is normal formatting, not a table — treating it as one docked 15 points off an
otherwise clean resume.

## PDF extraction

`pdftotext` is called **without** `-layout` on purpose. `-layout` preserves columns using
tab characters, which then trips the very table check that is supposed to detect columns.
Plain mode reads in document order, closer to what a parser sees.

An image-only scanned PDF extracts to empty text and produces a meaningless score. If
`keyword` is 0 and `parse` flags the length, check the PDF has a text layer:
`pdftotext resume.pdf - | wc -w`.

## Tuning

The weights live in `cmd_score` in `jobs.py` as literals. They are unchanged since the
first release and are not configurable by flag. If you change them, the two reference
numbers above (38 / 45) no longer apply.
