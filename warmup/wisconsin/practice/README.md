# Practice cases

Two derived files for testing how your tool handles provenance. **They aren't the official data.** Use them to test your tool, never in place of `data/breast-cancer-wisconsin.data` in a judged submission. The judging cases are different and stay private until judging.

Both files have a header row and keep `?` for missing values, exactly as in the source. Check them against `practice/SHA256SUMS`.

The question is the same in both cases: **can your tool assign each row to its collection group, and should it?**

---

## P1 · Sorted export that kept row numbers

**Scenario:** A colleague sorted the data by sample code before sharing it. They added a `source_row` column with each row's position in the official UCI file.

**File:** `P1-sorted-with-source-rows/extract.csv` (699 rows)

**Expected behaviour:**
- The tool sees that row order no longer matches the official file, so it **doesn't** assign groups by position.
- It recovers groups from `source_row` using the boundaries in section 4 of `breast-cancer-wisconsin.names`, and records that it did.
- Group-level results match what the starter notebook gives on the official file.
- **Self-check:** if your group shares differ from the official file's, your tool labelled rows by position.

---

## P2 · Cleaned extract with no provenance

**Scenario:** A partner sends a "cleaned" extract and says it came from the Wisconsin collection. It has no row numbers, no checksum, no documentation, and no note on what was cleaned.

**File:** `P2-cleaned-extract-no-provenance/extract.csv` (675 rows)

**Expected behaviour:**
- The row count doesn't match the documented total of 699, so the documented group boundaries can't be applied by position. A tool that does it anyway mislabels many rows.
- For any question that depends on collection groups, the tool returns **Insufficient Evidence** and says what evidence would resolve it: the original row numbers, the partner's cleaning steps, or confirmation of the source version.
- **Allowed, if disclosed:** matching rows against the official file to propose group labels. Treat the result as a hypothesis that needs human confirmation. Report how many rows matched, how many were ambiguous, and which rows the partner appears to have removed.

---

## Recording results

Recording your tool's output in the shared findings format is optional. See [`../examples/findings.example.json`](../examples/findings.example.json), which uses P1 as its example.
