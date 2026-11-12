"""Download the optional LIDC-IDRI CT exercise (5 patients, about 230 MB) with radiologist annotations.

Usage (from the repo root):  pip install idc-index  then  python data/lidc-idri/fetch_exercise.py [--one]
Downloads each patient's CT, nodule segmentations (SEG) and structured reports (SR) from NCI Imaging Data
Commons into data/lidc-idri/raw/<patient>/<modality>/. Use --one to fetch only the smallest patient (34 MB).
Everything else stays at the source: open any patient in the browser from patient_manifest.csv.
"""
import csv, pathlib, sys
from idc_index import IDCClient

HERE = pathlib.Path(__file__).resolve().parent

def main():
    patients = [r["patient_id"] for r in csv.DictReader(open(HERE / "exercise_patients.csv"))]
    if "--one" in sys.argv:
        patients = patients[:1]
    client = IDCClient()
    for p in patients:
        print(f"fetch  {p}")
        client.download_from_selection(collection_id="lidc_idri", patientId=p, downloadDir=str(HERE / "raw"),
                                       dirTemplate="%PatientID/%Modality", quiet=True)
    print(f"ok     {len(patients)} patient(s) in {HERE / 'raw'}")

if __name__ == "__main__":
    main()
