#!/usr/bin/env python3
"""Import IMD's daily district rainfall report for Maharashtra's 36 districts.

IMD overwrites one PDF URL every day, so each import stores a dated copy, its
SHA256 and the report's own DAY/PERIOD dates. Text comes from `pdftotext -raw`
(xpdf/poppler); `-layout` was checked and scrambles this report's columns.
Validation failures exit before any output is replaced, so the last good
snapshot stays published. No third-party Python packages are needed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
# Overridable so the public site repository can refresh its own copy (see its workflow).
PUBLIC = Path(os.environ.get("HUB_PUBLIC_DIR") or APP / "public")
WORK_ROOT = Path(os.environ.get("HUB_WORK_DIR") or APP / "outputs")
OUTPUT = PUBLIC / "data" / "rainfall"
WORK = WORK_ROOT / "rainfall"
SOURCE_PAGE = "https://mausam.imd.gov.in/imd_latest/contents/rainfall_statistics_3.php"
PDF_URL = "https://mausam.imd.gov.in/Rainfall/DISTRICT_RAINFALL_DISTRIBUTION_COUNTRY_INDIA_cd.pdf"
DEPARTURE_TOLERANCE = 1.5  # percentage points; IMD prints whole-number departures
INDEX = "index.json"

# IMD subdivision rows that hold Maharashtra districts. Konkan & Goa also lists
# the Goa districts, which are skipped by name below.
SUBDIVISIONS = {"KONKAN & GOA", "MADHYA MAHARASHTRA", "MARATHWADA", "VIDARBHA"}
NON_MAHARASHTRA = {"GOA", "NORTH GOA", "SOUTH GOA"}
# IMD district label -> LGD district code used by the hub.
DISTRICTS = {
    "MUMBAI CITY": "482", "MUMBAI SUBURBAN": "483", "PALGHAR": "665", "RAIGAD": "491",
    "RATNAGIRI": "492", "SINDHUDURG": "495", "THANE": "497",
    "AHMEDNAGAR": "466", "DHULE": "474", "JALGAON": "478", "KOLHAPUR": "480",
    "NANDURBAR": "486", "NASHIK": "487", "PUNE": "490", "SANGLI": "493", "SATARA": "494",
    "SHOLAPUR": "496",
    "BEED": "470", "CHHATRAPATI SAMBHAJINAGAR": "469", "DHARASHIV": "488", "HINGOLI": "477",
    "JALNA": "479", "LATUR": "481", "NANDED": "485", "PARBHANI": "489",
    "AKOLA": "467", "AMRAOTI": "468", "BHANDARA": "471", "BULDHANA": "472", "CHANDRAPUR": "473",
    "GADCHIROLI": "475", "GONDIA": "476", "NAGPUR": "484", "WARDHA": "498", "WASHIM": "499",
    "YEOTMAL": "500",
}
CATEGORIES = {
    "LE": ("Large excess", "खूप जास्त"), "E": ("Excess", "जास्त"), "N": ("Normal", "सामान्य"),
    "D": ("Deficient", "कमी"), "LD": ("Large deficient", "खूप कमी"),
    "NR": ("No rain", "पाऊस नाही"), "ND": ("No data", "माहिती नाही"),
}
HEADER = re.compile(r"DAY:\s*(\d{2}-\d{2}-\d{4})\s+PERIOD:\s*(\d{2}-\d{2}-\d{4})\s+to\s+(\d{2}-\d{2}-\d{4})")
ROW = re.compile(r"^(?:(\d+) )?([A-Z&().-]+(?: [A-Z&().-]+)*) (-?\d.*)$")
NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
PERCENT = re.compile(r"^-?\d+%$")


def iso(day_month_year: str) -> str:
    return datetime.strptime(day_month_year, "%d-%m-%Y").date().isoformat()


def parse_block(tokens: list[str]) -> dict:
    """Parse one ACTUAL/NORMAL/%DEP/CAT block; IMD leaves cells blank for ND."""
    if not tokens or tokens[-1] not in CATEGORIES:
        raise ValueError(f"block does not end with a category: {tokens}")
    category, cells = tokens[-1], tokens[:-1]
    departure = int(cells.pop()[:-1]) if cells and PERCENT.match(cells[-1]) else None
    if not all(NUMBER.match(c) for c in cells) or len(cells) > 2:
        raise ValueError(f"unexpected cells: {tokens}")
    if len(cells) == 2:
        actual, normal = float(cells[0]), float(cells[1])
    elif len(cells) == 1 and category == "ND":
        actual, normal = None, float(cells[0])
    else:
        raise ValueError(f"cannot assign cells: {tokens}")
    return {"actualMm": actual, "normalMm": normal, "departurePercent": departure, "category": category}


def parse_values(text: str) -> tuple[dict, dict]:
    tokens = text.split()
    first = next((i for i, t in enumerate(tokens) if t in CATEGORIES), None)
    if first is None:
        raise ValueError(f"no rainfall category in row values: {text}")
    return parse_block(tokens[:first + 1]), parse_block(tokens[first + 1:])


def table_rows(text: str):
    """Yield (serial, name, values) for each data row; serial is None for totals."""
    for line in text.splitlines():
        match = ROW.match(" ".join(line.split()))
        if match:
            yield match.groups()


def add_district(rows: dict, subdivision: str, name: str, values: str) -> None:
    if name not in DISTRICTS:
        raise ValueError(f"unmapped IMD district under {subdivision}: {name}")
    if name in rows:
        raise ValueError(f"duplicate IMD district row: {name}")
    day_values, period_values = parse_values(values)
    rows[name] = {"imdName": name, "subdivision": subdivision, "day": day_values, "period": period_values}


def parse_report(text: str) -> dict:
    headers = set(HEADER.findall(text))
    if len(headers) != 1:
        raise ValueError(f"expected one DAY/PERIOD header, found {headers}")
    day, start, end = next(iter(headers))
    state, rows, subdivision = None, {}, None
    for serial, name, values in table_rows(text):
        # The state total is printed between KONKAN & GOA and its first district.
        if serial is None and name == "MAHARASHTRA":
            state = dict(zip(("day", "period"), parse_values(values)))
        elif serial is None:
            subdivision = name if name in SUBDIVISIONS else None
        elif subdivision and name not in NON_MAHARASHTRA:
            add_district(rows, subdivision, name, values)
    missing = sorted(set(DISTRICTS) - set(rows))
    if missing or state is None:
        raise ValueError(f"missing districts {missing} or state row")
    return {"day": iso(day), "periodStart": iso(start), "periodEnd": iso(end), "state": state, "rows": rows}


def departure_warnings(rows: dict) -> list[dict]:
    """Rows whose printed departure differs from one recomputed from the printed millimetres.

    IMD prints trace rain as 0 mm but computes the departure (and LD category)
    from the unrounded value, so small actuals legitimately land here.
    """
    warnings = []
    for row in rows.values():
        for span in ("day", "period"):
            v = row[span]
            if v["actualMm"] is None or v["departurePercent"] is None or not v["normalMm"]:
                continue
            calculated = (v["actualMm"] - v["normalMm"]) / v["normalMm"] * 100
            if abs(calculated - v["departurePercent"]) > DEPARTURE_TOLERANCE:
                warnings.append({"district": row["imdName"], "span": span,
                                 "printed": v["departurePercent"], "calculated": round(calculated, 1),
                                 "likelyCause": "trace rainfall printed as 0 mm" if v["actualMm"] == 0 else "unexplained"})
    return warnings


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "MaharashtraFieldNotes/1.0 (public official data import)"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def pdf_text(raw: bytes) -> str:
    tool = shutil.which("pdftotext")
    if not tool:
        raise SystemExit("pdftotext (xpdf or poppler) is required.")
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "report.pdf"
        pdf.write_bytes(raw)
        result = subprocess.run([tool, "-raw", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, check=True)
    return result.stdout.decode("utf8", "replace")


def build(parsed: dict, digest: str, size: int, retrieved_at: str, pdf_name: str) -> dict:
    records = []
    for name, row in parsed["rows"].items():
        records.append({"districtCode": DISTRICTS[name], **row})
    records.sort(key=lambda r: r["districtCode"])
    warnings = departure_warnings(parsed["rows"])
    return {
        "meta": {
            "title": "IMD district rainfall: Maharashtra",
            "titleMr": "जिल्हानिहाय पाऊस (भारतीय हवामान विभाग)",
            "publisher": "India Meteorological Department (IMD), Ministry of Earth Sciences",
            "sourcePage": SOURCE_PAGE, "sourceDocumentUrl": PDF_URL,
            "localDocumentUrl": f"/data/rainfall/{pdf_name}",
            "reportDay": parsed["day"], "periodStart": parsed["periodStart"], "periodEnd": parsed["periodEnd"],
            "retrievedAt": retrieved_at, "sha256": digest, "bytes": size, "recordCount": len(records),
            "coverage": "All 36 Maharashtra districts as aggregated by IMD; district averages, not village or field rainfall",
            "extractionMethod": "pdftotext -raw text of IMD's machine-generated PDF; rows checked against page images on first import",
            "refreshMode": "manual run of scripts/import-rainfall.py", "automaticRefresh": False,
            "units": {"actualMm": "observed rainfall, millimetres", "normalMm": "IMD long-period normal for the same dates, millimetres",
                      "departurePercent": "printed percent departure from normal"},
            "categories": {k: {"en": en, "mr": mr} for k, (en, mr) in CATEGORIES.items()},
            "limitations": [
                "District figures are IMD area averages from reporting stations; rainfall at a village or farm can differ.",
                "Observed rainfall says nothing about rain to come; see the forecast panel and IMD advisories for that.",
                "The period total runs from the first day of the month; it is not the whole monsoon season.",
                "IMD republishes the same URL daily. This snapshot is fixed to the report date above.",
            ],
        },
        "state": parsed["state"],
        "validation": {"districtsFound": len(records), "allDistrictsMapped": len(records) == 36,
                       "departureWarnings": warnings},
        "records": records,
    }


def publish(files: dict[str, bytes], prune: str) -> None:
    """Stage every file, then move each into OUTPUT with index.json last; drop older files matching `prune`."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    # Stage beside the output so os.replace never crosses filesystems (HUB_WORK_DIR may be elsewhere).
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=OUTPUT.parent))
    try:
        for name, content in files.items():
            (staging / name).write_bytes(content)
        for name in sorted(files, key=lambda n: n == INDEX):
            os.replace(staging / name, OUTPUT / name)
        for old in OUTPUT.glob(prune):
            if old.name not in files:
                old.unlink()
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def published_day() -> str | None:
    index = OUTPUT / INDEX
    return json.loads(index.read_text(encoding="utf8"))["meta"]["reportDay"] if index.exists() else None


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf8")


def write_outputs(dataset: dict, raw: bytes, pdf_name: str) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / pdf_name).write_bytes(raw)  # dated archive; only the latest report is published
    meta = dataset["meta"]
    provenance = {key: meta[key] for key in ("publisher", "sourcePage", "sourceDocumentUrl", "reportDay", "periodStart",
                                             "periodEnd", "retrievedAt", "sha256", "bytes", "extractionMethod")}
    provenance.update({"script": "scripts/import-rainfall.py", "validation": dataset["validation"],
                       "apiCheck": "IMD district rainfall JSON APIs returned 401 (IP whitelisting) on 2026-10-27; the public PDF is used instead."})
    flat = [{"districtCode": r["districtCode"], "imdName": r["imdName"], "subdivision": r["subdivision"],
             **{f"{span}{k[0].upper()}{k[1:]}": r[span][k] for span in ("day", "period") for k in r[span]}}
            for r in dataset["records"]]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(flat[0]))
    writer.writeheader()
    writer.writerows(flat)
    publish({pdf_name: raw, "rainfall.csv": ("﻿" + stream.getvalue()).encode("utf8"),
             "provenance.json": json_bytes(provenance), INDEX: json_bytes(dataset)}, prune="imd-district-rainfall-*.pdf")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, help="Use an already downloaded IMD report instead of fetching today's")
    parser.add_argument("--force", action="store_true", help="Allow replacing the published snapshot with an older report")
    args = parser.parse_args()
    raw = args.pdf.read_bytes() if args.pdf else fetch(PDF_URL)
    digest = hashlib.sha256(raw).hexdigest()
    try:
        parsed = parse_report(pdf_text(raw))
    except (ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Rainfall report not imported; previous snapshot kept. {error}")
    current = published_day()
    if current and parsed["day"] < current and not args.force:
        raise SystemExit(f"Report for {parsed['day']} is older than the published {current}; previous snapshot kept. Use --force to replace it.")
    pdf_name = f"imd-district-rainfall-{parsed['day']}.pdf"
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    dataset = build(parsed, digest, len(raw), retrieved_at, pdf_name)
    write_outputs(dataset, raw, pdf_name)
    print(json.dumps({"reportDay": parsed["day"], "period": [parsed["periodStart"], parsed["periodEnd"]],
                      "districts": len(dataset["records"]), "sha256": digest,
                      "departureWarnings": len(dataset["validation"]["departureWarnings"])}))


if __name__ == "__main__":
    main()
