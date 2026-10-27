#!/usr/bin/env python3
"""Import monsoon-season rainfall by taluka and revenue circle from Maharain.

Maharain (Agriculture Department, Government of Maharashtra; hosted by NIC Pune) has
no download or API. Its report page posts a district code to rpt_circle_wise_report_
june_september.php and receives an HTML table: each taluka's circles followed by the
taluka total row, and the district total last. This script asks once per district,
parses those tables, maps names to LGD codes and publishes JSON. Values are provisional
for three days on Maharain. A failed run leaves the previous output intact.
No third-party packages are needed.
"""
from __future__ import annotations

import difflib
import html
import http.cookiejar
import json
import os
import re
import shutil
import ssl
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
# Overridable so the public site repository can refresh its own copy (see its workflow).
PUBLIC = Path(os.environ.get("HUB_PUBLIC_DIR") or APP / "public")
WORK_ROOT = Path(os.environ.get("HUB_WORK_DIR") or APP / "outputs")
OUTPUT = PUBLIC / "data" / "rainfall-season"
WORK = WORK_ROOT / "rainfall-season"
BOOTSTRAP = PUBLIC / "data" / "bootstrap.json"
BASE = "https://www.maharain.maharashtra.gov.in/test/maharain/"
REPORT = "rpt_circle_wise_report_june_september.php"
STATE_CODE = "11"
PAUSE_SECONDS = 1.0
MATCH_THRESHOLD = 0.75
PERCENT_TOLERANCE = 1.0
INDEX = "index.json"
INTERMEDIATE = Path(__file__).resolve().parent / "certs" / "maharain-chain.pem"
NO_MAHARAIN_DATA = {"482", "483"}  # Mumbai City and Mumbai Suburban are not in Maharain
AGENT = "MaharashtraFieldNotes/1.0 (public official data import)"
CELLS = 12
# Maharain spelling → LGD English name, where fuzzy matching alone is not reliable.
DISTRICT_ALIASES = {"Raigadh": "Raigad", "Parabhani": "Parbhani", "Nasik": "Nashik", "Gondia": "Gondiya"}
TALUKA_ALIASES = {
    ("487", "Nasik"): "Nashik", ("487", "Devali"): "Deola", ("498", "Devali"): "Deoli",
    ("496", "N.solapur"): "Solapur North", ("496", "S.solapur"): "Solapur South",
    ("484", "Nagpur city"): "Nagpur (Urban)", ("484", "Nagpur gramin"): "Nagpur (Rural)", ("484", "Kamthi"): "Kamptee",
    ("484", "Parshiwani"): "Parseoni", ("490", "Velhe"): "Rajgad", ("480", "Bavda"): "Gaganbawada",
    ("476", "Morgaon arjuni"): "Arjuni Morgaon", ("476", "Devari"): "Deori", ("477", "Audha"): "Aundha (Nagnath)",
    ("485", "Naigaon kh"): "Naigaon (Khairgaon)", ("470", "Gevari"): "Georai", ("470", "Kej"): "Kaij",
    ("470", "Vadavani"): "Wadwani", ("468", "Tiwasa"): "Tiosa", ("481", "Davani"): "Deoni", ("489", "Seloo"): "Selu",
    ("478", "Edlabad mukainagar"): "Muktainagar (Edlabad)", ("478", "Bodhwada"): "Bodvad",
}
# Maharain often appends the taluka town ("Khed Rajgurunagar"); the first word is then the taluka.
TOWN_SUFFIXED = re.compile(r"^(\S+)\s+(rajgurunagar|ghodegaon|ghodnandi|saswad|wadgaon|medha|vaduj|dahiwadi|eslampur|kolhapur|paud)$", re.I)

OPEN_ROW = re.compile(r"<tr([^>]*)>", re.I)
CELL =re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S | re.I)
GROUP = re.compile(r"show_sub_row_(\d+_\d+)")
TALUKA_ROW = re.compile(r'data-unique_code="(\d+_\d+)"')
STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}) \d{1,2}:\d{2}:\d{2}\s*[ap]m", re.I)


def tls_context() -> ssl.SSLContext:
    """Normal certificate verification, plus the intermediate Maharain's server leaves out.

    Windows fetches missing intermediates itself; Linux (e.g. GitHub Actions) does not.
    The chain still has to end at a root in the system store.
    """
    context = ssl.create_default_context()
    if INTERMEDIATE.exists():
        context.load_verify_locations(cafile=str(INTERMEDIATE))
    return context


class Client:
    def __init__(self) -> None:
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                                  urllib.request.HTTPSHandler(context=tls_context()))

    def call(self, path: str, form: dict | None = None) -> str:
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        request = urllib.request.Request(BASE + path, data=data, headers={"User-Agent": AGENT})
        with self.opener.open(request, timeout=90) as response:
            return response.read().decode("utf8", "replace")


def number(text: str) -> float | None:
    """Maharain prints '-' for a missing value; keep it missing rather than zero."""
    cleaned = text.replace(",", "").strip()
    return None if cleaned in {"", "-", "NA"} else float(cleaned)


def parse_values(cells: list[str]) -> dict:
    v = [number(c) for c in cells[1:CELLS]]
    return {"junSep": {"normalMm": v[0], "actualMm": v[1], "percent": v[2]},
            "october": {"normalMm": v[3], "toPreviousDayMm": v[4], "reportDayMm": v[5], "totalMm": v[6], "percent": v[7]},
            "season": {"normalMm": v[8], "actualMm": v[9], "percent": v[10]}}


def classify(body: str) -> tuple[str, str, list[str]] | None:
    """One table row → (kind, group, cells); kind is circle, taluka or district."""
    cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in CELL.findall(body)]
    if len(cells) < CELLS or not re.fullmatch(r"-?[\d.,]+|-", cells[1]):
        return None  # header rows
    opened = OPEN_ROW.findall(body)
    group = GROUP.search(opened[-1] if opened else "")
    if group:
        return "circle", group.group(1), cells
    taluka = TALUKA_ROW.search(body)
    if taluka:
        return "taluka", taluka.group(1), cells
    return ("district", "", cells) if "table_data" in body else None


def parse_report(page: str) -> dict:
    """Return {'stamp', 'talukas': [{name, circles, ...values}], 'district': values} from one district table."""
    stamp = STAMP.search(page)
    if not stamp:
        raise ValueError("report timestamp not found")
    pending: dict[str, list[dict]] = {}
    talukas, district = [], None
    # Rows are split on </tr>: taluka total rows are sometimes printed without an opening <tr>.
    for row in filter(None, map(classify, re.split(r"</tr>", page, flags=re.I))):
        kind, group, cells = row
        if kind == "circle":
            pending.setdefault(group, []).append({"name": cells[0], **parse_values(cells)})
        elif kind == "taluka":
            circles = pending.pop(group, [])
            if not circles:
                raise ValueError(f"taluka {cells[0]} has no circles")
            talukas.append({"name": cells[0], **parse_values(cells), "circles": circles})
        else:
            district = parse_values(cells)
    if pending or not talukas or district is None:
        raise ValueError(f"unparsed groups {list(pending)} or missing taluka/district rows")
    return {"stamp": stamp.group(1), "talukas": talukas, "district": district}


def percent_warnings(name: str, values: dict) -> list[str]:
    warnings = []
    for span in ("junSep", "season"):
        v = values[span]
        if None in (v["normalMm"], v["actualMm"], v["percent"]) or not v["normalMm"]:
            continue
        if abs(v["actualMm"] / v["normalMm"] * 100 - v["percent"]) > PERCENT_TOLERANCE:
            warnings.append(f"{name} {span}: printed {v['percent']}%, calculated {v['actualMm'] / v['normalMm'] * 100:.1f}%")
    return warnings


def fold(s: str) -> str:
    """Transliteration skeleton: Maharain and LGD spell the same Marathi names differently."""
    s = re.sub(r"[^a-z]", "", s.lower())
    for a, b in (("w", "v"), ("ee", "i"), ("oo", "u"), ("aa", "a"), ("kh", "k"), ("gh", "g"), ("dh", "d"), ("bh", "b"),
                 ("th", "t"), ("ph", "f"), ("sh", "s"), ("y", "i")):
        s = s.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", s)


def best_match(name: str, candidates: list[dict], key: str = "name") -> dict | None:
    scored = [(difflib.SequenceMatcher(None, fold(name), fold(c[key])).ratio(), c) for c in candidates]
    score, best = max(scored, key=lambda pair: pair[0], default=(0, None))
    return best if score >= MATCH_THRESHOLD else None


def lgd_district(name: str, districts: list[dict]) -> dict:
    target = DISTRICT_ALIASES.get(name, name)
    for d in districts:
        if target.lower() in {d["name"].lower(), *(a.lower() for a in d.get("aliases", []))}:
            return d
    hit = best_match(target, districts)
    if not hit:
        raise ValueError(f"no LGD district for Maharain district {name}")
    return hit


def lgd_taluka(district_code: str, name: str, talukas: list[dict]) -> dict | None:
    suffixed = TOWN_SUFFIXED.match(name)
    target = TALUKA_ALIASES.get((district_code, name), suffixed.group(1) if suffixed else name)
    exact = [t for t in talukas if t["name"].lower() == target.lower()]
    return exact[0] if exact else best_match(target, talukas)


def districts_list(client: Client) -> list[dict]:
    divisions = json.loads(client.call("Handlers/divisionHandler.php", {"getDivision": "1", "stateCode": STATE_CODE}))["data"]
    found = []
    for division in divisions:
        reply = json.loads(client.call("Handlers/districtHandler.php", {"getDistrict": "1", "stateCode": STATE_CODE,
                                                                         "divisionCode": division["division_code"]}))
        found += [{"division": division["division_code"], "code": d["district_code"], "name": d["district_name"]} for d in reply["data"]]
    return found


def build(client: Client, bootstrap: dict) -> dict:
    by_district: dict[str, list[dict]] = {}
    for t in bootstrap["talukas"]:
        by_district.setdefault(t["districtCode"], []).append(t)
    out, warnings, unmatched, stamps = {}, [], [], set()
    for d in districts_list(client):
        lgd = lgd_district(d["name"], bootstrap["districts"])
        time.sleep(PAUSE_SECONDS)
        report = parse_report(client.call(REPORT, {"stateCode": STATE_CODE, "divisionCode": d["division"], "districtCode": d["code"]}))
        stamps.add(report["stamp"])
        rows = []
        for t in report["talukas"]:
            match = lgd_taluka(lgd["code"], t["name"], by_district.get(lgd["code"], []))
            if match is None:
                unmatched.append(f"{lgd['name']}: {t['name']}")
            warnings += percent_warnings(f"{lgd['name']}/{t['name']}", t)
            rows.append({"code": match["code"] if match else None, "maharainName": t["name"], **{k: v for k, v in t.items() if k != "name"}})
        if lgd["code"] in out:
            raise ValueError(f"Maharain districts {out[lgd['code']]['maharainName']} and {d['name']} both matched LGD {lgd['code']}")
        out[lgd["code"]] = {"districtCode": lgd["code"], "maharainName": d["name"], "district": report["district"], "talukas": rows}
    missing = {d["code"] for d in bootstrap["districts"]} - NO_MAHARAIN_DATA - set(out)
    if missing:
        raise ValueError(f"no Maharain data for LGD districts {sorted(missing)}")
    codes = [t["code"] for d in out.values() for t in d["talukas"] if t["code"]]
    if len(codes) != len(set(codes)):
        raise ValueError("two Maharain talukas matched the same LGD taluka")
    stamp = max(stamps)
    data_till = (date.fromisoformat(stamp) - timedelta(days=1)).isoformat()
    return {
        "meta": {
            "title": "Monsoon-season rainfall by taluka and revenue circle", "titleMr": "हंगामातील पाऊस (तालुका व महसूल मंडळ)",
            "publisher": "Maharain, Agriculture Department, Government of Maharashtra (hosted by NIC Pune)",
            "sourcePage": BASE + "rpt_circle_wise_report_june_september.php", "reportStamp": stamp, "dataTill": data_till,
            "seasonStart": f"{stamp[:4]}-06-01", "retrievedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "units": {"normalMm": "normal rainfall for the period, mm", "actualMm": "observed rainfall, mm", "percent": "actual as percent of normal"},
            "spans": {"season": "1 June to the day before the report", "junSep": "1 June to 30 September", "october": "1 October to date"},
            "limitations": ["Maharain says figures can be corrected for three days after they are first published.",
                            "A revenue circle total comes from its rain gauge or weather station; rain on a particular farm can differ.",
                            "The report gives 'percent of normal'. 100% means normal; 50% means half the normal rain."],
            "unmatchedTalukas": unmatched, "percentWarnings": warnings,
        },
        "districts": out,
    }


def publish(files: dict[str, bytes]) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    # Stage beside the output so os.replace never crosses filesystems (HUB_WORK_DIR may be elsewhere).
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=OUTPUT.parent))
    try:
        for name, content in files.items():
            (staging / name).write_bytes(content)
        for name in sorted(files, key=lambda n: n == INDEX):
            os.replace(staging / name, OUTPUT / name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> None:
    bootstrap = json.loads(BOOTSTRAP.read_text(encoding="utf8"))
    client = Client()
    try:
        client.call("")  # session cookie, as the browser page does
        dataset = build(client, bootstrap)
    except (ValueError, KeyError, OSError) as error:
        raise SystemExit(f"Maharain rainfall not imported; previous output kept. {error}")
    publish({INDEX: (json.dumps(dataset, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf8")})
    meta = dataset["meta"]
    talukas = sum(len(d["talukas"]) for d in dataset["districts"].values())
    circles = sum(len(t["circles"]) for d in dataset["districts"].values() for t in d["talukas"])
    print(json.dumps({"districts": len(dataset["districts"]), "talukas": talukas, "circles": circles, "dataTill": meta["dataTill"],
                      "unmatched": len(meta["unmatchedTalukas"]), "percentWarnings": len(meta["percentWarnings"])}))


if __name__ == "__main__":
    main()
