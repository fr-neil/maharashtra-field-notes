# Official district services and notices

The importer checks all 36 district websites linked by the [official Maharashtra NIC/S3WaaS directory](https://maharashtra.s3waas.gov.in/). It reads public home, helpline, telephone/contact directory and current announcement pages. This is a bounded current-page check, not a search of every historical order or news article.

`index.json` includes actual published contact values and official notice records, with original titles, listing dates and original document links. `contacts.csv` and `notices.csv` provide the same values for download. Marathi digits are normalized in `number`; `numberPublished` retains the published form. Local numbers remain local: an absent area code is never inferred. A number appearing on an official page establishes provenance, not that the service answers calls or that the authority has recently revalidated it.

`startDate` and `endDate` are website listing dates. They do not automatically establish an order's legal effective date, expiry or continuing enforceability. `publishedAt` appears only when supplied explicitly. `listingPeriodStatus: ended` concerns the listing period; `legalStatus: not-assessed` applies to every record. News is not imported as a legal order. No absence of a notice should be treated as proof that a restriction does not exist.

Each district has its own `checkedAt`, `lastAttemptAt`, `status`, `failures` and section check dates. Each successfully downloaded source has its exact URL, SHA-256 and raw byte count. Failed empty sections retain previous data as `stale-partial`, with their previous successful check time and explicit `retainedLastGood` sections. A failed official directory or incomplete geographic match aborts before replacing the last dataset. A successful page with no matching notices is labeled `no-matching-items-in-checked-pages`.

Run daily from the repository root:

```sh
python scripts/import-district-services.py --workers 6 --timeout 12
python -m unittest discover -s scripts -p test_district_services.py
```

The importer uses only Python's standard library. Raw source HTML is saved under the ignored `outputs/district-services/cache/` directory. A bounded retry handles transient public-server/DNS failures; TLS verification stays enabled. Authenticated WordPress REST endpoints and failing RSS feeds are not dependencies. A specific district can be retried with `--district 477` (the LGD district code); other districts keep their own previous check timestamps.

For the public repository layout, set `HUB_PUBLIC_DIR` to the directory containing `data/bootstrap.json`, and `HUB_WORK_DIR` to a writable work directory. Defaults are `public/` and `outputs/` under the app checkout. `--output` and `--cache-dir` override the respective destinations. A selected district failure returns exit code 1 **after** saving the available and retained records, so automation can report failure without discarding useful data. Districts outside a targeted refresh do not determine that run's exit code.
