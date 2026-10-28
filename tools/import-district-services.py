"""Refresh official Maharashtra district notices and public service contacts.

Uses the NIC district directory and public S3WaaS HTML. No login, CAPTCHA bypass,
REST authentication or third-party news scraping. Failed sections retain their
last successful records with explicit status and attempt dates.
"""
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = Path(os.environ.get("HUB_PUBLIC_DIR") or ROOT / "public")
WORK_ROOT = Path(os.environ.get("HUB_WORK_DIR") or ROOT / "outputs")
DIRECTORY_URL = "https://maharashtra.s3waas.gov.in/"
UA = "MaharashtraFieldNotes/1.0 (public district notices and contact directory)"
TOPICS = re.compile(r"drought|water|fodder|cattle|agricultur|farmer|crop|rainfall|irrigat|flood|silage|groundwater|livestock|tanker|seed|fertiliz|soil|pesticid|hail|sowing|farm\b|loan|subsid|disaster|\blake\b|\bpond\b|\bsilt\b|employment.guarantee|fee.relief|\bEGS\b|(?<![\u0900-\u097f])(?:रब्बी|खरीप|दुष्काळ|पाणी|पाण्य|चारा|चाऱ|शेत|पिक|पीक|पशु|पर्जन्य|भूजल|जल|पूर|पुर(?!वठा)|महापूर|अतिवृष|सिंचन|बियाण|खत|दूध|मुरघास|अनुदान|कर्ज|कृषि|कृषी|तलाव|गाळ|रोजगार\s*हमी|शुल्क\s*माफी)", re.I)
EXCLUDE = re.compile(r"suicid|आत्महत्या|elector|election|voter|मतदार|निवडणूक|recruitment|भरती|मुलाखत|वैद्यकीय\s*अधिकारी|ASDD|सखोल\s*पुनरीक्षण", re.I)
CONTACT_TOPICS = re.compile(r"disaster|emergency.operation|control\s*room|citizen|farmer|agricultur|veterinary|animal|livestock|water|collector|rescue|relief|आपत्ती|आपत्कालीन|नियंत्रण|नागरिक|शेतकरी|कृषि|कृषी|पशु|पाणी|जिल्हाधिकारी|किसान|बचाव", re.I)
DATE_RE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](20\d{2})\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+91[ \-]?)?(?:\(0\d{2,4}\)[ \-]?\d{6,8}|0\d{2,4}[ \-–]+\d{3}[ ]?\d{3,5}|0\d{2,4}[ \-–]+\d{4,8}|1800(?:[ \-]?\d){6,7}|[6-9]\d{9}|0\d{9,10}|\d{3,8})(?!\d)")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def clean(value: str) -> str:
    return " ".join(value.replace("\ufffd", " ").replace("\xa0", " ").split())


def ascii_digits(value):
    return "".join(str(unicodedata.digit(c)) if c.isdigit() else c for c in value)


class Node:
    def __init__(self, tag="root", attrs=None, parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs or []), parent
        self.children: list[Node | str] = []

    def text(self):
        if self.tag in {"script", "style", "noscript"}:
            return ""
        return clean(" ".join(c.text() if isinstance(c, Node) else c for c in self.children))

    def walk(self, tag=None):
        if tag is None or self.tag == tag:
            yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk(tag)


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(n)
        if tag not in VOID:
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def links(node, base):
    out = []
    for a in node.walk("a"):
        href = a.attrs.get("href", "")
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        url = urllib.parse.urljoin(base, href)
        if urllib.parse.urlparse(url).scheme not in {"http", "https", "tel"}:
            continue
        out.append({"url": url, "title": a.text(), "node": a})
    return out


def date_iso(value):
    m = DATE_RE.search(ascii_digits(value))
    if not m:
        return None
    try:
        return datetime(int(m[3]), int(m[2]), int(m[1])).date().isoformat()
    except ValueError:
        return None


def norm(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())


def category(value):
    for kind, pattern in [
        ("fodder", r"fodder|cattle|livestock|animal|silage|चारा|पशु|मुरघास|दूध"),
        ("water", r"water|groundwater|reservoir|tanker|irrigat|silt|पाणी|जल|सिंचन"),
        ("drought", r"drought|दुष्काळ"),
        ("agriculture", r"agricultur|farmer|crop|seed|fertiliz|soil|sowing|शेत|पीक|कृष|बियाण|खत"),
        ("weather", r"rainfall|flood|hail|disaster|पर्जन्य|पूर|अतिवृष्टी|आपत्ती"),
    ]:
        if re.search(pattern, value, re.I):
            return kind
    return "public-support"


def relevant(title):
    return bool(TOPICS.search(title)) and not EXCLUDE.search(title)


def documents(node, base):
    result, seen = [], set()
    for a in links(node, base):
        url = a["url"]
        if re.search(r"\.(pdf|docx?|xlsx?|zip)(?:$|\?)", url, re.I) and url not in seen:
            seen.add(url)
            result.append({"title": a["title"] or "Original document", "url": url})
    return result


def notice_rows(document, source_url):
    result = []
    for table in document.root.walk("table"):
        rows = list(table.walk("tr"))
        header = []
        for row in rows:
            cells = [c for c in row.children if isinstance(c, Node) and c.tag in {"td", "th"}]
            if not cells:
                continue
            if any(c.tag == "th" for c in cells):
                header = [c.text().lower() for c in cells]
                continue
            title = cells[0].text()
            description = next((cells[i].text() for i, label in enumerate(header) if i < len(cells) and ("description" in label or "वर्णन" in label)), "")
            if not relevant(title + " " + description):
                continue
            row_links = links(cells[0], source_url)
            detail_url = next((a["url"] for a in row_links if "/notice/" in a["url"]), None)
            docs = documents(row, source_url)
            dates = {}
            for i, cell in enumerate(cells):
                label = header[i] if i < len(header) else ""
                if "start" in label or "प्रारंभ" in label or "प्रारम्भ" in label or "आरंभ" in label or "सुरुवात" in label or "सुरवात" in label or "सुरू" in label:
                    dates["startDate"] = date_iso(cell.text())
                if "end" in label or "समाप्त" in label or "शेवट" in label:
                    dates["endDate"] = date_iso(cell.text())
            if not dates:
                values = [date_iso(c.text()) for c in cells if date_iso(c.text())]
                if values:
                    dates["startDate"] = values[0]
                    dates["endDate"] = values[1] if len(values) > 1 else None
            result.append({"title": title, "description": description, "url": detail_url or (docs[0]["url"] if docs else source_url), "sourceUrl": source_url, "documentUrl": docs[0]["url"] if docs else None, "documents": docs, **dates})
    return result


def parse_contacts(document, source_url, checked_at):
    # Keep independent cells/list items separate. Flattening entire tables confuses
    # neighbouring services and turns postal codes, fax numbers and extensions into
    # apparent helplines.
    candidates: list[tuple[str, str]] = []
    for table in document.root.walk("table"):
        headers = []
        for row in table.walk("tr"):
            cells = [c for c in row.children if isinstance(c, Node) and c.tag in {"td", "th"}]
            if any(c.tag == "th" for c in cells):
                headers = [c.text().lower() for c in cells]
                continue
            if not cells:
                continue
            phone_columns = [i for i, h in enumerate(headers) if re.search(r"phone|landline|contact\s*(no|number)|दूरध्वनी", h) and not re.search(r"fax|address", h)]
            if phone_columns:
                labels = [c.text() for i, c in enumerate(cells) if i < min(phone_columns) and not re.fullmatch(r"\d+[.]?", c.text()) and not re.search(r"@|\[at\]|email", c.text(), re.I)]
                label = clean(" ".join(labels))
                for i in phone_columns:
                    if i < len(cells):
                        candidates.append((label, cells[i].text()))
            else:
                independent = [c for c in cells if CONTACT_TOPICS.search(c.text()) and PHONE_RE.search(c.text())]
                if independent:
                    candidates += [("", c.text()) for c in independent]
                elif CONTACT_TOPICS.search(row.text()):
                    # A service-name cell followed by phone cells; omit email,
                    # fax/address cells wherever their headers identify them.
                    label = cells[0].text()
                    candidates += [(label, c.text()) for i, c in enumerate(cells[1:], 1) if i >= len(headers) or not re.search(r"fax|address|email", headers[i])]
    classes = {"list-text", "photoTxtContainer", "gen-card-content"}
    for n in document.root.walk():
        eligible = bool(classes.intersection(n.attrs.get("class", "").split())) or n.tag in {"li", "p"}
        if not eligible or any(c.tag in {"ul", "ol", "li", "table"} for c in n.walk() if c is not n):
            continue
        if "menu-item" in n.attrs.get("class", "") or any(a.attrs.get("href", "").startswith(("http", "/")) and not re.search(r"helpline|contact|disaster", a.attrs.get("href", ""), re.I) for a in n.walk("a")):
            continue
        text = n.text()
        if len(text) <= 250 and PHONE_RE.search(text):
            candidates.append(("", text))
    out, seen = [], set()
    for supplied_label, text in candidates:
        published_text = text
        text = ascii_digits(text)
        matches = list(PHONE_RE.finditer(text))
        if not matches:
            continue
        label = supplied_label or clean(text[:matches[0].start()].strip(" :–-,("))
        label = re.sub(r"^\d+[.)]?\s+", "", label)
        if len(label) < 3 or not CONTACT_TOPICS.search(label) or re.search(r"suicid|mental|आत्महत्या|senior\s*citizen|police|corona|covid|fax|railway|ज्येष्ठ|महिला|बाल\s*मदत|पोलीस|पीडीएस|रेल्वे|सायबर|election|निवडणूक", supplied_label + " " + text, re.I):
            continue
        for match in matches:
            value = clean(match.group())
            digits = re.sub(r"\D", "", value)
            if len(digits) < 3 or len(digits) > 13 or DATE_RE.fullmatch(value):
                continue
            if re.search(r"(?:pin|postal|fax|ext(?:ension)?)\s*[:.\-]?\s*$", text[max(0, match.start()-15):match.start()], re.I):
                continue
            # An isolated STD code or address postcode is not a callable number.
            if len(digits) == 3 and digits.startswith("0"):
                continue
            if re.fullmatch(r"20\d{2}|1800", digits):
                continue
            if len(digits) == 6 and (digits.startswith("4") or not supplied_label and len(matches) > 1 and value == matches[-1].group()):
                continue
            key = digits
            if key in seen:
                continue
            seen.add(key)
            out.append({"label": label, "number": value, "numberPublished": clean(published_text[match.start():match.end()]), "sourceUrl": source_url, "checkedAt": checked_at, "verification": "published-on-official-site", "operationallyTested": False})
    return out[:12]


def parse_detail(document, url):
    rows = notice_rows(document, url)
    if rows:
        item = rows[0]
        item["url"] = url
        return item
    titles = list(document.root.walk("h1"))
    title = titles[-1].text() if titles else ""
    if not relevant(title):
        return None
    docs = documents(document.root, url)
    published = None
    for meta in document.root.walk("meta"):
        if meta.attrs.get("property") == "article:published_time":
            published = meta.attrs.get("content")
    return {"title": title, "url": url, "sourceUrl": url, "publishedAt": published, "documentUrl": docs[0]["url"] if docs else None, "documents": docs}


def download(url, timeout, cache_dir, district_code, label):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read(3_000_000)
                if len(raw) >= 3_000_000:
                    raise ValueError("HTML response exceeded 3 MB limit")
                final_url = response.url
            break
        except (urllib.error.URLError, TimeoutError):
            if attempt:
                raise
            time.sleep(0.4)
    # A few official pages mix Windows punctuation bytes with UTF-8 Marathi.
    # Preserve both, instead of replacing original title punctuation.
    html = raw.decode("utf-8", errors="surrogateescape")
    html = "".join(bytes([ord(c)-0xdc00]).decode("cp1252", "replace") if 0xdc80 <= ord(c) <= 0xdcff else c for c in html)
    digest = hashlib.sha256(raw).hexdigest()
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{district_code}-{label}-{digest[:12]}.html").write_bytes(raw)
    document = Document(html)
    document.raw_bytes = len(raw)
    return document, final_url, digest


def resolve_websites(document, districts):
    lookup = {}
    for d in districts:
        for name in [d["name"], *d.get("aliases", []), "Mumbai City" if d["name"] == "Mumbai" else d["name"]]:
            lookup[norm(name)] = d["code"]
    result = {}
    for link in links(document.root, DIRECTORY_URL):
        code = lookup.get(norm(link["title"]))
        if code and code not in result:
            result[code] = link["url"].rstrip("/") + "/"
    return result


def scrape_district(district, website, previous, options, checked_at):
    code = district["code"]
    result = {"districtCode": code, "districtName": district["name"], "website": website, "checkedAt": checked_at, "lastAttemptAt": checked_at, "status": "unavailable", "helplines": [], "notices": [], "failures": [], "sources": []}
    cache = options.cache_dir
    if not website:
        result["failures"].append({"section": "directory", "error": "No website matched from official directory"})
        return preserve(result, previous)
    try:
        home, home_url, sha = download(website, options.timeout, cache, code, "home")
        result["website"] = home_url
        result["sources"].append({"url": home_url, "sha256": sha, "bytes": home.raw_bytes, "role": "district-home"})
    except Exception as e:
        result["failures"].append({"section": "home", "url": website, "error": str(e)[:220]})
        return preserve(result, previous)
    home_links = links(home.root, home_url)
    result["helplines"] = parse_contacts(home, home_url, checked_at)
    helpline_urls = list(dict.fromkeys(a["url"] for a in home_links if re.search(r"/helplines?(?:-\d+)?/?$", a["url"])))
    if not helpline_urls:
        helpline_urls = [urllib.parse.urljoin(home_url, "helpline/")]
    contact_fallbacks = list(dict.fromkeys(a["url"] for a in home_links if re.search(r"/(?:contact-directory|telephone-directory|government-offices|disaster-management|contact-us)/?$", a["url"])))
    contact_failures = []
    for u in [*helpline_urls[:1], *contact_fallbacks[:3]]:
        if result["helplines"] and u not in helpline_urls:
            break
        try:
            doc, final_url, sha = download(u, options.timeout, cache, code, "helpline")
            contacts = parse_contacts(doc, final_url, checked_at)
            existing = {re.sub(r"\D", "", x["number"]) for x in result["helplines"]}
            result["helplines"] += [x for x in contacts if re.sub(r"\D", "", x["number"]) not in existing]
            result["sources"].append({"url": final_url, "sha256": sha, "bytes": doc.raw_bytes, "role": "helplines"})
            result["helplinesCheckedAt"] = checked_at
        except Exception as e:
            contact_failures.append({"section": "helplines", "url": u, "error": str(e)[:220]})
    if not result["helplines"]:
        result["failures"] += contact_failures or [{"section": "helplines", "url": home_url, "error": "Checked official home/helpline/contact pages returned no matching published public-service numbers"}]
    else:
        result["helplinesCheckedAt"] = checked_at
    archive_urls = list(dict.fromkeys(a["url"] for a in home_links if re.search(r"/notice[_-]category/(announcements|notifications|public-notices)/?", a["url"])))[:2]
    if not archive_urls:
        archive_urls = [urllib.parse.urljoin(home_url, "notice_category/announcements/")]
    notices = []
    archive_success = False
    for u in archive_urls:
        try:
            doc, final_url, sha = download(u, options.timeout, cache, code, "announcements")
            notices += notice_rows(doc, final_url)
            result["sources"].append({"url": final_url, "sha256": sha, "bytes": doc.raw_bytes, "role": "notices-archive"})
            archive_success = True
        except Exception as e:
            result["failures"].append({"section": "notices", "url": u, "error": str(e)[:220]})
    already = {n["url"] for n in notices}
    candidates = []
    for a in home_links:
        if "/notice/" in a["url"] and relevant(a["title"]) and a["url"] not in already:
            candidates.append(a["url"])
            already.add(a["url"])
    for i, u in enumerate(candidates[:options.max_details]):
        try:
            doc, final_url, sha = download(u, options.timeout, cache, code, f"notice-{i}")
            parsed = parse_detail(doc, final_url)
            if parsed:
                notices.append(parsed)
                result["sources"].append({"url": final_url, "sha256": sha, "bytes": doc.raw_bytes, "role": "notice-detail"})
        except Exception as e:
            result["failures"].append({"section": "notice-detail", "url": u, "error": str(e)[:220]})
    unique = {}
    for n in notices:
        key = n.get("documentUrl") or n["url"]
        if key in unique:
            old = unique[key]
            if old["title"] != n["title"]:
                old.setdefault("alternateTitles", []).append(n["title"])
            for field in ["startDate", "endDate", "publishedAt"]:
                if not old.get(field) and n.get(field):
                    old[field] = n[field]
            continue
        n.update({"id": f"{code}-{hashlib.sha256(key.encode()).hexdigest()[:14]}", "category": category(n["title"] + " " + n.get("description", "")), "language": "mr" if re.search(r"[\u0900-\u097f]", n["title"]) else "en", "kind": "official-notice", "legalStatus": "not-assessed", "checkedAt": checked_at})
        n["dateStatus"] = "dated" if n.get("publishedAt") or n.get("startDate") or n.get("endDate") else "date-not-supplied"
        n["listingPeriodStatus"] = "ended" if n.get("endDate") and n["endDate"] < checked_at[:10] else "open-or-unspecified"
        unique[key] = n
    result["notices"] = sorted(unique.values(), key=lambda n: n.get("publishedAt") or n.get("startDate") or "", reverse=True)[:options.max_notices]
    if archive_success:
        result["noticesCheckedAt"] = checked_at
    if not result["notices"] and archive_success:
        result["noticesStatus"] = "no-matching-items-in-checked-pages"
    result["status"] = "partial" if result["failures"] else "checked"
    return preserve(result, previous)


def preserve(current, previous):
    previous = previous or {}
    retained = []
    for section in ["helplines", "notices"]:
        has_failure = any(f["section"].startswith(section[:-1]) or f["section"] == "home" for f in current["failures"])
        if not current[section] and previous.get(section) and has_failure:
            current[section] = previous[section]
            current[f"{section}CheckedAt"] = previous.get(f"{section}CheckedAt", previous.get("checkedAt"))
            retained.append(section)
    for notice in current["notices"]:
        notice["listingPeriodStatus"] = "ended" if notice.get("endDate") and notice["endDate"] < current["lastAttemptAt"][:10] else "open-or-unspecified"
        notice["dateStatus"] = "dated" if notice.get("publishedAt") or notice.get("startDate") or notice.get("endDate") else "date-not-supplied"
    if retained:
        current["retainedLastGood"] = retained
        current["status"] = "stale-partial"
        seen_sources = {s["url"] for s in current.setdefault("sources", [])}
        for source in previous.get("sources", []):
            if source["url"] not in seen_sources:
                current["sources"].append({**source, "retainedLastGood": True})
    return current


def write_exports(payload, destination):
    """Small, downloadable evidence tables using the exact imported values."""
    fields = {
        "contacts.csv": ["districtCode", "districtName", "label", "number", "numberPublished", "sourceUrl", "checkedAt", "verification"],
        "notices.csv": ["districtCode", "districtName", "title", "description", "alternateTitles", "startDate", "endDate", "publishedAt", "category", "language", "url", "sourceUrl", "documentUrl", "listingPeriodStatus", "legalStatus", "checkedAt"],
    }
    for filename, columns in fields.items():
        section = "helplines" if filename == "contacts.csv" else "notices"
        with (destination / filename).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for district in payload["districts"]:
                for item in district[section]:
                    row = {"districtCode": district["districtCode"], "districtName": district["districtName"], **item}
                    if isinstance(row.get("alternateTitles"), list):
                        row["alternateTitles"] = " | ".join(row["alternateTitles"])
                    writer.writerow(row)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=PUBLIC / "data/district-services/index.json")
    p.add_argument("--cache-dir", type=Path, default=WORK_ROOT / "district-services/cache")
    p.add_argument("--district", action="append", help="Only refresh selected LGD district codes; retain others")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--timeout", type=float, default=12)
    p.add_argument("--max-notices", type=int, default=12)
    p.add_argument("--max-details", type=int, default=5)
    options = p.parse_args(argv)
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    bootstrap = json.loads((PUBLIC / "data/bootstrap.json").read_text(encoding="utf-8"))
    districts = bootstrap["districts"]
    old = json.loads(options.output.read_text(encoding="utf-8")) if options.output.exists() else {"districts": []}
    previous = {d["districtCode"]: d for d in old.get("districts", [])}
    directory, directory_final, directory_sha = download(DIRECTORY_URL, options.timeout, options.cache_dir, "MH", "directory")
    websites = resolve_websites(directory, districts)
    if len(websites) != len(districts):
        raise RuntimeError(f"Official directory matched {len(websites)} of {len(districts)} districts; refuse an incomplete geographic overwrite")
    selected = [d for d in districts if not options.district or d["code"] in options.district]
    if options.district and set(options.district) - {d["code"] for d in districts}:
        raise ValueError("Unknown district code; use an LGD code from public/data/bootstrap.json")
    results = dict(previous)
    with ThreadPoolExecutor(max_workers=max(1, min(options.workers, 10))) as pool:
        futures = {pool.submit(scrape_district, d, websites[d["code"]], previous.get(d["code"]), options, checked_at): d for d in selected}
        for f in as_completed(futures):
            d = futures[f]
            try:
                result = f.result()
            except Exception as e:
                result = preserve({"districtCode": d["code"], "districtName": d["name"], "website": websites[d["code"]], "checkedAt": checked_at, "lastAttemptAt": checked_at, "status": "unavailable", "helplines": [], "notices": [], "sources": [], "failures": [{"section": "home", "error": str(e)[:220]}]}, previous.get(d["code"]))
            results[d["code"]] = result
            print(f"{d['code']} {d['name']}: {len(result['helplines'])} contacts, {len(result['notices'])} notices ({result['status']})", flush=True)
    ordered = [results[d["code"]] for d in districts if d["code"] in results]
    payload = {"meta": {"title": "Official Maharashtra district services and notices", "retrievedAt": checked_at, "directorySourceUrl": DIRECTORY_URL, "directoryFinalUrl": directory_final, "directorySha256": directory_sha, "expectedDistrictCount": len(districts), "districtCount": len(ordered), "contactCount": sum(len(d["helplines"]) for d in ordered), "noticeCount": sum(len(d["notices"]) for d in ordered), "districtsWithContacts": sum(bool(d["helplines"]) for d in ordered), "districtsWithNotices": sum(bool(d["notices"]) for d in ordered), "refreshMethod": "Public official HTML; no authenticated REST or RSS dependency", "recommendedRefresh": "daily", "dateSemantics": "startDate/endDate are website listing dates, not automatically legal effective/expiry dates; publishedAt only where explicitly supplied", "contactSemantics": "Numbers verified as published on an official page; no phone calls or operational tests", "coverageNote": "Bounded check of home and current announcements; no claim to exhaust historical notices or establish absence of a restriction"}, "districts": ordered}
    payload["meta"]["directoryBytes"] = directory.raw_bytes
    payload["meta"]["datedNoticeCount"] = sum(n["dateStatus"] == "dated" for d in ordered for n in d["notices"])
    payload["meta"]["downloads"] = {"contacts": "contacts.csv", "notices": "notices.csv"}
    failed = [d for d in selected if results[d["code"]].get("failures")]
    payload["meta"]["selectedDistrictCount"] = len(selected)
    payload["meta"]["selectedDistrictFailureCount"] = len(failed)
    payload["meta"]["refreshStatus"] = "partial" if failed else "complete"
    options.output.parent.mkdir(parents=True, exist_ok=True)
    temp = options.output.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(options.output)
    write_exports(payload, options.output.parent)
    print(json.dumps(payload["meta"], ensure_ascii=True), flush=True)
    if failed:
        print("District refresh failures after saving available/retained data: " + ", ".join(d["code"] + " " + d["name"] for d in failed), file=sys.stderr, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
