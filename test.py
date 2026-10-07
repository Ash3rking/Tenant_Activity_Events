
import base64
import csv
import os
import sys
import time
from datetime import datetime, timezone

import requests


PROJECT_KEYS = ("TBA", "TDE", "TDA", "TDS")
SECTIONS = {
    "completedIssues": "Completed",
    "issuesNotCompletedInCurrentSprint": "Not completed",
    "puntedIssues": "Removed from sprint",
    "issuesCompletedInAnotherSprint": "Completed outside sprint",
}
SUMMARY_FIELDS = (
    "project_key", "sprint", "state", "start", "complete", "completed",
    "not_completed", "removed", "added_after_start",
    "completed_original_estimate_hours", "completed_time_remaining_hours",
    "not_completed_original_estimate_hours", "not_completed_time_remaining_hours",
)
ISSUE_FIELDS = (
    "project_key", "sprint", "section", "key", "summary", "status",
    "original_estimate_hours", "time_remaining_hours", "added_after_start",
)


class JiraApi:
    def __init__(self):
        base_url = os.getenv("JIRA_BASE_URL")
        email = os.getenv("JIRA_EMAIL")
        api_token = os.getenv("JIRA_API_TOKEN")
        if not all((base_url, email, api_token)):
            raise ValueError(
                "Set JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN before running."
            )

        self.base_url = base_url.rstrip("/")
        self.timeout = float(os.getenv("JIRA_TIMEOUT_SECONDS", "60"))
        auth = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
        })

    def request(self, method, path, params=None, json_body=None):
        url = f"{self.base_url}{path}"
        for attempt in range(5):
            response = self.session.request(
                method,
                url,
                params=params,
                json=json_body,
                timeout=self.timeout,
            )

            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 4:
                    response.raise_for_status()
                try:
                    delay = float(response.headers.get("Retry-After", 2 ** attempt))
                except ValueError:
                    delay = 2 ** attempt
                time.sleep(min(delay, 60))
                continue

            response.raise_for_status()
            return response.json()

        raise RuntimeError(f"Jira request failed after retries: {url}")

    def get(self, path, params=None):
        return self.request("GET", path, params=params)

    def scrum_boards(self, project_keys):
        boards = []
        for project_key in project_keys:
            start_at = 0
            while True:
                page = self.get(
                    "/rest/agile/1.0/board",
                    params={
                        "projectKeyOrId": project_key,
                        "type": "scrum",
                        "startAt": start_at,
                        "maxResults": 50,
                    },
                )
                values = page.get("values", [])
                boards.extend(
                    {
                        "boardId": board["id"],
                        "projectKey": project_key,
                        "name": board["name"],
                    }
                    for board in values
                )
                if page.get("isLast", True) or not values:
                    break
                start_at += len(values)
        return boards

    def sprints(self, board_id):
        start_at = 0
        while True:
            page = self.get(
                f"/rest/agile/1.0/board/{board_id}/sprint",
                params={"startAt": start_at, "maxResults": 50},
            )
            values = page.get("values", [])
            for sprint in values:
                yield sprint
            if page.get("isLast", True) or not values:
                break
            start_at += len(values)

    def issue_estimates(self, issue_keys):
        estimates = {}
        issue_ids = {}
        for offset in range(0, len(issue_keys), 100):
            batch = issue_keys[offset:offset + 100]
            jql_keys = ", ".join(f'"{key}"' for key in batch)
            token = None
            while True:
                body = {
                    "jql": f"key in ({jql_keys})",
                    "fields": ["timeoriginalestimate", "timeestimate"],
                    "maxResults": 100,
                }
                if token:
                    body["nextPageToken"] = token
                page = self.request(
                    "POST", "/rest/api/3/search/jql", json_body=body
                )
                for issue in page.get("issues", []):
                    fields = issue.get("fields") or {}
                    estimates[issue["key"]] = {
                        "timeoriginalestimate": fields.get("timeoriginalestimate"),
                        "timeestimate": fields.get("timeestimate"),
                    }
                    issue_ids[str(issue["id"])] = issue["key"]

                token = page.get("nextPageToken")
                if not token or page.get("isLast"):
                    break
        return estimates, issue_ids

    def changelogs(self, issue_ids, batch_size=100):
        for offset in range(0, len(issue_ids), batch_size):
            token = None
            while True:
                body = {
                    "issueIdsOrKeys": issue_ids[offset:offset + batch_size],
                    "maxResults": 10000,
                }
                if token:
                    body["nextPageToken"] = token
                page = self.request(
                    "POST", "/rest/api/3/changelog/bulkfetch", json_body=body
                )
                yield from page.get("issueChangeLogs", [])
                token = page.get("nextPageToken")
                if not token:
                    break


def parse_jira_datetime(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 100_000_000_000 else value
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if not isinstance(value, str):
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def seconds_at_sprint_end(histories, current_value, field_id, sprint_end):
    if sprint_end is None:
        return None

    value = current_value
    ordered_histories = sorted(
        histories,
        key=lambda history: parse_jira_datetime(history.get("created"))
        or datetime.max.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    for history in ordered_histories:
        created = parse_jira_datetime(history.get("created"))
        if created is None or created <= sprint_end:
            continue
        for item in history.get("items", []):
            if item.get("fieldId") == field_id:
                value = item.get("from")

    if value in (None, ""):
        return None
    return float(value)


def seconds_to_hours(seconds):
    if seconds is None:
        return None
    return round(seconds / 3600, 2)


def write_csv(filename, fieldnames, rows):
    with open(filename, "w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {filename}: {len(rows)} rows")


def can_overwrite(filename):
    if not os.path.exists(filename):
        return True
    try:
        with open(filename, "a", encoding="utf-8"):
            pass
    except PermissionError:
        return False
    return True


def main():
    try:
        client = JiraApi()
        summary, issues = [], []

        boards = client.scrum_boards(PROJECT_KEYS)
        print(f"Found {len(boards)} Scrum boards")

        for board in boards:
            print(f"Reading sprints for {board['projectKey']} board {board['name']}")
            for sprint in client.sprints(board["boardId"]):
                if sprint.get("state") == "future":
                    continue

                print(f"Fetching sprint report: {sprint['name']}", flush=True)
                report = client.get(
                    "/rest/greenhopper/1.0/rapid/charts/sprintreport",
                    params={"rapidViewId": board["boardId"], "sprintId": sprint["id"]},
                )
                contents = report.get("contents") or {}
                added = contents.get("issueKeysAddedDuringSprint") or {}
                completed = contents.get("completedIssues") or []
                not_completed = contents.get("issuesNotCompletedInCurrentSprint") or []
                removed = contents.get("puntedIssues") or []

                summary.append({
                    "project_key": board["projectKey"],
                    "sprint": sprint["name"],
                    "state": sprint.get("state"),
                    "start": sprint.get("startDate"),
                    "complete": sprint.get("completeDate"),
                    "completed": len(completed),
                    "not_completed": len(not_completed),
                    "removed": len(removed),
                    "added_after_start": len(added),
                })

                for field, section in SECTIONS.items():
                    for issue in contents.get(field) or []:
                        status = issue.get("status") or {}
                        issues.append({
                            "project_key": board["projectKey"],
                            "sprint": sprint["name"],
                            "section": section,
                            "key": issue.get("key"),
                            "summary": issue.get("summary"),
                            "status": status.get("name"),
                            "original_estimate_hours": None,
                            "time_remaining_hours": None,
                            "added_after_start": issue.get("key") in added,
                        })

        issue_keys = sorted({row["key"] for row in issues if row.get("key")})
        latest_estimates, issue_ids = client.issue_estimates(issue_keys)
        histories_by_key = {key: [] for key in issue_keys}
        for issue_changelog in client.changelogs(list(issue_ids)):
            issue_key = issue_ids.get(str(issue_changelog.get("issueId")))
            if issue_key:
                histories_by_key[issue_key].extend(
                    issue_changelog.get("changeHistories", [])
                )

        sprint_ends = {
            (row["project_key"], row["sprint"]): parse_jira_datetime(row["complete"])
            for row in summary
        }
        total_fields = {
            "Completed": (
                "completed_original_estimate_hours",
                "completed_time_remaining_hours",
            ),
            "Not completed": (
                "not_completed_original_estimate_hours",
                "not_completed_time_remaining_hours",
            ),
        }
        totals = {}
        for row in issues:
            issue_key = row.get("key")
            current = latest_estimates.get(issue_key, {})
            histories = histories_by_key.get(issue_key, [])
            sprint_end = sprint_ends.get((row["project_key"], row["sprint"]))
            original_estimate = seconds_to_hours(seconds_at_sprint_end(
                histories,
                current.get("timeoriginalestimate"),
                "timeoriginalestimate",
                sprint_end,
            ))
            time_remaining = seconds_to_hours(seconds_at_sprint_end(
                histories,
                current.get("timeestimate"),
                "timeestimate",
                sprint_end,
            ))
            row["original_estimate_hours"] = original_estimate
            row["time_remaining_hours"] = time_remaining

            summary_fields = total_fields.get(row["section"])
            if summary_fields:
                for field, value in zip(summary_fields, (original_estimate, time_remaining)):
                    if value is not None:
                        key = (row["project_key"], row["sprint"], field)
                        total, count = totals.get(key, (0.0, 0))
                        totals[key] = (total + value, count + 1)

        for row in summary:
            for field in (*total_fields["Completed"], *total_fields["Not completed"]):
                total, count = totals.get((row["project_key"], row["sprint"], field), (0.0, 0))
                row[field] = round(total, 2) if count else None

        output_files = (
            ("sprint_report_summary.csv", SUMMARY_FIELDS, summary),
            ("sprint_report_issues.csv", ISSUE_FIELDS, issues),
        )
        if not all(can_overwrite(filename) for filename, _, _ in output_files):
            run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            print(
                "A report CSV is locked; writing both reports to timestamped files.",
                file=sys.stderr,
            )
            output_files = tuple(
                (f"{filename[:-4]}_{run_stamp}.csv", fields, rows)
                for filename, fields, rows in output_files
            )

        for filename, fields, rows in output_files:
            write_csv(filename, fields, rows)
        return 0
    except (requests.RequestException, ValueError, KeyError, OSError) as error:
        print(f"Sprint report failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())