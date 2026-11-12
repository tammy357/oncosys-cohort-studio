# Prediction pack: Broad DepMap

**Ready.** [starter.csv](starter.csv) has 1,103 lab-grown cancer cell-line models from [DepMap Public 24Q4 v1](https://doi.org/10.25452/figshare.plus.27993248.v1), about 101 kB. One row is one model. The eight gene columns contain expression on the source log2(TPM+1) scale; the target, `kras_gene_effect`, is the CRISPR knockout gene-effect score. Both were rounded to four decimal places for this extract.

**Start here:** open the CSV or run `python data/depmap/baseline.py` with pandas and scikit-learn. The example compares ridge regression against predicting the training-set mean. It uses 921 training models and 182 holdouts from the Lung and Bowel lineages. Models from donors found in those holdout lineages are excluded from training, and models without donor metadata are omitted. The compact table omits donor IDs and demographic fields. The holdout labels are public practice data, not a blind benchmark.

**Build:** an evaluation tool that compares a model with the mean baseline, checks for model overlap, and shows errors by lineage. Scores describe growth effects in lab models. Do not interpret them as patient outcomes or join them to patient data.

[Source manifest](sources.csv) records the original files and SHA-256 hashes. Run `python data/depmap/build.py` only to rebuild the extract; the three raw downloads total about 936 MB and stay untracked. Credit Broad DepMap 24Q4 Public (2024), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); this challenge subset selects complete model records and eight features, adds a split, and rounds values.
