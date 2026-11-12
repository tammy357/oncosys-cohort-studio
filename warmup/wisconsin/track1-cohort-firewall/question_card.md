# Track 1 · Cohort Firewall — research-question card

**Dataset:** Breast Cancer Wisconsin (Original), UCI, frozen file `data/breast-cancer-wisconsin.data` (check `SHA256SUMS`).

| Field | Value |
|---|---|
| **Intended use** | A data-use reviewer wants to publish one descriptive figure: the share of samples labelled malignant across all eight collection groups (1989–1991). |
| **Group studied** | All 699 sample records in the file. |
| **Outcome of interest** | Class label: 2 = benign, 4 = malignant. |
| **Acceptable limitations** | Small differences (about 1 percentage point) from missing values or duplicate handling, if documented. |
| **Not acceptable** | A headline figure that hides material differences between collection groups, or rests on undocumented assumptions. |
| **Out of scope** | Classifiers, diagnosis, risk scores, or any clinical claim. |

## Your tool must return one verdict

- **Ready:** the data supports the intended use as stated.
- **Conditional:** usable only with specific changes to how the figure is reported or prepared.
- **Blocked:** the data can't support the intended use.
- **Insufficient Evidence:** the tool can't tell, for example because a needed provenance fact can't be confirmed.

For each verdict, show the checks performed, the fields and rows used, the decision rule, and what a person must review before approving.

## Expect a change at judging

Judges will give your tool a changed research question or a changed version of the file. It should re-check rather than repeat its earlier answer.

