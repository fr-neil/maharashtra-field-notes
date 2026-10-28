# Maharashtra Field Notes · महाराष्ट्र फील्ड नोट्स

Live: https://fr-neil.github.io/maharashtra-field-notes/

A Marathi-first, read-only site for farmers and villagers in the 2026 drought. Choose a district and taluka to see:

- **Drought status** from the Relief and Rehabilitation Department order of 25 Sep 2026 (265 talukas), plus the 39 talukas reported added on 14 Oct.
- **The 12 relief measures** in plain Marathi, filtered by who you are (land, loan, pump, animals, labourer, student), with where to ask and what to carry. The order's own wording is quoted.
- **Crop loss**: the separate compensation order still to come, the announced ₹5,000 advance, panchnama and crop insurance (14447).
- **Rain this season** by taluka and revenue circle (Maharain) and this month by district (IMD); groundwater at GSDA monitoring wells; Nashik dam storage.
- **Dams across Maharashtra**: the 38 reservoirs in the Central Water Commission's weekly bulletin, each with its district from a named government source. The two boundary dams, Upper Wardha and Manjara, are listed for both districts.
- **District notices and contacts** from official district websites, **villages** for every district, and **livestock (2019 census) and farm statistics** with their reference years.
- **Animals and fodder**: what is announced and what is not yet published, care that does not wait for a scheme, questions to ask, helpline 1962.
- **Share** as a WhatsApp message, print a notice-board page, or listen in Marathi.

Every number shows its date and source. The 265 officially documented talukas and 39 news-reported additions are counted separately. Reported additions remain pending official confirmation, including in WhatsApp and print summaries. This is not a government website; confirm at the Talathi or tehsil office.

The language choice is kept in the URL and shared links, and restores on reload and browser back/forward. Source cards, directory controls and the research briefs have authored Marathi translations. Original documents and English CSV headers are labelled. Rain and water information appears before the expandable relief list.

## Data and refresh

Each folder under `data/` holds `index.json`, a CSV and provenance (publisher, original URL, SHA256, report date, retrieval time). `.github/workflows/refresh-data.yml` runs the importers in `tools/` at 19:00 IST, with a backup run at 22:15 IST:
- IMD and Maharain rainfall and district notices: daily
- GSDA groundwater: Mondays
- the CWC dam bulletin: Mondays and Fridays

A manual run refreshes all of them, and `data/refresh-status.json` records each feed's result. The workflow explicitly stages public files, uploads a Pages artifact and deploys it after pushes and refreshes. An importer that cannot validate its source leaves the previous files in place; the available snapshot is deployed and the failed import is flagged. Repository tooling is excluded from the public artifact.

Pages are `noindex`. Built from the `maharashtra-hub` source with `npm run build:static`.
