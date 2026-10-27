# Data assets and provenance

Checked 27 October 2026. These files seed a source and research hub, not a live measurement dashboard.

- `bootstrap.json`: 36 districts and 358 talukas from the official Maharashtra LGD directory, 15 separately named/coded Nashik talukas, the 15-row workspace source register, 6 dated source/research updates, and 7 curated research briefs.
- `maharashtra-districts.geojson`: district polygons converted from Guneet Narula's MIT-licensed state topology. Current names and LGD codes are crosswalked through Census codes and the Palghar name. This is a visualization layer with Census 2011/2014 source vintages, not current legal-boundary evidence.
- `MAP-LICENSE.txt`: upstream MIT license, Copyright 2020 Guneet Narula. The upstream repository credits the DataMeet community; retain both credits in the map UI.
- `library/`: curated, non-sensitive project briefs. They summarize questions and process, omit unreviewed feeding/disinfectant recipes, and link to original public sources.
- `districts.csv`: directory snapshot with explicit checked date. Administrative counts are not field-observation coverage or household participation.

Official geography: https://mahavillages.mahabhumi.gov.in/all_list.php

Nashik English taluka names: https://nashik.gov.in/en/about-district/administrative-setup/tehsil/

Nashik LGD codes/Marathi names were fetched from the directory's own `lgdajaxpro.php` endpoint using `lgddistrict=487`.

All 358 taluka names and codes were fetched through the directory's own `all_list_back.php` endpoint using the same public search parameters as its browser interface (`lgdState=27`, `lgdlocalbody=T`, `postAction=AjaxSearchRecord1dist`). No village directory has been imported.

Boundary source: https://github.com/guneetnarula/indian-district-boundaries

There are no seeded water levels, weather measurements, household incomes, operational field reports or fodder stock quantities. Source checks retain their access/provenance gaps. All source content has its original reference year; update entries are hub research/audit notes, not new government notifications.


## Second release

The verified village index now covers 1,981 Nashik villages across all 15 talukas; see villages-README.md for coverage and retrieval limitations. Other districts accept manual village reports. district-points.json supplies approximate representative query points, not station or village coordinates. Open-Meteo forecasts are fetched on view and attributed in the interface; they are not seeded field observations.

## Official observation snapshots (27 October 2026)

These folders hold dated official readings, each with `index.json`, a CSV and `provenance.json` (publisher, URL, SHA256, report date, retrieval time, method). Report or observation dates are kept separate from retrieval dates. The statement above about seeded water levels still holds for field reports; these are official observations, not peer reports.

- `rainfall/`: IMD district rainfall report for 27 Oct 2026 (day and 1–27 Oct), all 36 districts, plus the report PDF. District averages, not village rainfall.
- `groundwater/`: GSDA telemetry summarised per well and district from 1,085,008 six-hourly readings (1 Jan – 26 Oct 2026). 50,551 out-of-range readings and 4,805 spike-days removed; 102 unstable wells left out of district medians. The source CSV (145 MB) is not published; its URL and SHA256 are in `provenance.json`.
- `reservoirs/`: Nashik district dam storage report for 23 Oct 2026, transcribed from the scanned PDF and checked against its totals.

Depth to groundwater and dam storage are not village water availability; see each file's `limitations`.
