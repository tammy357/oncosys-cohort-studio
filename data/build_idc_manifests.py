"""Rebuild the image manifests from NCI Imaging Data Commons (organizers; participants don't need to run this).

Usage (from the repo root):  pip install idc-index  then  python data/build_idc_manifests.py
Writes data/tcga-brca/slide_manifest.csv, data/cptac/slide_manifest.csv, data/cptac/lung_ct_manifest.csv and
data/lidc-idri/patient_manifest.csv. The committed manifests were built with idc-index data version v25 on 2026-11-10;
a newer IDC version may add or change rows.
"""
import re
from idc_index import IDCClient

SLIM = "https://viewer.imaging.datacommons.cancer.gov/slim/studies/{}/series/{}"
OHIF = "https://viewer.imaging.datacommons.cancer.gov/v3/viewer/?StudyInstanceUIDs={}"
KINDS = {"DX": "diagnostic (FFPE)", "TS": "frozen, top section", "BS": "frozen, bottom section", "MS": "frozen, middle section"}

def main():
    c = IDCClient(); c.fetch_index("sm_index"); q = c.sql_query
    print("IDC data version:", c.get_idc_version())
    s = q("""SELECT i.PatientID patient_id, s.ContainerIdentifier slide_barcode, i.StudyInstanceUID study_uid, i.SeriesInstanceUID series_uid,
             ROUND(i.series_size_MB,1) size_mb, s.ObjectiveLensPower objective, i.license_short_name license
             FROM index i JOIN sm_index s USING (SeriesInstanceUID) WHERE i.collection_id='tcga_brca' AND i.Modality='SM' ORDER BY 2""")
    s["sample_id"] = s.slide_barcode.str[:15]
    s["slide_kind"] = s.slide_barcode.str.extract(r"-(DX|TS|BS|MS)\w*$")[0].map(KINDS)
    s["viewer_url"] = [SLIM.format(a, b) for a, b in zip(s.study_uid, s.series_uid)]
    s[["slide_barcode", "sample_id", "patient_id", "slide_kind", "objective", "size_mb", "license", "viewer_url", "study_uid", "series_uid"]].to_csv("data/tcga-brca/slide_manifest.csv", index=False)
    cp = q("""SELECT i.collection_id, i.PatientID case_id, s.ContainerIdentifier slide_id, i.StudyInstanceUID study_uid, i.SeriesInstanceUID series_uid,
              ROUND(i.series_size_MB,1) size_mb, i.license_short_name license FROM index i JOIN sm_index s USING (SeriesInstanceUID)
              WHERE i.collection_id IN ('cptac_luad','cptac_coad','cptac_ccrcc') AND i.Modality='SM' ORDER BY 1,2,3""")
    cp["cohort"] = cp.collection_id.map({"cptac_luad": "lung adenocarcinoma", "cptac_coad": "colon adenocarcinoma", "cptac_ccrcc": "clear cell renal carcinoma"})
    cp["id_format"] = cp.case_id.map(lambda x: "standard (C3L-/C3N-)" if re.match(r"^C3[LN]-\d{5}$", x) else "non-standard")
    cp["viewer_url"] = [SLIM.format(a, b) for a, b in zip(cp.study_uid, cp.series_uid)]
    cp[["cohort", "case_id", "id_format", "slide_id", "size_mb", "license", "viewer_url", "study_uid", "series_uid"]].to_csv("data/cptac/slide_manifest.csv", index=False)
    ct = q("SELECT PatientID case_id, COUNT(DISTINCT SeriesInstanceUID) ct_series, ANY_VALUE(StudyInstanceUID) study_uid FROM index WHERE collection_id='cptac_luad' AND Modality='CT' GROUP BY 1 ORDER BY 1")
    ct["viewer_url"] = [OHIF.format(u) for u in ct.study_uid]
    ct.to_csv("data/cptac/lung_ct_manifest.csv", index=False)
    l = q("""SELECT PatientID patient_id, ANY_VALUE(StudyInstanceUID) study_uid,
             SUM(CASE WHEN Modality='CT' THEN 1 ELSE 0 END) ct_series, ROUND(SUM(CASE WHEN Modality='CT' THEN series_size_MB ELSE 0 END),1) ct_mb,
             SUM(CASE WHEN Modality='SEG' THEN 1 ELSE 0 END) seg_series, SUM(CASE WHEN Modality='SR' THEN 1 ELSE 0 END) sr_series,
             ANY_VALUE(license_short_name) license FROM index WHERE collection_id='lidc_idri' GROUP BY 1 ORDER BY 1""")
    l = l[l.ct_series > 0].copy()
    l["viewer_url"] = [OHIF.format(u) for u in l.study_uid]
    l.to_csv("data/lidc-idri/patient_manifest.csv", index=False)
    print(f"tcga slides {len(s)} · cptac slides {len(cp)} · cptac lung CT {len(ct)} · lidc patients {len(l)}")

if __name__ == "__main__":
    main()
