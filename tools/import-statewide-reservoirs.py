#!/usr/bin/env python3
"""Refresh Maharashtra's CWC-monitored reservoir subset from the public bulletin.

Production dependencies: Python 3.11+ and poppler-utils (pdftotext) or PyMuPDF.
Scheduled refreshes find newer bulletins by their public file names. The first
import, or --discover, opens the public JS bulletin page with Playwright and
Chromium, without a login, and collects only its published PDF links. No private API, credentials or app secrets
are used. If any download / validation step fails, the existing dataset remains.
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
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PUBLIC = Path(os.environ.get("HUB_PUBLIC_DIR") or APP / "public") / "data" / "reservoirs-statewide"
WORK = Path(os.environ.get("HUB_WORK_DIR") or APP / "outputs") / "reservoirs-statewide"
INDEX_URL = "https://rsms.cwc.gov.in/frameWork/web/bulletin-report-page"
PUBLISHER = "Central Water Commission, Ministry of Jal Shakti, Government of India"
NASHIK_REPORT = "https://nashik.gov.in/en/document/dam-storage-information-2/"
HANDBOOK = "https://cwc.gov.in/sites/default/files/handbook-on-water-related-information-2017.pdf"
NRSD = "https://ndsa.gov.in/ndsa-backend/web/sites/default/files/2026-07/NATIONAL%20REGISTER%20OF%20SPECIFIED%20DAMS%202026.pdf"

# A dam's district here is a documented project/location label, never a water
# entitlement or command-area allocation. Unreviewed projects remain null.
# Keep URLs and evidence notes beside each manually reviewed identity match.
DISTRICTS = {
    "KHADAKVASLA": ("490", "Pune", "https://pune.gov.in/en/tourist-place/khadakwasala-dam/", "District administration's Khadakwasala dam page; spelling alias KHADAKVASLA → Khadakwasala."),
    "PANSHET TANAJISAGAR": ("490", "Pune", "https://pune.gov.in/en/tourist-place/khadakwasala-dam/", "Same district dam page identifies the nearby twin dams Panshet and Varasgaon."),
    "BHAMA ASKHED": ("490", "Pune", HANDBOOK, "2017 CWC project register, Table 6, Bhama Askhed row168: district Pune."),
    "CHASKAMAN": ("490", "Pune", HANDBOOK, "2017 CWC project register, Table 6, Chaskaman row173: district Pune."),
    "BHATSA": ("497", "Thane", HANDBOOK, "2017 CWC project register, Table 6, BHATSA 1 row169: district Thane."),
    "DUDHGANGA": ("480", "Kolhapur", HANDBOOK, "2017 CWC project register, Table 6, Dudhganga row185: district Kolhapur."),
    "URMODI": ("494", "Satara", HANDBOOK, "2017 CWC project register, Table 6, Urmodi Project row269: district Satara."),
    "TILLARI": ("495", "Sindhudurg", HANDBOOK, "2017 CWC project register, Table 6, Tillari Interstate Irrigation row267: Maharashtra project district Sindhudurg; this does not describe the Goa service area."),
    "DHOM": ("494", "Satara", "https://www.satara.gov.in/en/places-of-interest/", "District administration lists Dhom Dam among district places of interest."),
    "KANHER": ("494", "Satara", "https://www.satara.gov.in/en/places-of-interest/", "District administration lists Kanher Dam among district places of interest."),
    "KOYANA": ("494", "Satara", "https://www.satara.gov.in/en/tourist-place/patan-taluka/", "District administration locates Koyna Dam near Helwak, Patan taluka; spelling alias KOYANA → Koyna."),
    "BHANDARDARA": ("466", "Ahilyanagar", "https://ahilyanagar.maharashtra.gov.in/en/tourist-place/bhandardara/", "District administration's Bhandardara dam page."),
    "MULA": ("466", "Ahilyanagar", "https://ahilyanagar.maharashtra.gov.in/en/tourist-place/mula-dam/", "District administration's Mula Dam page."),
    "DARNA DAM": ("487", "Nashik", NASHIK_REPORT, "Named Darna row11 in district administration's 23 October2026 reservoir report."),
    "GIRNA": ("487", "Nashik", NASHIK_REPORT, "Named Girna row24 in district administration's 23 October2026 reservoir report; does not allocate water to Nashik villages."),
    "LOWER TERNA": ("488", "Dharashiv", "https://gazetteers.maharashtra.gov.in/cultural.maharashtra.gov.in/english/gazetteer/Osmanabad/appendix.html", "Official Osmanabad district gazetteer lists Lower Terna irrigation project; historical district label crosswalked to Dharashiv."),
    # National Register of Specified Dams 2026 rows (PDF page, PIC). Identity is
    # checked by the register's effective storage against the bulletin's live
    # capacity at FRL; each row was read from the rendered page, not only text.
    "UPPER VAITARNA": ("487", "Nashik", NRSD, "NRSD 2026 PDF page 422, PIC MH09HH0384 Upper Vaitarana: district Nashik; effective storage 331.31 MCM matches the bulletin's 0.331 BCM."),
    "UPPER TAPI": ("478", "Jalgaon", NRSD, "NRSD 2026 PDF page 334, PIC MH09MH0948 Hatnur: district Jalgaon; effective storage 255 MCM matches the bulletin's 0.255 BCM. The 2017 CWC handbook also lists the Upper Tapi project in Jalgaon."),
    "PENCH": ("484", "Nagpur", NRSD, "NRSD 2026 PDF page 419, PIC MH09HH1229 Totladoh: district Nagpur; effective storage 1016.93 MCM against the bulletin's 1.091 BCM. The other Pench dam, Kamthikhairy (page 344, MH09HH0596), is also in Nagpur."),
    "UPPER WARDHA": ("498", "Wardha", NRSD, "NRSD 2026 PDF page 422, PIC MH09HH1319 Upper Wardha: district Wardha (a separate cell from the river, also Wardha); effective storage 564.05 MCM matches the bulletin's 0.564 BCM."),
    "MANIKDOH": ("490", "Pune", NRSD, "NRSD 2026 PDF page 372, PIC MH09HH1060 Manikdoh: district Pune; effective storage 288.105 MCM matches the bulletin's 0.288 BCM."),
    "BHATGHAR": ("490", "Pune", NRSD, "NRSD 2026 PDF page 305, PIC MH09HH0048 Bhatghar: district Pune; gross storage 672.65 MCM matches the bulletin's 0.673 BCM."),
    "NIRA DEOGHAR": ("490", "Pune", NRSD, "NRSD 2026 PDF page 384, PIC MH09HH1554 Niradevghar: district Pune; effective storage 332.126 MCM matches the bulletin's 0.332 BCM."),
    "THOKARWADI": ("490", "Pune", NRSD, "NRSD 2026 PDF page 418, PIC MH295HH3573 Thokarwadi Gravity Dam: district Pune; effective storage 352.64 MCM matches the bulletin's 0.353 BCM."),
    "MULSHI": ("490", "Pune", NRSD, "NRSD 2026 PDF page 378, PIC MH295LH3572 Mulshi Gravity: district Pune; effective storage 570.76 MCM against the bulletin's 0.572 BCM."),
    "SURYA": ("665", "Palghar", NRSD, "NRSD 2026 PDF page 319, PIC MH09HH1173 Dhamni (Surya): district Palghar; effective storage 276.35 MCM matches the bulletin's 0.276 BCM. The 2017 CWC handbook names Thane, the district before Palghar was formed."),
    "DIMBHE DAM": ("490", "Pune", NRSD, "NRSD 2026 PDF page 322, PIC MH09HH1558 Dimbhe: district Pune; effective storage 353.91 MCM matches the bulletin's 0.354 BCM."),
    "VEER DAM": ("490", "Pune", NRSD, "NRSD 2026 PDF page 424, PIC MH09HH0116 Veer: district Pune; effective storage 266 MCM matches the bulletin's 0.266 BCM."),
    "BARVI DAM": ("497", "Thane", NRSD, "NRSD 2026 PDF page 303, PIC MH295MH3540 Barvi: district Thane; effective storage 338.84 MCM matches the bulletin's 0.339 BCM."),
    "JAYAKWADI": ("469", "Chhatrapati Sambhajinagar", NRSD, "NRSD 2026 PDF page 340, PIC MH09HH0597 Jayakwadi: district Chhatrapati Sambhajinagar; effective storage 2170.94 MCM matches the bulletin's 2.171 BCM."),
    "BHIMA UJJAINI": ("496", "Solapur", NRSD, "NRSD 2026 PDF page 420, PIC MH09HH0843 Ujjani: district Solapur; effective storage 1517.22 MCM matches the bulletin's 1.517 BCM."),
    "ISAPUR": ("500", "Yavatmal", NRSD, "NRSD 2026 PDF page 337, PIC MH09HH0947 Isapur: district Yavatmal; effective storage 964.09 MCM matches the bulletin's 0.965 BCM. The register's other Isapur (MH330LH3068) holds 1.6 MCM."),
    "YELDARI": ("477", "Hingoli", NRSD, "NRSD 2026 PDF page 432, PIC MH09MH1601 Yeldari: district Hingoli, Purna river, gross storage 934.44 MCM against the bulletin's 0.809 BCM live capacity. Its effective-storage cell repeats the next row's 1.216; the register's other Yeldari (MH292LH3690, Latur) holds 1.3 MCM. The Mahabhumi village directory also lists Yeldari village in Sengaon taluka, Hingoli."),
    "WAGHUR": ("478", "Jalgaon", NRSD, "NRSD 2026 PDF page 427, PIC MH09LH1750 Waghur: district Jalgaon; effective storage 248.21 MCM matches the bulletin's 0.248 BCM."),
    "MANJARA": ("488", "Dharashiv", NRSD, "NRSD 2026 PDF page 372, PIC MH09MH1585 Manjra: district Osmanabad, now Dharashiv; effective storage 176.963 MCM matches the bulletin's 0.177 BCM. The register's Yavatmal Manjara (MH09MH0946) holds 3.23 MCM."),
    "MAJALGAON": ("470", "Beed", NRSD, "NRSD 2026 PDF page 370, PIC MH09HH1174 Majalgaon: district Beed; effective storage 312 MCM matches the bulletin's 0.312 BCM."),
    "LOWER MANAR": ("485", "Nanded", NRSD, "NRSD 2026 PDF page 371, PIC MH09MH0170 Manar: district Nanded; effective storage 138.211 MCM against the bulletin's 0.130 BCM. The register's Upper Manar (MH09HH1806) is also in Nanded."),
    "LOWER DUDHANA": ("489", "Parbhani", NRSD, "NRSD 2026 PDF page 364, PIC MH09MH2089 Lower Dudhana: district Parbhani; effective storage 242.2 MCM matches the bulletin's 0.242 BCM."),
}

# Boundary dams whose register district differs from the district of the village
# the dam is known by. They are listed for both, each with its own evidence.
VILLAGES = "https://mahavillages.mahabhumi.gov.in/all_list.php"
ALSO_DISTRICTS = {
    "UPPER WARDHA": [("468", "Amravati", VILLAGES, "The dam is known by Simbhora village, which the Mahabhumi village directory lists in Morshi taluka, Amravati. The register records Wardha; the dam is on the Wardha river at the district boundary.")],
    "MANJARA": [("470", "Beed", VILLAGES, "The dam is reported at Dhanegaon, Kaij taluka; the Mahabhumi village directory lists that Dhanegaon in Kaij taluka, Beed. The register records Osmanabad (Dharashiv); the dam is on the Manjara river at the district boundary.")],
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    os.replace(temp, path)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "MaharashtraFieldNotes/1.0 (public official bulletin import)"})
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status != 200:
            raise ValueError(f"HTTP {response.status} for official source")
        return response.read()


def bulletin_date(url: str) -> date:
    match = re.search(r"bulletin-(\d{2})-(\d{2})-(\d{4})(?:-\d+)?\.pdf(?:$|\?)", url)
    if not match:
        raise ValueError("Unrecognized official bulletin filename date")
    day, month, year = map(int, match.groups())
    return date(year, month, day)


BULLETIN_BASE = "https://rsms.cwc.gov.in/admin/storage/bulletins/"
SEQUENCED_NAME = re.compile(r"bulletin-(\d{2})-(\d{2})-(\d{4})-(\d+)\.pdf$")
# Bulletins appear weekly; older than this with no successor means the naming
# pattern changed or publication stopped, so the refresh reports a failure.
STALE_AFTER_DAYS = 13


def pdf_exists(url: str) -> bool:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "MaharashtraFieldNotes/1.0 (public official bulletin import)"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status == 200 and "pdf" in response.headers.get("Content-Type", "").lower()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise


def probe_latest(previous_url: str, today: date, exists=pdf_exists) -> tuple[str | None, list[str]]:
    """Find newer bulletins by their public file names: a weekly date and a rising serial.

    The bulletin page lists these PDFs only through an encrypted API that needs a
    browser. The PDFs themselves are plain public files. The host refuses requests
    from outside India (HTTP 401 on GitHub's runners), so run this from India.
    """
    match = SEQUENCED_NAME.search(urllib.parse.urlparse(previous_url).path)
    if not match:
        raise ValueError("Published bulletin URL has no serial number to continue from; run with --discover")
    day, month, year, serial = map(int, match.groups())
    last = date(year, month, day)
    tried = []
    for weeks in range(4, 0, -1):  # newest first, so a missed week is skipped over
        for shift in (0, -1, 1, -2, 2):  # the usual weekday, then nearby days
            when = last + timedelta(days=7 * weeks + shift)
            if when > today:
                continue
            for number in range(serial + 1, serial + weeks + 4):  # serials sometimes skip
                url = f"{BULLETIN_BASE}bulletin-{when:%d-%m-%Y}-{number}.pdf"
                tried.append(url)
                if exists(url):
                    return url, tried
    return None, tried


def discover_pdf(browser_executable: str | None = None) -> tuple[str, list[dict]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Latest bulletin discovery needs 'pip install playwright' and 'python -m playwright install --with-deps chromium'") from exc
    with sync_playwright() as playwright:
        launch_options = {"headless": True}
        if browser_executable:
            launch_options["executable_path"] = browser_executable
        browser = playwright.chromium.launch(**launch_options)
        try:
            page = browser.new_page()
            page.goto(INDEX_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector('a[href*="/admin/storage/bulletins/"]', timeout=60000)
            links = page.locator('a[href*="/admin/storage/bulletins/"]').evaluate_all("items => items.map(a => ({url: a.href, rowText: a.closest('tr')?.innerText || ''}))")
        finally:
            browser.close()
    valid = []
    for item in links:
        parsed = urllib.parse.urlparse(item["url"])
        if parsed.scheme == "https" and parsed.netloc == "rsms.cwc.gov.in" and parsed.path.startswith("/admin/storage/bulletins/"):
            try:
                observed = bulletin_date(item["url"])
            except ValueError:
                continue
            if observed <= date.today():
                valid.append((observed, item["url"]))
    if not valid:
        raise ValueError("No dated public CWC bulletin PDF links were found; retaining last-good snapshot")
    return max(valid)[1], links


def is_poppler(tool: str) -> bool:
    banner = subprocess.run([tool, "-v"], capture_output=True)
    return b"poppler" in (banner.stdout + banner.stderr).lower()


def extract_text(pdf: Path) -> str:
    # xpdf's pdftotext (common on Windows) merges this table's columns under
    # -layout and drops the title under -table, so only Poppler's is used.
    tool = shutil.which("pdftotext")
    if tool and is_poppler(tool):
        result = subprocess.run([tool, "-layout", "-enc", "UTF-8", str(pdf), "-"], check=True, capture_output=True)
        return result.stdout.decode("utf8")
    try:
        import pymupdf
    except ImportError as exc:
        raise RuntimeError("PDF parsing needs Poppler's pdftotext (poppler-utils), or PyMuPDF; xpdf's pdftotext misreads this bulletin") from exc
    with pymupdf.open(pdf) as document:
        return "\f".join(page.get_text(sort=True) for page in document)


ROW_PATTERN = re.compile(
    r"^\s*(\d{1,3})\s+(.+?)\s+"
    r"(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+"
    r"(\d{2}\.\d{2}\.\d{4})\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+"
    r"(\d+\.\d+)\s+(\d+\.\d+)\s*$"
)


def parse_state_summary(text: str) -> dict:
    pattern = re.compile(r"^\s*\d+\s+Maharashtra\s+(\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)", re.M)
    match = pattern.search(text)
    if not match:
        raise ValueError("Maharashtra's independent state summary could not be parsed")
    count, capacity, storage, previous, normal, percent, previous_percent, normal_percent = match.groups()
    return {"reservoirCount": int(count), "capacityBcm": float(capacity), "storageBcm": float(storage), "previousYearStorageBcm": float(previous), "normalStorageBcm": float(normal), "percent": float(percent), "lastYearPercent": float(previous_percent), "normalPercent": float(normal_percent)}


def parse_rows(text: str, expected_date: date) -> list[dict]:
    if "Central Water Commission" not in text or "WEEKLY BULLETIN" not in text:
        raise ValueError("Source is missing the official CWC weekly bulletin title")
    title_date = re.search(r"AS ON\s+(\d{2}\.\d{2}\.\d{4})", text, re.I)
    if not title_date or datetime.strptime(title_date.group(1), "%d.%m.%Y").date() != expected_date:
        raise ValueError("PDF title date does not match the public document URL date")
    records = []
    inside_maharashtra = False
    for page_number, page in enumerate(text.split("\f"), 1):
        if not re.search(r"REGION\s*/\s*STATE\s+WISE\s+WEEKLY\s+REPORT", page, re.I):
            continue
        for line in page.splitlines():
            stripped = line.strip()
            if stripped == "Maharashtra":
                inside_maharashtra = True
                continue
            if inside_maharashtra and stripped == "Goa":
                inside_maharashtra = False
                break
            if not inside_maharashtra:
                continue
            match = ROW_PATTERN.match(line)
            if match:
                serial, name, frl, level, capacity, storage, observed, current_percent, last_year_percent, normal_percent, irrigation, hydro = match.groups()
                observed_date = datetime.strptime(observed, "%d.%m.%Y").date()
                if observed_date != expected_date:
                    raise ValueError("Mixed observation dates in Maharashtra's source table")
                records.append({
                    "sourceRow": int(serial), "name": " ".join(name.split()),
                    "asOf": observed_date.isoformat(), "frlM": float(frl),
                    "reservoirLevelM": float(level), "capacityBcm": float(capacity),
                    "storageBcm": float(storage), "percent": float(current_percent),
                    "lastYearPercent": float(last_year_percent), "normalPercent": float(normal_percent),
                    "sourcePage": page_number, "rawRow": line.strip(),
                })
            elif records and re.fullmatch(r"[A-Z][A-Z ]{1,29}", stripped) and not any(x in stripped for x in ("CURRENT", "STORAGE", "CAPACITY", "RESERVOIR", "WEEKLY", "REGION", "FRL", "YEAR", "NO ")):
                # Wrapped reservoir names appear immediately below their numeric
                # row (UPPER / VAITARNA, PANSHET / TANAJISAGAR, etc.).
                records[-1]["name"] += " " + stripped
                records[-1]["rawRow"] += " | " + stripped
    if not 30 <= len(records) <= 250:
        raise ValueError(f"Unexpected Maharashtra row count {len(records)}; refusing a partial parse")
    if [row["sourceRow"] for row in records] != list(range(1, len(records) + 1)):
        raise ValueError("Maharashtra source row numbers are not continuous from1")
    if len({row["name"] for row in records}) != len(records):
        raise ValueError("Duplicate reservoir names in parsed table")
    state_summary = parse_state_summary(text)
    if len(records) != state_summary["reservoirCount"]:
        raise ValueError("Parsed project count differs from independent published Maharashtra summary")
    tolerance = (len(records) + 1) * 0.0005 + 1e-9
    if abs(sum(row["capacityBcm"] for row in records) - state_summary["capacityBcm"]) > tolerance or abs(sum(row["storageBcm"] for row in records) - state_summary["storageBcm"]) > tolerance:
        raise ValueError("Row storage/capacity totals differ from independent state summary beyond source rounding")
    for row in records:
        capacity, storage, percent = row["capacityBcm"], row["storageBcm"], row["percent"]
        if capacity <= 0 or storage < 0 or not 0 <= percent <= 110:
            raise ValueError(f"Invalid storage / capacity / percent in {row['name']}")
        # PDF volumes are rounded to0.001BCM (1MCM); percentages use finer
        # underlying measurements. Validate the rounding interval, not equality.
        low = max(0, storage - 0.0005) / (capacity + 0.0005) * 100
        high = (storage + 0.0005) / max(0.000001, capacity - 0.0005) * 100
        if not low - 0.02 <= percent <= high + 0.02:
            raise ValueError(f"Printed percent outside rounded-volume bounds in {row['name']}")
    return records


def build_dataset(records: list[dict], url: str, raw_pdf: bytes, checked_at: str) -> dict:
    for row in records:
        match = DISTRICTS.get(row["name"])
        row.update({
            "id": "cwc-" + re.sub(r"[^a-z0-9]+", "-", row["name"].lower()).strip("-"),
            "nameMr": None, "state": "Maharashtra", "source": "cwc",
            "capacityMcm": round(row["capacityBcm"] * 1000, 3),
            "storageMcm": round(row["storageBcm"] * 1000, 3),
            "districtCode": match[0] if match else None,
            "district": match[1] if match else None,
            "districtEvidence": {"url": match[2], "note": match[3], "checkedOn": "2026-10-28"} if match else None,
            "districtMatchStatus": "reviewed-government-source" if match else "unverified",
            "alsoDistricts": [{"code": code, "name": name, "evidence": {"url": url, "note": note, "checkedOn": "2026-10-28"}} for code, name, url, note in ALSO_DISTRICTS.get(row["name"], [])],
        })
    capacity = round(sum(row["capacityMcm"] for row in records), 3)
    storage = round(sum(row["storageMcm"] for row in records), 3)
    mapped = [row for row in records if row["districtCode"]]
    district_codes = sorted({row["districtCode"] for row in mapped} | {also["code"] for row in records for also in row["alsoDistricts"]})
    report_date = bulletin_date(url).isoformat()
    return {
        "meta": {
            "title": "Maharashtra reservoirs monitored by CWC",
            "titleMr": "केंद्रीय जल आयोगाने निरीक्षण केलेली महाराष्ट्रातील धरणे",
            "coverage": "38 CWC-monitored Maharashtra projects in this bulletin; this is a subset, not every dam or every district",
            "sourcePublisher": PUBLISHER, "sourceUrl": INDEX_URL, "sourceDocumentUrl": url,
            "localDocumentUrl": "/data/reservoirs-statewide/latest-bulletin.pdf",
            "csvUrl": "/data/reservoirs-statewide/reservoirs.csv",
            "provenanceUrl": "/data/reservoirs-statewide/provenance.json",
            "reportDate": report_date, "observationDate": report_date,
            "retrievedAt": checked_at, "recordCount": len(records),
            "sha256": hashlib.sha256(raw_pdf).hexdigest(),
            "extractionMethod": "Automatic pdftotext-layout or PyMuPDF text parsing of the Maharashtra rows in the CWC region/state weekly table",
            "refreshMode": "automatic-public-weekly-bulletin", "automaticRefresh": True,
            "districtCodes": district_codes, "mappedRecordCount": len(mapped),
            "unmappedRecordCount": len(records) - len(mapped),
            "units": {
                "capacityBcm": "billion cubic metres of live capacity at full reservoir level",
                "storageBcm": "billion cubic metres of live storage on observation date",
                "capacityMcm": "million cubic metres; source BCM multiplied by1000",
                "storageMcm": "million cubic metres; source BCM multiplied by1000",
                "frlM": "metres; full reservoir level as published",
                "reservoirLevelM": "metres; observed reservoir level as published",
                "percent": "published live storage percentage of live capacity at FRL",
                "lastYearPercent": "last year's storage as percent of live capacity, corresponding period",
                "normalPercent": "normal storage as percent of live capacity, corresponding period",
            },
            "limitations": [
                "CWC monitors a selected set of important reservoirs; this is not a complete Maharashtra dam inventory or a district water balance.",
                "Reservoirs with an unverified project-to-district identity retain a null districtCode and remain visible in the Maharashtra list.",
                "A district/project match describes location or a named project in a government source, not a service area, village entitlement or irrigation allocation.",
                "A dam on a river that forms a district boundary is listed under the district its government source records. Where official sources place it in two districts (alsoDistricts), it is listed for both, each with its own evidence. Any dam can also serve neighbouring districts.",
                "BCM volumes are rounded to0.001BCM by the source. Retain source percentages rather than recomputing individual percentages from rounded volumes.",
                "Storage and water level do not establish how many days village, household or cattle water will last. Inflow, releases, withdrawals and allocation are not provided here.",
                "Nashik's separate district report is supplemental. Overlapping reservoirs must not be added together across reports or dates.",
            ],
        },
        "summary": {
            "asOf": report_date, "reservoirCount": len(records),
            "capacityMcm": capacity, "storageMcm": storage,
            "percent": round(storage / capacity * 100, 2),
            "aggregation": "Sum of this bulletin's monitored Maharashtra rows; capacity-weighted percentage calculated from rounded source volumes",
            "mappedDistrictCount": len(district_codes), "unmappedRecordCount": len(records) - len(mapped),
        },
        "validation": {"continuousSourceRows": True, "singleObservationDate": True, "percentWithinVolumeRoundingBounds": True, "countMatchesIndependentStateSummary": True, "totalsMatchIndependentStateSummaryWithinRounding": True, "duplicateNames": False},
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, help="Parse an already downloaded source PDF")
    parser.add_argument("--text", type=Path, help="Parse saved layout text, while still preserving and hashing --pdf")
    parser.add_argument("--url", help="Official document URL for a local --pdf; omitted in scheduled auto-discovery")
    parser.add_argument("--discover", action="store_true", help="Read the bulletin page in a browser instead of checking the expected file names")
    parser.add_argument("--browser-executable", help="Optional locally installed Chrome/Chromium path")
    parser.add_argument("--output-dir", type=Path, default=PUBLIC)
    parser.add_argument("--work-dir", type=Path, default=WORK)
    args = parser.parse_args()
    checked_at = now()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    status_path = args.output_dir / "refresh-status.json"
    try:
        links = []
        if args.pdf:
            if not args.url:
                raise ValueError("--pdf requires the original official --url for provenance")
            url = args.url
            raw = args.pdf.read_bytes()
        else:
            if args.url:
                raise ValueError("Scheduled refresh discovers its own public bulletin; --url is only for a local --pdf fixture")
            index_path = args.output_dir / "index.json"
            published = json.loads(index_path.read_text(encoding="utf8")) if index_path.exists() else None
            if published and not args.discover:
                url, probed = probe_latest(published["meta"]["sourceDocumentUrl"], date.today())
                links = [{"url": item, "method": "file-name check"} for item in probed]
                if url is None:
                    age = (date.today() - date.fromisoformat(published["meta"]["reportDate"])).days
                    if age > STALE_AFTER_DAYS:
                        raise ValueError(f"No newer CWC bulletin at the expected file names, and the published one is {age} days old; check {INDEX_URL} or run with --discover")
                    write_json_atomic(status_path, {"status": "unchanged", "checkedAt": checked_at, "reportDate": published["meta"]["reportDate"], "recordCount": published["meta"]["recordCount"], "lastGoodRetained": True, "sourceUrl": INDEX_URL, "checkedUrls": probed})
                    print(json.dumps({"status": "unchanged", "reportDate": published["meta"]["reportDate"], "checkedUrls": len(probed)}))
                    return 0
            else:
                url, links = discover_pdf(args.browser_executable)
            raw = fetch(url)
        source_url = urllib.parse.urlparse(url)
        if source_url.scheme != "https" or source_url.netloc != "rsms.cwc.gov.in" or not source_url.path.startswith("/admin/storage/bulletins/"):
            raise ValueError("Only the official public CWC bulletin host/path is accepted")
        observed_date = bulletin_date(url)
        if observed_date > date.today():
            raise ValueError("Source observation date is in the future")
        if not raw.startswith(b"%PDF-") or not 10000 <= len(raw) <= 25000000:
            raise ValueError("Official source did not return an expected-size PDF")
        old_path = args.output_dir / "index.json"
        previous = json.loads(old_path.read_text(encoding="utf8")) if old_path.exists() else None
        if previous and observed_date.isoformat() < previous["meta"]["reportDate"]:
            raise ValueError("Official result would downgrade observation date; retaining last-good snapshot")
        source_filename = Path(source_url.path).name
        source_pdf = args.work_dir / source_filename
        source_pdf.write_bytes(raw)
        text = args.text.read_text(encoding="utf8") if args.text else extract_text(source_pdf)
        (args.work_dir / (source_filename + ".txt")).write_text(text, encoding="utf8")
        records = parse_rows(text, observed_date)
        dataset = build_dataset(records, url, raw, checked_at)
        dataset["publishedStateSummary"] = parse_state_summary(text)
        dataset["meta"]["coverage"] = f"{len(records)} CWC-monitored Maharashtra projects in this bulletin; a subset, not every dam or every district"
        provenance = {
            "publisher": PUBLISHER, "sourceUrl": INDEX_URL, "documentUrl": url,
            "documentDate": observed_date.isoformat(), "retrievedAt": checked_at,
            "sha256": dataset["meta"]["sha256"], "bytes": len(raw),
            "method": dataset["meta"]["extractionMethod"],
            "publicDiscovery": "Checked the expected public file names (weekly date, serial +1) in the official bulletin store. No login or private API." if links and links[0].get("method") == "file-name check" else "Fresh anonymous browser reads PDF links displayed on the official public Bulletin page. No login or private API.",
            "discoveredLinks": links, "districtMatches": DISTRICTS, "alsoDistrictMatches": ALSO_DISTRICTS,
            "validation": dataset["validation"],
        }
        stream = io.StringIO(newline="")
        csv_records = [{**{key: value for key, value in row.items() if key not in ("rawRow", "districtEvidence", "alsoDistricts")}, "alsoDistrictCodes": " ".join(also["code"] for also in row["alsoDistricts"])} for row in records]
        writer = csv.DictWriter(stream, fieldnames=list(csv_records[0]))
        writer.writeheader()
        writer.writerows(csv_records)
        # All checks passed. Commit reviewed evidence and normalized data only now.
        (args.output_dir / "latest-bulletin.pdf").write_bytes(raw)
        (args.output_dir / "reservoirs.csv").write_text("\ufeff" + stream.getvalue(), encoding="utf8", newline="")
        write_json_atomic(args.output_dir / "provenance.json", provenance)
        write_json_atomic(old_path, dataset)
        write_json_atomic(status_path, {"status": "ok", "checkedAt": checked_at, "reportDate": observed_date.isoformat(), "recordCount": len(records), "lastGoodRetained": False, "sourceUrl": INDEX_URL})
        print(json.dumps({"status": "ok", "summary": dataset["summary"], "sha256": dataset["meta"]["sha256"]}))
        return 0
    except Exception as exc:
        existing = args.output_dir / "index.json"
        write_json_atomic(status_path, {"status": "error", "checkedAt": checked_at, "error": str(exc), "lastGoodRetained": existing.exists(), "sourceUrl": INDEX_URL})
        print(f"CWC refresh failed; existing index retained: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
