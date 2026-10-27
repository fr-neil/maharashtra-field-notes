# Maharashtra Field Notes · महाराष्ट्र फील्ड नोट्स

Live: https://fr-neil.github.io/maharashtra-field-notes/

A Marathi-first, read-only site for farmers and villagers in the 2026 drought. Choose a district and taluka to see:

- **Drought status** from the Relief and Rehabilitation Department order of 25 Sep 2026 (265 talukas), plus the 39 talukas reported added on 14 Oct.
- **The 12 relief measures** in plain Marathi, filtered by who you are (land, loan, pump, animals, labourer, student), with where to ask and what to carry. The order's own wording is quoted.
- **Crop loss**: the separate compensation order still to come, the announced ₹5,000 advance, panchnama and crop insurance (14447).
- **Rain this season** by taluka and revenue circle (Maharain) and this month by district (IMD); groundwater at GSDA monitoring wells; Nashik dam storage.
- **Animals and fodder**: what is announced and what is not yet published, care that does not wait for a scheme, questions to ask, helpline 1962.
- **Share** as a WhatsApp message, print a notice-board page, or listen in Marathi.

Every number shows its date and source. News reports are labelled as news, not as orders. This is not a government website; confirm at the Talathi or tehsil office.

## Data and refresh

Each folder under `data/` holds `index.json`, a CSV and provenance (publisher, original URL, SHA256, report date, retrieval time). `.github/workflows/refresh-data.yml` refreshes IMD and Maharain rainfall daily at 19:00 IST and GSDA groundwater on Mondays, using the importers in `tools/`. An importer that cannot validate its source leaves the previous files in place, and the workflow run is marked as failed.

Pages are `noindex`. Built from the `maharashtra-hub` source with `npm run build:static`.
