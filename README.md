# Tenant Activity Events

Pull the Microsoft Fabric / Power BI **admin activity log** for a given date, then
flatten, filter and categorise it for analysis.

The activity log records who did what to which item across the whole tenant:
report views, edits, exports, sharing, app installs, admin setting changes. Its
retention is short, so anything you want to keep has to be extracted and stored
before it ages out.

This repo contains two pieces:

- **`smoke_test.py`** fetches events from the Power BI admin API and writes them
  to JSON or CSV.
- **`activity_events_flatten.ipynb`** takes that output and turns it into a flat
  table, in Python and in SQL, with report filtering and activity buckets.

---

## Contents

| File | What it is |
|---|---|
| `smoke_test.py` | Fetches activity events from the admin API |
| `smoke_test_explained.md` | Plain-language walkthrough of that script |
| `activity_events_flatten.ipynb` | Flattening, filtering and bucketing notebook |
| `powerbi_operations_reference.csv` | All ~740 documented operation names and their friendly descriptions, extracted from Microsoft's operation list |
| `.env.example` | Template for the three required credentials |
| `activity_events.json` | Sample output from a run |

---

## Requirements

- Python 3.9 or later. Developed against 3.14.
- A Microsoft Entra app registration with a client secret.
- The app's service principal must be in a security group that is **allowed to
  use Power BI admin APIs**, enabled in the Fabric admin portal. Without this the
  API returns HTTP 401 no matter how correct your credentials are.
- Fabric administrator rights to configure the above.

Install the dependencies:

```
pip install requests python-dotenv pandas pyarrow
```

Only `requests` is strictly required by the fetch script. `python-dotenv` lets it
read a `.env` file, and `pandas` plus `pyarrow` are for the notebook.

---

## Setup

Copy the template and fill in the three values from your app registration:

```
cp .env.example .env
```

```
POWERBI_TENANT_ID=...
POWERBI_CLIENT_ID=...
POWERBI_CLIENT_SECRET=...
```

`.env` is git-ignored. Keep it that way, and never commit a real client secret.

---

## Usage

```
# Yesterday, printed to screen, nothing saved
python smoke_test.py

# One specific day, saved as JSON
python smoke_test.py --day 2026-08-15 --output-format json

# A date range, saved as CSV with a name you choose
python smoke_test.py --start-date 2026-08-10 --end-date 2026-08-16 \
    --output-format csv --output-file august_week.csv

# Every option
python smoke_test.py --help
```

### Options

| Option | Default | Notes |
|---|---|---|
| `--day` | yesterday | A single day, `YYYY-MM-DD` |
| `--start-date` / `--end-date` | none | A range, split into one call per day |
| `--output-format` | `none` | `json`, `csv` or `none` |
| `--output-file` | `activity_events.<format>` | Destination path |
| `--max-pages` | 50 | Page budget shared across all days |

Every option also reads an environment variable, so you can set defaults in
`.env`: `POWERBI_DAY`, `POWERBI_START_DATE`, `POWERBI_END_DATE`,
`POWERBI_OUTPUT_FORMAT`, `POWERBI_OUTPUT_FILE`.

**By default nothing is written to disk.** The output format is `none`, which
prints a summary and one sample event and then discards the data. Pass
`--output-format` to actually save a file.

---

## Rules the API enforces

These are the constraints that cause most failures. The script handles all of
them, but they explain why it is shaped the way it is.

- **A request cannot span more than one UTC day.** Microsoft's reference states
  that start and end "must be in the same UTC day." A multi-day range is split
  into one call per day automatically.
- **History is 28 days.** Anything older is gone. The script rejects an
  out-of-range start date rather than letting the API return a confusing error.
- **Pagination uses a continuation token, sent on its own.** Repeating the date
  parameters alongside the token is rejected with HTTP 400.
- **Rate limit of 200 requests per hour.** Extract once per day and query the
  saved copy repeatedly, rather than re-pulling.
- **All timestamps are UTC**, with no timezone marker in the payload. Convert
  once, early.

---

## The notebook

`activity_events_flatten.ipynb` covers four things:

1. **Flattening in Python**, with both a standard-library recursive flattener and
   the pandas `json_normalize` one-liner.
2. **Flattening in SQL**, with separate cells for Snowflake, Databricks and
   Fabric, since each handles semi-structured data differently.
3. **Filtering to report activity**, by operation name and by whether a record
   carries a report identifier at all.
4. **Bucketing operations by type**, validated against the official operation
   list so no invented names creep in.

Fields vary a lot between operations, so any flat table of this data is mostly
nulls. That is normal, not a broken extract.

### A note on report page views

The activity log records that a report was opened, through `ViewReport`. It does
**not** record movement between pages inside that report. Microsoft states that
"report page views aren't part of audit logs."

The usage metrics report does show a per-page breakdown, because it comes from a
separate client-side telemetry pipeline rather than the server. Section 3.1 of
the notebook explains the difference, why page counts from that source are
approximate, and how to get at them if you need them.

What the activity log *does* give you is `DistributionMethod`, which separates
views through an app from views in a workspace.

---

## Documentation

- [Operation list](https://learn.microsoft.com/en-us/fabric/admin/operation-list)
  is the authoritative catalogue of operation names, extracted here as
  `powerbi_operations_reference.csv`.
- [Admin - Get Activity Events](https://learn.microsoft.com/en-us/rest/api/power-bi/admin/get-activity-events)
  is the API this script calls, including the same-day and continuation token rules.
- [Track user activities in Power BI](https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-admin-auditing)
  explains the activity log and how it relates to the Purview audit log.
- [Access the Power BI activity log](https://learn.microsoft.com/en-us/power-bi/guidance/admin-activity-log)
  is the guidance article, with PowerShell alternatives.
- [Tenant-level auditing](https://learn.microsoft.com/en-us/power-bi/guidance/powerbi-implementation-planning-auditing-monitoring-tenant-level-auditing)
  covers building a durable extract rather than a one-off pull.
- [Monitor usage metrics in Power BI workspaces](https://learn.microsoft.com/en-us/power-bi/collaborate-share/service-modern-usage-metrics)
  is the source for report page views and their limitations.

---

## Handling the extracted data

Activity events are **real tenant telemetry**. A single day's export identifies
your tenant, your users by email address, their IP addresses, and the names and
GUIDs of workspaces, reports and semantic models.

Treat any file this script produces as internal data. Before committing a sample
to a public repository, either redact it or replace the identifying fields with
fake values. The generated CSV and Parquet outputs are git-ignored for this
reason.
