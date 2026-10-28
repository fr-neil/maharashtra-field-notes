# District statistics

Official historical reference data, not a live household or livestock inventory.

Livestock: Census 2019, Maharashtra table 3.11, PDF pages 48–49 (printed 35–36). There are 35 unique source units: 34 individual districts and one Mumbai City + Suburban unit. Each of the 36 location lookups points to the appropriate unit; aggregate reportingUnits once. The book explains the combined Mumbai census administration on PDF page21.

Land/crop context: DES Economic Survey2025–26 selected district indicators, PDFpage6 (printed317), with district order from page3. Average operational holding:2015–16; net-sown-area share and cropping intensity:2023–24 estimates; per-capita foodgrain production:2024–25 estimate. The two Mumbai agricultural rows are unavailable. These data do not establish household income or current farmer counts.

Reproduce with `python scripts/import-district-statistics.py --download`. Set HUB_PUBLIC_DIR/HUB_WORK_DIR when refreshing a static deployment. Requires Poppler pdftotext or optional PyMuPDF. Validation failures preserve the previous published index. Source URLs, SHA256 values, reference years, units and coverage are in index.json.
