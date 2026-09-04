# What smoke_test.py actually does

In one sentence: it logs into Power BI as an application, asks the admin activity log "what did people do on this date?", and prints or saves whatever comes back.

It's called a smoke test because its real job is to answer "is this pipeline wired up correctly?" rather than to be a finished data product.

---

## Quick answers

### Which date is it pulling?

**By default, all of yesterday**, measured in UTC. If you run it today (3 September 2026) with no extra arguments, it asks for everything that happened on **2 September 2026**, from `00:00:00.000` to `23:59:59.999`.

It deliberately skips today, because the activity log lags behind real time by a few hours. Asking for today usually returns a half-empty answer that looks like data loss but isn't.

### How do I pick a specific date?

Add `--day` followed by the date, written as **year-month-day** with dashes:

```
python smoke_test.py --day 2026-08-15
```

That's the whole answer for a single day. A few variations:

| What you want | What you type |
|---|---|
| One specific day | `--day 2026-08-15` |
| A range of days | `--start-date 2026-08-10 --end-date 2026-08-15` |
| Yesterday | nothing, that's the default |

Two rules the Power BI service enforces, not the script:

- The format must be `YYYY-MM-DD`. Not `08/15/2026`, not `Aug 15 2026`.
- You can't go back further than **28 days**. The activity log simply doesn't keep anything older, and the script stops you with a clear message rather than letting you get a confusing error back from Microsoft.

If you'd rather not type the date every time, you can put it in your `.env` file as `POWERBI_DAY=2026-08-15` and it will be used automatically.

### Where does the output go? JSON or CSV?

**Right now, nowhere.** This is the part that surprises people.

The default output format is `none`, which means the script fetches the data, prints a summary and one sample record to your screen, and then throws the data away. Nothing is written to disk. That's why there's no `activity_events.json` sitting in your folder.

To actually save a file, you have to ask for it:

```
python smoke_test.py --output-format json
```

That creates **`activity_events.json`** in whatever folder you ran the command from. Swap `json` for `csv` and you get `activity_events.csv` instead. To choose the name and location yourself:

```
python smoke_test.py --output-format csv --output-file C:\reports\august.csv
```

One wrinkle worth knowing: the script only prints the "output written to..." confirmation line when you named the file yourself. If you let it pick the default name, it saves the file silently. The file is there, it just doesn't tell you about it.

**JSON vs CSV, briefly.** JSON keeps the data exactly as Power BI sent it, nested structures and all, which is better if something else is going to read it. CSV flattens everything into a spreadsheet grid. Because different events carry different fields, the CSV version collects every field name that appears anywhere in the batch and uses that as the header row, so most rows will have some blank cells. Any value that was itself a nested structure gets squashed into text so it can fit in a single cell.

---

## Walking through the code, top to bottom

### Reading your credentials

The script needs three secrets to prove who it is: a tenant ID, a client ID, and a client secret. It looks for those in your environment, and it loads your `.env` file first so you don't have to set them by hand every time you open a terminal.

It checks that all three are present, but it does that check *inside* the main routine rather than the moment the file loads. That's a small thing with a practical benefit: `--help` still works even when you haven't set up your credentials yet.

### Working out which dates to ask for

This is `resolve_date_window`, and it handles the three ways you might specify a date: one day, a range, or nothing at all. Whichever you use, it produces a start moment and an end moment.

The important detail is that the end is always the **last millisecond of the day**, `23:59:59.999`. Ending at midnight instead would either miss the last day or spill into the next one, and the service is strict about this.

### Chopping the range into single days

`iter_utc_days` exists because of a rule that isn't obvious: **the Power BI activity endpoint refuses any request whose start and end fall on different days.** You cannot ask for a whole week in one call.

So if you ask for six days, this function quietly turns that into six separate day-sized windows, and the script makes six rounds of calls. You don't have to think about it. This was the source of the 400 error earlier, when a request accidentally straddled midnight.

### Getting a login token

`get_token` sends your three secrets to Microsoft's login service and gets back an access token, which is a temporary pass proving the script is allowed to ask for data. Every later request carries that token.

If the login fails, the script prints Microsoft's actual complaint before giving up, which is usually specific enough to tell you which of the three secrets is wrong.

### Asking, and asking again when the network misbehaves

`request_with_retry` is the safety net around every call. It sorts failures into two piles:

- **Temporary problems** such as rate limiting or the service being briefly overloaded. It waits and tries again, doubling the wait each time: 1 second, then 2, then 4, then 8. This is standard practice, and it exists because retrying instantly just adds to the pile-on.
- **Real problems** such as bad credentials or a malformed request. Retrying those is pointless, so it prints the server's explanation and stops immediately.

### Collecting the events

Power BI won't hand over a busy day all at once. It gives you a chunk plus a **continuation token**, which is essentially a bookmark meaning "there's more, come back with this."

`fetch_activity_events_for_day` keeps following those bookmarks until the service stops offering one, which is how you know you've got the whole day.

There's a rule here that also caused the earlier 400 error: on the first call you send the dates, and on every follow-up call you send **only** the bookmark. Sending both together is rejected. The code deliberately builds those two requests as separate cases so they can't get mixed.

`fetch_all_activity_events` sits above that and walks through each day in your range, gathering everything into one combined pile.

### The page limit

`--max-pages` is a spending cap, defaulting to 50. A very busy tenant could paginate for a long time, and this stops a quick sanity check from turning into a twenty-minute download. The budget is shared across all the days you asked for, and if it runs out the script tells you which day it stopped at, so you know the results are incomplete rather than assuming the tenant was quiet.

### Saving the results

`write_output` is the part described in the output section above. It does nothing at all unless you asked for a format.

### Putting it together

`main` runs the whole sequence in order: read the settings, check the credentials, work out the dates, log in, fetch, report, save. Along the way it prints how many pages it fetched, how many events came back, and the first event in full so you can see the shape of the data.

---

## When something goes wrong

| What you see | What it usually means |
|---|---|
| `Missing required environment variable` | Your `.env` file is missing, or one of the three secrets is blank. |
| `AADSTS...` during login | The tenant ID, client ID, or secret is wrong, or the secret has expired. |
| HTTP 400 on the events call | The request was malformed. The date-splitting fix addressed the two known causes. |
| HTTP 401 on the events call | Credentials are fine, but this app isn't allowed to read the admin activity log. That's fixed in the Fabric admin portal, not here. |
| `events returned: 0` | Often genuine, especially for a quiet weekend day. Try a weekday before assuming something is broken. |
| Start date rejected as too old | You asked for more than 28 days ago. That data no longer exists. |

## Handy commands

```
# Yesterday, print to screen only
python smoke_test.py

# One specific day, saved as JSON
python smoke_test.py --day 2026-08-15 --output-format json

# A week, saved as CSV with a name you choose
python smoke_test.py --start-date 2026-08-10 --end-date 2026-08-16 --output-format csv --output-file august_week.csv

# See every option without running anything
python smoke_test.py --help

# Set environment variables in terminal
$env:POWERBI_TENANT_ID = "..."
$env:POWERBI_CLIENT_ID = "..."
$env:POWERBI_CLIENT_SECRET = "..."


```
## Why the activity log cannot see page changes. 
When you open a report, the browser requests it from the service, and the server logs a ViewReport event. When you then click to page two, the page definitions are already sitting in your browser. No request goes back to the server, so there is nothing for the server to log. Microsoft puts it plainly: switching report pages "doesn't issue a report load request to the server since the page definition is already in the browser."

The usage metrics report gets page data by having the browser itself phone home. That is client-side telemetry, and it flows into an internal usage metrics store rather than the audit log.

Activity log (your script)	Usage metrics page views
Collected by	Power BI service, server-side	The user's browser or device
Reaches you via	Admin activity events API	Usage Metrics Report semantic model only
Page-level detail	No	Yes
Retention	30 days	30 days, current day excluded
Reliability	Complete, authoritative	Lossy
The catch worth knowing before you trust page numbers. Because page views depend on the client successfully sending data, ad blockers, firewalls, and flaky networks all drop events. Microsoft says the data "might not include all views or all users," and there is an FAQ entry devoted to why page views can come in lower than report views. Two more limits bear on what you were doing earlier: pages for reports served through an app do not appear in the pages table at all, and some fields in that table are always blank.

Report-level counts do reconcile between the two. Microsoft states that the Report Views metric "matches the aggregate counts of activities in audit logs and activity logs." So your ViewReport totals should line up. Only the page layer diverges.

One thing that may explain inflated view counts in your data: report subscriptions trigger a ViewReport event each time the service snapshots a report for email.

If you want page data in your pipeline, there is no admin API for it. You connect to the per-workspace Usage Metrics Report semantic model from Power BI Desktop or Analyze in Excel, and you have to export on a schedule, because anything past 30 days is deleted permanently.

The notebook already points at usage metrics as the place to get page analytics. I can expand that section with this mechanism and the reliability caveats if you want it written down.

Sources: https://learn.microsoft.com/en-us/power-bi/collaborate-share/service-modern-usage-metrics