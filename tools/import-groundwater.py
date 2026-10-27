#!/usr/bin/env python3
"""Summarise GSDA's six-hourly groundwater telemetry (NWDP CSV) by station and district.

The source reports depth to water below the surface as a negative number of
metres. Telemetry contains placeholders and sensor spikes, so each station is
reduced to daily medians, out-of-range readings are dropped and one-day spikes
are removed with a rolling-median (Hampel) filter. Counts of everything removed
are published with the results. A failed run leaves the previous output intact.
No third-party Python packages are needed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
import statistics
import tempfile
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
# Overridable so the public site repository can refresh its own copy (see its workflow).
PUBLIC = Path(os.environ.get("HUB_PUBLIC_DIR") or APP / "public")
WORK_ROOT = Path(os.environ.get("HUB_WORK_DIR") or APP / "outputs")
OUTPUT = PUBLIC / "data" / "groundwater"
WORK = WORK_ROOT / "groundwater"
CSV_NAME = "gwl_tel_6_hourly_maharashtra_gw_mh_2026_2030.csv"
DATASET_PAGE = "https://nwdp.nwic.gov.in/dataset/ground-water-level-telemetry-daily-maharashtra-gw"
CSV_URL = ("https://nwdp.nwic.gov.in/dataset/3e0f97ab-5bf6-498f-96c5-7b23dfdb9a70/resource/"
           "7a821ef4-b73b-4243-9a42-4f1a6f300230/download/" + CSV_NAME)
HEADER = ["SlNo", "Station", "Agency", "State LGD Code", "State", "District LGD Code", "District", "Tehsil",
          "Block", "Village", "River", "Basin", "Tributary", "Subtributary", "SubSubtributary", "Local River",
          "Latitude", "Longitude", "RL_MSL", "Data Acquisition Time", "Groundwater Level Telemetry 6 Hourly (meter)"]
CHUNK = 8 * 1024 * 1024
INDEX = "index.json"

MAX_DEPTH_M = 200.0          # deeper readings are treated as sensor faults
SPIKE_WINDOW_DAYS = 3        # Hampel window: +/- days around each daily median
SPIKE_MIN_M = 1.0            # never call a deviation under 1 m a spike
SPIKE_SIGMAS = 3.0
CURRENT_WITHIN_DAYS = 7      # station is current if its last clean day is this close to the dataset's last day
LATEST_WINDOW_DAYS = 3       # current depth = median of the last N clean days
CHANGE_LAG_DAYS = 30
CHANGE_TOLERANCE_DAYS = 3
MIN_CLEAN_DAYS = 14
MIN_REFERENCE_DAYS = 10
ERRATIC_SHARE = 0.2          # >20 % readings excluded, or >20 % days flagged as spikes
TREND_THRESHOLD_M = 0.5      # 30-day change smaller than this is "steady"
FLAT_WINDOW_DAYS = 90        # a well that barely moves for this long may be dry below the sensor, or the sensor stuck
FLAT_RANGE_M = 0.1
MIN_FLAT_DAYS = 30
PRE_MONSOON_MONTH = 5        # May: the usual pre-monsoon low


@dataclass
class Station:
    name: str
    district_code: str
    district: str
    tehsil: str
    block: str
    village: str
    lat: float
    lon: float
    elevation: float | None
    readings: int = 0
    excluded: int = 0
    by_day: dict[date, list[float]] = field(default_factory=lambda: defaultdict(list))


def clean_text(value: str) -> str:
    return "" if value.strip() in {"", "-"} else value.strip()


def read_stations(path: Path) -> tuple[dict[tuple, Station], int]:
    stations: dict[tuple, Station] = {}
    rows = 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        if header != HEADER:
            raise ValueError(f"unexpected CSV header: {header}")
        for row in reader:
            rows += 1
            if len(row) != len(HEADER):
                raise ValueError(f"row {rows} has {len(row)} columns, expected {len(HEADER)}")
            if row[4] != "Maharashtra":
                raise ValueError(f"row {rows} is not Maharashtra: {row[4]}")
            key = (row[1], row[5], row[16], row[17])
            station = stations.get(key)
            if station is None:
                elevation = float(row[18]) if clean_text(row[18]) else None
                station = Station(row[1], row[5], row[6], clean_text(row[7]), clean_text(row[8]), clean_text(row[9]),
                                  float(row[16]), float(row[17]), elevation)
                stations[key] = station
            station.readings += 1
            depth = -float(row[20])
            if not 0 < depth <= MAX_DEPTH_M:
                station.excluded += 1
                continue
            station.by_day[datetime.strptime(row[19], "%d-%m-%Y %H:%M").date()].append(depth)
    if not rows:
        raise ValueError("CSV has no data rows")
    return stations, rows


def despike(daily: dict[date, float]) -> tuple[dict[date, float], int]:
    """Drop daily values far from the median of the surrounding days (Hampel filter)."""
    clean, spikes = {}, 0
    for day, value in daily.items():
        window = [daily[d] for d in (day + timedelta(days=o) for o in range(-SPIKE_WINDOW_DAYS, SPIKE_WINDOW_DAYS + 1))
                  if d in daily and d != day]
        if len(window) < 2:
            clean[day] = value
            continue
        centre = statistics.median(window)
        spread = statistics.median(abs(v - centre) for v in window) * 1.4826
        if abs(value - centre) > max(SPIKE_SIGMAS * spread, SPIKE_MIN_M):
            spikes += 1
        else:
            clean[day] = value
    return clean, spikes


def window_median(series: dict[date, float], start: date, end: date) -> float | None:
    values = [v for d, v in series.items() if start <= d <= end]
    return statistics.median(values) if values else None


def levels(clean: dict[date, float], last: date) -> tuple[float | None, float | None, float | None, bool]:
    """Current depth, 30-day change, May median and the flat-line flag for one station."""
    current = window_median(clean, last - timedelta(days=LATEST_WINDOW_DAYS - 1), last)
    lag = last - timedelta(days=CHANGE_LAG_DAYS)
    earlier = window_median(clean, lag - timedelta(days=CHANGE_TOLERANCE_DAYS), lag + timedelta(days=CHANGE_TOLERANCE_DAYS))
    change = None if earlier is None or current is None else current - earlier
    may_days = [v for d, v in clean.items() if d.month == PRE_MONSOON_MONTH and d.year == last.year]
    may = statistics.median(may_days) if len(may_days) >= MIN_REFERENCE_DAYS else None
    recent = [v for d, v in clean.items() if d > last - timedelta(days=FLAT_WINDOW_DAYS)]
    flat = len(recent) >= MIN_FLAT_DAYS and max(recent) - min(recent) < FLAT_RANGE_M
    return current, change, may, flat


def station_metrics(station: Station, dataset_last: date, weeks: list[date]) -> dict:
    daily = {d: statistics.median(v) for d, v in sorted(station.by_day.items())}
    clean, spikes = despike(daily)
    last = max(clean) if clean else None
    current, change, may, flat = levels(clean, last) if last else (None, None, None, False)
    erratic = (station.excluded > ERRATIC_SHARE * station.readings) or (spikes > ERRATIC_SHARE * max(len(daily), 1))
    if not last or len(clean) < MIN_CLEAN_DAYS:
        status = "insufficient"
    elif (dataset_last - last).days > CURRENT_WITHIN_DAYS:
        status = "stale"
    else:
        status = "current"
    weekly = [window_median(clean, w, w + timedelta(days=6)) for w in weeks]
    return {
        "station": station.name, "districtCode": station.district_code, "sourceDistrict": station.district,
        "tehsil": station.tehsil, "block": station.block, "village": station.village,
        "lat": station.lat, "lon": station.lon, "elevationM": station.elevation,
        "status": status, "erratic": erratic, "flat": flat, "lastObservation": last.isoformat() if last else None,
        "depthM": rnd(current), "change30dM": rnd(change), "mayDepthM": rnd(may),
        "vsMayM": rnd(None if may is None or current is None else may - current),
        "readings": station.readings, "excludedReadings": station.excluded,
        "days": len(daily), "spikeDays": spikes, "cleanDays": len(clean),
        "weekly": [rnd(v) for v in weekly],
    }


def rnd(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def trend(change: float | None) -> str | None:
    if change is None:
        return None
    if change > TREND_THRESHOLD_M:
        return "falling"
    return "rising" if change < -TREND_THRESHOLD_M else "steady"


def median_of(values: list) -> float | None:
    present = [v for v in values if v is not None]
    return rnd(statistics.median(present)) if present else None


def district_summary(code: str, stations: list[dict]) -> dict:
    usable = [s for s in stations if s["status"] == "current" and not s["erratic"]]
    trends = [trend(s["change30dM"]) for s in usable]
    return {
        "districtCode": code, "sourceDistrict": stations[0]["sourceDistrict"],
        "stations": len(stations), "usableStations": len(usable),
        "staleStations": sum(s["status"] == "stale" for s in stations),
        "erraticStations": sum(s["erratic"] for s in stations),
        "flatStations": sum(s["flat"] for s in usable),
        "insufficientStations": sum(s["status"] == "insufficient" for s in stations),
        "medianDepthM": median_of([s["depthM"] for s in usable]),
        "medianChange30dM": median_of([s["change30dM"] for s in usable]),
        "medianVsMayM": median_of([s["vsMayM"] for s in usable]),
        "falling": trends.count("falling"), "rising": trends.count("rising"), "steady": trends.count("steady"),
        "latestObservation": max((s["lastObservation"] for s in usable), default=None),
        "latestAnyObservation": max((s["lastObservation"] for s in stations if s["lastObservation"]), default=None),
    }


def download(target: Path) -> dict:
    """Fetch the CSV in byte ranges; the portal times out on single large downloads."""
    agent = {"User-Agent": "MaharashtraFieldNotes/1.0 (public official data import)"}
    parts, offset, total, etag, modified = [], 0, None, None, None
    while total is None or offset < total:
        request = urllib.request.Request(CSV_URL, headers={**agent, "Range": f"bytes={offset}-{offset + CHUNK - 1}"})
        with urllib.request.urlopen(request, timeout=120) as response:
            if response.status != 206:
                raise ValueError(f"expected HTTP 206, got {response.status}")
            span, size = response.headers["Content-Range"].split()[1].split("/")
            first = int(span.split("-")[0])
            if first != offset or (total is not None and int(size) != total):
                raise ValueError(f"unexpected Content-Range {response.headers['Content-Range']}")
            total, etag, modified = int(size), response.headers.get("ETag"), response.headers.get("Last-Modified")
            chunk = response.read()
        parts.append(chunk)
        offset += len(chunk)
    raw = b"".join(parts)
    if len(raw) != total:
        raise ValueError(f"downloaded {len(raw)} of {total} bytes")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    provenance = {"sourceUrl": CSV_URL, "bytes": total, "sha256": hashlib.sha256(raw).hexdigest(), "etag": etag,
                  "lastModifiedHeader": modified, "checkedDate": date.today().isoformat(),
                  "method": f"Complete sequential HTTP byte ranges, {CHUNK // 1048576} MiB per request; exact content ranges and lengths checked."}
    (target.parent / "download-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf8")
    return provenance


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def build(path: Path, download_info: dict) -> tuple[dict, dict[str, dict]]:
    stations, rows = read_stations(path)
    all_days = [d for s in stations.values() for d in s.by_day]
    first, last = min(all_days), max(all_days)
    weeks = [first + timedelta(days=7 * i) for i in range((last - first).days // 7 + 1)]
    metrics = sorted((station_metrics(s, last, weeks) for s in stations.values()),
                     key=lambda m: (m["districtCode"], m["tehsil"], m["station"]))
    by_district: dict[str, list[dict]] = defaultdict(list)
    for m in metrics:
        by_district[m["districtCode"]].append(m)
    summaries = [district_summary(code, items) for code, items in sorted(by_district.items())]
    state = {**district_summary("27", metrics), "sourceDistrict": "Maharashtra"}
    meta = {
        "title": "Groundwater depth at GSDA telemetry stations",
        "titleMr": "भूजल पातळी (GSDA निरीक्षण विहिरी)",
        "publisher": "Groundwater Surveys and Development Agency (GSDA), Government of Maharashtra, via the National Water Data Portal",
        "datasetPage": DATASET_PAGE, "sourceUrl": CSV_URL, "sha256": download_info["sha256"], "bytes": download_info["bytes"],
        "sourceLastModified": download_info.get("lastModifiedHeader"), "downloadedOn": download_info.get("checkedDate"),
        "builtAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "firstObservation": first.isoformat(), "lastObservation": last.isoformat(),
        "weekStarts": [w.isoformat() for w in weeks],
        "rows": rows, "stations": len(metrics), "districts": len(summaries),
        "excludedReadings": sum(m["excludedReadings"] for m in metrics), "spikeDays": sum(m["spikeDays"] for m in metrics),
        "statusCounts": {k: sum(m["status"] == k for m in metrics) for k in ("current", "stale", "insufficient")},
        "erraticStations": sum(m["erratic"] for m in metrics),
        "refreshMode": "manual run of scripts/import-groundwater.py [--download]", "automaticRefresh": False,
        "method": {
            "signConvention": "Source values are negative metres; NWDP describes them as groundwater depth below the surface. Shown here as positive depth.",
            "validRange": f"0 < depth <= {MAX_DEPTH_M:g} m; other readings excluded and counted",
            "daily": "median of the day's six-hourly readings (timestamps as published, assumed IST)",
            "spikes": f"daily value dropped if more than max({SPIKE_SIGMAS:g} x scaled MAD, {SPIKE_MIN_M:g} m) from the median of the surrounding +/-{SPIKE_WINDOW_DAYS} days",
            "current": f"median of the last {LATEST_WINDOW_DAYS} clean days; station 'current' if that is within {CURRENT_WITHIN_DAYS} days of the dataset's last observation",
            "change30d": f"current depth minus the median of clean days {CHANGE_LAG_DAYS}+/-{CHANGE_TOLERANCE_DAYS} days earlier; positive = water further down",
            "vsMay": f"May median depth minus current depth (needs {MIN_REFERENCE_DAYS}+ clean May days); positive = water higher than before the monsoon",
            "trend": f"falling / rising when the 30-day change exceeds {TREND_THRESHOLD_M:g} m; otherwise steady",
            "erratic": f"more than {ERRATIC_SHARE:.0%} of readings excluded or {ERRATIC_SHARE:.0%} of days flagged as spikes; left out of district medians",
            "flat": f"clean readings varied less than {FLAT_RANGE_M:g} m over the last {FLAT_WINDOW_DAYS} days; the well may be dry below the sensor or the sensor stuck. Shown with a note and kept in medians",
        },
        "limitations": [
            "A station is one monitoring well. Its depth does not tell you the water level in another village's well or borewell.",
            "Depth to water is not a volume. It cannot say how many litres a village has left or how long they will last.",
            "District medians use only current, stable stations and can hide large local differences.",
            "This CSV starts on 1 January 2026, so there is no comparison with the same date last year.",
            "Mumbai City and Mumbai Suburban have no stations in this dataset.",
        ],
    }
    return {"meta": meta, "state": state, "districts": summaries}, {code: {"districtCode": code, "weekStarts": meta["weekStarts"], "stations": items}
                                                    for code, items in by_district.items()}


def publish(files: dict[str, bytes], prune: str) -> None:
    """Stage every file, then move each into OUTPUT with index.json last; drop old files matching `prune`."""
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


def write_outputs(index: dict, districts: dict[str, dict]) -> None:
    files = {f"district-{code}.json": (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf8")
             for code, payload in districts.items()}
    rows = [{k: v for k, v in s.items() if k != "weekly"} for payload in districts.values() for s in payload["stations"]]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    files["stations.csv"] = ("﻿" + stream.getvalue()).encode("utf8")
    provenance = {k: index["meta"][k] for k in ("publisher", "datasetPage", "sourceUrl", "sha256", "bytes", "sourceLastModified",
                                                "downloadedOn", "builtAt", "firstObservation", "lastObservation", "method")}
    provenance["script"] = "scripts/import-groundwater.py"
    files["provenance.json"] = (json.dumps(provenance, ensure_ascii=False, indent=2) + "\n").encode("utf8")
    files[INDEX] = (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode("utf8")
    publish(files, prune="district-*.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=WORK / CSV_NAME, help="Local copy of the NWDP CSV")
    parser.add_argument("--download", action="store_true", help="Download a fresh copy before importing")
    args = parser.parse_args()
    provenance_file = args.csv.parent / "download-provenance.json"
    if args.download:
        info = download(args.csv)
    elif provenance_file.exists():
        info = json.loads(provenance_file.read_text(encoding="utf8"))
        if sha256_file(args.csv) != info["sha256"]:
            raise SystemExit("CSV does not match download-provenance.json; re-run with --download. Previous output kept.")
    else:
        raise SystemExit("No download-provenance.json beside the CSV; run with --download. Previous output kept.")
    try:
        index, districts = build(args.csv, info)
    except (ValueError, csv.Error) as error:
        raise SystemExit(f"Groundwater not imported; previous output kept. {error}")
    write_outputs(index, districts)
    meta = index["meta"]
    print(json.dumps({k: meta[k] for k in ("rows", "stations", "districts", "firstObservation", "lastObservation",
                                           "excludedReadings", "spikeDays", "statusCounts", "erraticStations")}))


if __name__ == "__main__":
    main()
