# Maharashtra CWC-monitored reservoir subset

`index.json` contains real values parsed from the Central Water Commission's
latest publicly listed weekly bulletin. The 22 October 2026 issue covers 38
Maharashtra projects. It does **not** cover every Maharashtra dam or district.

The public source index is
https://rsms.cwc.gov.in/frameWork/web/bulletin-report-page . The importer opens
that public page in a fresh anonymous browser, reads its published PDF links,
chooses the latest non-future issue, downloads it, and parses Maharashtra's
region/state table. No login, private API, API key or app secret is used.

Current source volumes are in billion cubic metres (BCM). The displayed MCM
fields multiply BCM by 1,000. Volumes in the bulletin are rounded to 0.001 BCM;
individual percentages are therefore kept exactly as published. Full reservoir
level and measured reservoir level are separate fields in metres.

The importer independently checks the Maharashtra state-summary count and
storage/capacity totals, continuous row numbers, the PDF observation date,
duplicate names, and percentage compatibility with rounded volumes. A changed
format, failed request, invalid parse or older report preserves the existing
`index.json`; `refresh-status.json` records the failed check separately.

`latest-bulletin.pdf` is the original downloaded evidence, not a generated
report. Its SHA256 is in `index.json` and `provenance.json`. `reservoirs.csv`
provides a UTF-8 Excel-friendly export. The source observation date remains
unchanged when a refresh checks the same report again.

District matching is reviewed against government project / district sources.
Unreviewed matches remain null. A project district is not a service area,
village entitlement, canal allocation or forecast of days of water remaining.
The separate Nashik district snapshot is supplemental; overlapping projects
must not be added across these two datasets or their different dates.

Refresh on Ubuntu:

```sh
sudo apt-get install -y poppler-utils
python -m pip install playwright
python -m playwright install --with-deps chromium
python scripts/import-statewide-reservoirs.py
python scripts/test_statewide_reservoirs.py
```

On a machine with Chrome already installed, pass `--browser-executable` to its
binary. A local downloaded PDF can be verified with `--pdf` and its original
official `--url`. Source downloads and intermediate text stay in ignored
`outputs/reservoirs-statewide/`.
