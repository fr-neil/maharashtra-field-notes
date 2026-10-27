# Maharashtra Field Notes (public read-only copy)

Live: https://fr-neil.github.io/maharashtra-field-notes/

Official water and weather readings for Maharashtra districts, each with its date and source:

- **Rainfall**: IMD district report, month to date, all 36 districts (`data/rainfall/`).
- **Groundwater**: depth to water at GSDA telemetry wells, with the change since May and over 30 days (`data/groundwater/`). Wells in 21 districts stopped reporting around 2 June 2026.
- **Dam storage**: the Nashik district report (`data/reservoirs/`).
- A 7-day point forecast from Open-Meteo, plus the source register, research notes and the official location directory.

Each data folder holds `index.json`, a CSV and `provenance.json` (publisher, original URL, SHA256, report date and retrieval time). A well's depth or a dam's storage is not a village's water supply; see each file's `limitations`.

This is a static build with no sign-in. Field reports, the fodder board and follow-ups run in the peers' private workspace. Pages are marked `noindex`.

Built from the `maharashtra-hub` source with `npm run build:static`; the import scripts are in its `scripts/` folder.
