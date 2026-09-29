# Prompt for Snowflake Cortex Code: rebuild the Jira sprint-report medallion from existing source tables

## Role and goal
You are working in my Snowflake account as a senior data engineer. Jira Cloud data for four Scrum teams (project keys **TBA, TDE, TDA, TDS**) has already been loaded into source tables, but I don't know exactly how it was pulled. It may have come from Fivetran, Airbyte, a custom API extract with raw JSON, a CSV export or something else. Your job is to:

1. **Discover** what the source tables actually contain.
2. **Map** them to a fixed silver contract (six views, defined below), reporting clearly what maps, what is partial and what is missing.
3. **Build** the silver views, then the gold view `GOLD.SPRINT_ISSUE` (one row per issue per sprint it was part of), which rebuilds Jira's sprint report.
4. **Validate** the result and give me a short report.

The gold logic is already written and tested (Appendix A). It only depends on the silver contract. Most of your work is the source-to-silver mapping.

## Ground rules
- **Discovery first; never guess schema shapes.** Before writing any transformation, run discovery queries and look at real rows. Start with the simplest query that can work (`SELECT * … LIMIT 5`), then expand. If a column's meaning is unclear, sample distinct values before using it.
- **Read-only on sources.** Never modify, truncate or drop source tables. Create everything new in `{TARGET_DB}.SILVER` and `{TARGET_DB}.GOLD` (create the schemas if needed). Use views, not tables, unless a view becomes too slow.
- **Ask before anything destructive or costly:** dropping objects, warehouses larger than SMALL, or queries likely to scan more than a few GB.
- **Show your work.** For each silver view, show the source tables and columns it uses and one sample output row.
- **Stop and ask** if a required element can't be found or is ambiguous, rather than inventing a substitute. Partial answers with gaps clearly marked are fine.

Placeholders I will fill in or you should ask me for: `{SOURCE_DB}` / `{SOURCE_SCHEMA}` (where the raw Jira tables live; if I leave them blank, search for them), `{TARGET_DB}` (where to build silver/gold), and the warehouse to use.

## Step 1 — Discover the source
1. List candidate tables and their row counts:
```sql
SELECT table_catalog, table_schema, table_name, row_count, last_altered
FROM {SOURCE_DB}.INFORMATION_SCHEMA.TABLES
WHERE table_schema ILIKE '%{SOURCE_SCHEMA}%' OR table_name ILIKE ANY ('%issue%','%sprint%','%changelog%','%history%','%worklog%','%status%','%field%','%board%')
ORDER BY table_schema, table_name;
```
2. For each candidate table, get its columns from `INFORMATION_SCHEMA.COLUMNS` and look at 5 rows. For VARIANT/JSON columns, list the top-level keys (`OBJECT_KEYS`) and one full sample.
3. Work out which **ingestion pattern** it is. These are the patterns I expect, but confirm them against the actual data:

| Pattern | Typical tables | Where status history is | Where sprint history is |
|---|---|---|---|
| Raw API JSON (custom extract) | ISSUES_RAW, CHANGELOGS_RAW, SPRINTS_RAW, WORKLOGS_RAW with a VARIANT column | changelog items where `field = 'status'` | changelog items where `field = 'Sprint'`, with `from`/`to` holding the full comma-separated list of sprint IDs before and after |
| Fivetran Jira connector | ISSUE, ISSUE_FIELD_HISTORY, ISSUE_MULTISELECT_HISTORY, SPRINT, STATUS, FIELD, WORKLOG, PROJECT | ISSUE_FIELD_HISTORY where field_id = 'status' (value = status ID → join STATUS for the name) | ISSUE_MULTISELECT_HISTORY where field_id = the Sprint custom field (one row per sprint value per change time) |
| Airbyte Jira source | issues, issue_changelogs (or changelog inside issues), sprints, sprint_issues, issue_worklogs | changelog JSON | changelog JSON |
| CSV export | one wide table, often with several Sprint columns | usually absent | usually absent (only the current Sprint values) |

4. Identify the site-specific custom fields by name, not by ID. In the Jira instance I know, they are Sprint = `customfield_10020`, Story Points = `customfield_10040` and Team Member = `customfield_10045` (a select list whose `value` is the person's name). If there is a FIELD table, use it to confirm the IDs.
5. Report a short **mapping table**: silver column → source table.column (+ transformation) → status (mapped / derived / partial / missing).

## Step 2 — Silver contract (build these six views exactly)
Column names and types must match, because the gold view depends on them. Use `TIMESTAMP_TZ` for timestamps.

**`SILVER.SPRINTS`**: one row per sprint (all states)
| column | type | notes |
|---|---|---|
| sprint_id | NUMBER | Jira sprint ID |
| board_id | NUMBER | nullable |
| sprint_name | STRING | e.g. `TDE - Sprint 1 - 01.10.25` |
| team_key | STRING | project key; in my naming it's the text before the first ` - `, but derive it from board/project if available |
| sprint_number | INT | the N from "Sprint N" |
| state | STRING | lower case: `closed`, `active`, `future` |
| start_ts, end_ts, complete_ts | TIMESTAMP_TZ | complete_ts is NULL for active/future sprints |

**`SILVER.ISSUES`**: one row per issue (latest version only; de-duplicate if the source keeps several extracts)
| column | type | notes |
|---|---|---|
| issue_id | NUMBER | Jira numeric ID |
| issue_key | STRING | e.g. TDE-310 |
| project_key | STRING | |
| summary | STRING | |
| status | STRING | current status name |
| team_member | STRING | Team Member custom field value (the person's name) |
| story_points | FLOAT | |
| original_estimate_h | FLOAT | Jira stores seconds; divide by 3600 |
| time_spent_h | FLOAT | seconds / 3600 |
| created_ts, resolved_ts | TIMESTAMP_TZ | |

Filter to issue type Story if the source has other types, and to projects TBA, TDE, TDA, TDS.

**`SILVER.ISSUE_CURRENT_SPRINTS`**: one row per (issue_id, sprint_id) in the issue's *current* Sprint field. The source may hold an array of sprint objects, an array of IDs, a comma-separated string, or legacy strings like `com.atlassian.greenhopper.service.sprint.Sprint@…[id=51,…,name=…]`. Parse whichever it is.

**`SILVER.STATUS_CHANGES`**: one row per status change
| column | type |
|---|---|
| issue_id | NUMBER |
| change_ts | TIMESTAMP_TZ |
| from_status | STRING (status name) |
| to_status | STRING (status name) |

**`SILVER.SPRINT_CHANGES`**: one row per sprint added to or removed from an issue
| column | type | notes |
|---|---|---|
| issue_id | NUMBER | |
| change_ts | TIMESTAMP_TZ | |
| sprint_id | NUMBER | |
| action | STRING | `'add'` or `'remove'` |

For raw changelog JSON, each Sprint item holds the complete list before and after (e.g. from `"51"` to `"51, 52"`), so compute adds as `to − from` and removes as `from − to`. `STRTOK_TO_ARRAY(x, ', ')` with `ARRAY_EXCEPT` works well. For Fivetran-style multiselect history, derive adds and removes by comparing each change's set of sprint values with the previous set for the same issue.

**`SILVER.WORKLOGS`**: one row per worklog
| column | type |
|---|---|
| worklog_id | NUMBER |
| issue_id | NUMBER |
| started_ts | TIMESTAMP_TZ |
| hours | FLOAT (timeSpentSeconds / 3600) |

## Known Jira quirks to check for
- **Changelog time formats:** bulk-fetched changelog `created` values are **epoch milliseconds** (`TO_TIMESTAMP_TZ(x, 3)`), while per-issue changelogs are ISO strings.
- **Timestamp offsets:** issue timestamps look like `2024-11-06T17:15:00.000-0600`, with the offset written without a colon, so use `'YYYY-MM-DD"T"HH24:MI:SS.FF3TZHTZM'`. Sprint dates end in `Z`.
- **Imported history:** imported changelog rows have **no fieldId**, so filter on field *name* (`status`, `Sprint`), not fieldId.
- **Optional keys:** `from` / `to` keys are missing when empty. `from` and `to` are reserved words, so use bracket access (`item['from']`).
- **Repeated extracts:** sources that keep every extract have several rows per issue. Keep the latest with `QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY <load timestamp> DESC) = 1`.
- **Noise rows:** the changelog also has rows for Rank, assignee, timespent, WorklogId, resolution and so on. Ignore everything except status and Sprint.
- **Leftover test data:** issue TBA-1 is a known test story in TBA Sprint 1. Keep it, but mention it.

## If something is missing, degrade gracefully and say so
| Missing | Fallback | Effect on gold |
|---|---|---|
| Sprint history (changelog) | Treat every sprint in the current Sprint field as joined at `created_ts` | `committed` / `added_after_start` / `removed_during` become unreliable, and removed stories disappear. Flag this in the report. |
| Status history | Use the current status for every sprint | Status at close is wrong for stories carried over. Flag this. |
| Worklogs | `time_spent_h` only, and `hours_logged_in_sprint` = NULL | No per-sprint hours |
| Team Member field | NULL, or assignee display name if present (ask me) | |
| Story Points field | NULL | Point totals are empty |

## Step 3 — Gold view
Create `{TARGET_DB}.GOLD.SPRINT_ISSUE` using the SQL in Appendix A **without changing its logic**. Only fix object names if needed. It returns one row per issue per sprint the issue was in at sprint start or joined during the sprint, with these columns:
`team_key, sprint_id, sprint_name, sprint_number, state, start_ts, end_ts, issue_id, issue_key, summary, team_member, story_points, original_estimate_h, committed, added_after_start, removed_during, completed, not_completed, status_at_end, hours_logged_in_sprint`.

Rules it applies:
- **committed:** the issue was in the sprint at startDate.
- **added_after_start:** added between start and completion.
- **removed_during:** not in the sprint at completion.
- **completed / not_completed:** in the sprint at completion, and status at completion is (or isn't) `Done`.
- **Implicit join:** an issue created with the sprint already set counts as joined at creation.
- **Active sprints** are measured up to now.

If the Done status has a different name in this source (check `SELECT DISTINCT to_status`), tell me before changing it.

Optionally, also create `GOLD.SPRINT_STORY` (closed sprints only, one row per story per sprint still on its Sprint field at close) with these columns: key, ID, Summary, Estimate, Spent, Begin_ts, End_ts, Sprint, project_key, Team_member, Status, Sprint_names (ordered ARRAY of closed sprint names), Sprint_count. Build it from `SPRINT_ISSUE WHERE state = 'closed' AND NOT removed_during`.

## Step 4 — Validate and report
Run these checks and show the results:
1. **Row counts** for each silver view, and the share of NULLs in key columns (issue_id, change_ts, sprint_id, created_ts, start_ts).
2. **Referential integrity:** every sprint_id in SPRINT_CHANGES / ISSUE_CURRENT_SPRINTS exists in SPRINTS, and every issue_id exists in ISSUES.
3. **Consistency:** for closed sprints, the set of issues in the sprint at completion (from SPRINT_CHANGES + implicit joins) should match the issues whose current Sprint field contains that sprint. Report the percentage that match.
4. **Known answers:** if this is the same dataset I built, these numbers should match exactly. If they don't, investigate before concluding the logic is wrong.

| sprint_name | committed | added | removed | completed | not completed | completion % |
|---|---|---|---|---|---|---|
| TDE - Sprint 1 - 01.10.25 | 27 | 4 | 0 | 22 | 9 | 71.0 |
| TDA - Sprint 20 - 10.03.25 | 25 | 2 | 2 | 22 | 3 | 88.0 |
| TDS - Sprint 45 - 09.18.26 | 24 | 4 | 0 | 18 | 10 | 64.3 |

Overall completion across closed sprints should be about TBA 74.5%, TDE 76.1%, TDA 71.8% and TDS 74.2%. The dataset has 45 closed sprints per team and 3,852 issues.

```sql
SELECT sprint_name,
       COUNT_IF(committed) committed, COUNT_IF(added_after_start) added, COUNT_IF(removed_during) removed,
       COUNT_IF(completed) completed, COUNT_IF(not_completed) not_completed,
       ROUND(100 * COUNT_IF(completed) / NULLIF(COUNT_IF(completed OR not_completed), 0), 1) completion_pct
FROM {TARGET_DB}.GOLD.SPRINT_ISSUE
WHERE sprint_name IN ('TDE - Sprint 1 - 01.10.25', 'TDA - Sprint 20 - 10.03.25', 'TDS - Sprint 45 - 09.18.26')
GROUP BY sprint_name ORDER BY sprint_name;
```

5. **Final report:**
   - the ingestion pattern you found;
   - the mapping table (Step 1.5);
   - the DDL of every object you created;
   - the validation results;
   - every assumption and gap, each with its impact on the sprint report;
   - anything I should decide.

## Appendix A — Gold view SQL (use as-is)
```sql
CREATE OR REPLACE VIEW {TARGET_DB}.GOLD.SPRINT_ISSUE AS
WITH pairs AS (          -- every (issue, sprint) combination the issue ever touched
  SELECT issue_id, sprint_id FROM {TARGET_DB}.SILVER.issue_current_sprints
  UNION
  SELECT issue_id, sprint_id FROM {TARGET_DB}.SILVER.sprint_changes
),
first_evt AS (           -- first explicit sprint event per pair
  SELECT issue_id, sprint_id, action AS first_action FROM (
    SELECT issue_id, sprint_id, action,
           ROW_NUMBER() OVER (PARTITION BY issue_id, sprint_id ORDER BY change_ts) AS rn
    FROM {TARGET_DB}.SILVER.sprint_changes) x
  WHERE rn = 1
),
membership AS (          -- explicit add/remove events + implicit "in the sprint since creation"
  SELECT issue_id, sprint_id, change_ts, action FROM {TARGET_DB}.SILVER.sprint_changes
  UNION ALL
  SELECT p.issue_id, p.sprint_id, i.created_ts AS change_ts, 'add' AS action
  FROM pairs p
  JOIN {TARGET_DB}.SILVER.issues i ON i.issue_id = p.issue_id
  LEFT JOIN first_evt f ON f.issue_id = p.issue_id AND f.sprint_id = p.sprint_id
  WHERE f.first_action IS NULL OR f.first_action = 'remove'
),
win AS (                 -- sprint window (an active sprint is measured up to now)
  SELECT p.issue_id, p.sprint_id, s.team_key, s.sprint_name, s.sprint_number, s.state,
         s.start_ts, COALESCE(s.complete_ts, CURRENT_TIMESTAMP) AS end_ts
  FROM pairs p JOIN {TARGET_DB}.SILVER.sprints s ON s.sprint_id = p.sprint_id
),
at_start AS (
  SELECT issue_id, sprint_id, action FROM (
    SELECT w.issue_id, w.sprint_id, m.action,
           ROW_NUMBER() OVER (PARTITION BY w.issue_id, w.sprint_id ORDER BY m.change_ts DESC) AS rn
    FROM win w JOIN membership m ON m.issue_id = w.issue_id AND m.sprint_id = w.sprint_id
    WHERE m.change_ts <= w.start_ts) x
  WHERE rn = 1
),
at_end AS (
  SELECT issue_id, sprint_id, action FROM (
    SELECT w.issue_id, w.sprint_id, m.action,
           ROW_NUMBER() OVER (PARTITION BY w.issue_id, w.sprint_id ORDER BY m.change_ts DESC) AS rn
    FROM win w JOIN membership m ON m.issue_id = w.issue_id AND m.sprint_id = w.sprint_id
    WHERE m.change_ts <= w.end_ts) x
  WHERE rn = 1
),
adds_during AS (
  SELECT w.issue_id, w.sprint_id, COUNT(*) AS n_adds
  FROM win w JOIN membership m ON m.issue_id = w.issue_id AND m.sprint_id = w.sprint_id
  WHERE m.action = 'add' AND m.change_ts > w.start_ts AND m.change_ts <= w.end_ts
  GROUP BY w.issue_id, w.sprint_id
),
status_at_end AS (       -- last status change on or before the sprint end
  SELECT issue_id, sprint_id, to_status FROM (
    SELECT w.issue_id, w.sprint_id, c.to_status,
           ROW_NUMBER() OVER (PARTITION BY w.issue_id, w.sprint_id ORDER BY c.change_ts DESC) AS rn
    FROM win w JOIN {TARGET_DB}.SILVER.status_changes c ON c.issue_id = w.issue_id
    WHERE c.change_ts <= w.end_ts) x
  WHERE rn = 1
),
first_status AS (        -- status before any recorded change
  SELECT issue_id, from_status FROM (
    SELECT issue_id, from_status, ROW_NUMBER() OVER (PARTITION BY issue_id ORDER BY change_ts) AS rn
    FROM {TARGET_DB}.SILVER.status_changes) x
  WHERE rn = 1
),
hours AS (
  SELECT w.issue_id, w.sprint_id, SUM(l.hours) AS hours_logged
  FROM win w JOIN {TARGET_DB}.SILVER.worklogs l ON l.issue_id = w.issue_id
  WHERE l.started_ts >= w.start_ts AND l.started_ts <= w.end_ts
  GROUP BY w.issue_id, w.sprint_id
),
flags AS (
  SELECT w.*,
         COALESCE(st.action = 'add', FALSE) AS in_at_start,
         COALESCE(en.action = 'add', FALSE) AS in_at_end,
         COALESCE(ad.n_adds, 0) > 0 AS added_during,
         COALESCE(se.to_status, fs.from_status, i.status) AS status_at_end,
         COALESCE(h.hours_logged, 0) AS hours_logged_in_sprint,
         i.issue_key, i.summary, i.team_member, i.story_points, i.original_estimate_h
  FROM win w
  JOIN {TARGET_DB}.SILVER.issues i ON i.issue_id = w.issue_id
  LEFT JOIN at_start st ON st.issue_id = w.issue_id AND st.sprint_id = w.sprint_id
  LEFT JOIN at_end en ON en.issue_id = w.issue_id AND en.sprint_id = w.sprint_id
  LEFT JOIN adds_during ad ON ad.issue_id = w.issue_id AND ad.sprint_id = w.sprint_id
  LEFT JOIN status_at_end se ON se.issue_id = w.issue_id AND se.sprint_id = w.sprint_id
  LEFT JOIN first_status fs ON fs.issue_id = w.issue_id
  LEFT JOIN hours h ON h.issue_id = w.issue_id AND h.sprint_id = w.sprint_id
)
SELECT team_key, sprint_id, sprint_name, sprint_number, state, start_ts, end_ts,
       issue_id, issue_key, summary, team_member, story_points, original_estimate_h,
       in_at_start                                   AS committed,
       added_during AND NOT in_at_start              AS added_after_start,
       NOT in_at_end                                 AS removed_during,
       in_at_end AND status_at_end = 'Done'          AS completed,
       in_at_end AND status_at_end <> 'Done'         AS not_completed,
       status_at_end, hours_logged_in_sprint
FROM flags
WHERE in_at_start OR added_during;
```
