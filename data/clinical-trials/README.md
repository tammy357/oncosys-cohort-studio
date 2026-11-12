# Trial records and fictional replay exercise

**Ready for practice:** three real oncology registry records can be fetched, and an entirely fictional two-version fixture can be replayed offline. Nothing here screens patients or represents a real protocol amendment.

Run from the repo root: `python data/clinical-trials/fetch.py`. It checks the response against the recorded SHA-256 and stores it locally under `raw/`. See [sources.csv](sources.csv) for source links and dates. If the registry changes, stop and use the record link for exploration; do not treat a newer version as the frozen response.

- The real records are dated snapshots for exploring source structure and drafting criteria citations. Cite ClinicalTrials.gov and the NCT ID; review the [source terms](https://clinicaltrials.gov/about-site/terms-conditions). No real record is assigned to the fictional cases.
- [fictional_rules.json](fictional_rules.json) and [fictional_cases.csv](fictional_cases.csv) form a separate, organizer-authored workflow fixture. Run the example with `python data/clinical-trials/replay.py`. Compare a 30-day and a 60-day rule, show the clause and date calculation, and route missing, incomplete, or inconsistent dates to review. It is not clinician-reviewed, and historical registry versions are not included.
- A public registry record change is not by itself proof of a formal protocol amendment. Do not add real patient records or claim clinical eligibility.
