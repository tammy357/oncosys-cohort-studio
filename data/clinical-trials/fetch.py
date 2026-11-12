"""Retrieve the public registry records and verify their frozen response hashes."""
import csv
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent

def fetch(manifest, dest):
    dest.mkdir(exist_ok=True)
    for item in csv.DictReader(manifest.open()):
        file = dest / (item["nct_id"] + ".json")
        if file.exists():
            blob = file.read_bytes()
        else:
            with urlopen(item["api_url"], timeout=30) as response:
                blob = response.read()
        if hashlib.sha256(blob).hexdigest() != item["sha256"]:
            raise SystemExit(f"Registry record changed: {item['nct_id']}; ask for a refreshed manifest")
        record = json.loads(blob)["protocolSection"]
        if record["identificationModule"]["nctId"] != item["nct_id"]:
            raise SystemExit(f"Wrong record: {file}")
        file.write_bytes(blob)
        print(item["nct_id"], record["statusModule"]["lastUpdatePostDateStruct"]["date"],
              len(record["eligibilityModule"]["eligibilityCriteria"]))

if __name__ == "__main__":
    fetch(HERE / "sources.csv", HERE / "raw")
