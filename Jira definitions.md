## Completed After the Sprint Closed:

The issue was still open when the sprint was officially completed/closed.

It was later marked as Done (either while in the backlog or during a subsequent sprint).

## Completed Before the Sprint Started:

The issue was added to the sprint and resolved/marked Done before someone clicked Start Sprint.

## Completed in Another Sprint (Multi-Sprint Issues):

The issue was carried over across multiple sprints. It was marked Done while another sprint was active, but still retains this sprint in its sprint history.

Part 1: Sprint report terms
Report header
Term

Description

Sprint name

The name of the sprint being reported on.

Sprint status

Active (in progress) or Closed (completed).

Start date

When the sprint was started on the board.

End date (planned)

The scheduled end date set when the sprint started.

Completed date

When the sprint was actually closed. It can differ from the planned end date.

Sprint goal

The objective the team set for the sprint.

Board

The Scrum board the sprint belongs to. Its columns decide what counts as "Done".

Burndown chart
Term

Description

Estimation statistic

The unit used to measure work: story points, original time estimate or work item count. It's set in board settings.

Remaining values (red line)

How much estimated work is left, plotted over the sprint.

Guideline (grey line)

An ideal straight-line burndown from the committed amount at sprint start down to zero at the end date.

Scope change

Steps up or down in the line when work is added, removed or re-estimated mid-sprint.

Non-working days (shaded)

Days excluded from the guideline, based on the board's working-day settings.

Tracking statistic

If time tracking is on, remaining time estimate can be tracked separately from the estimation statistic.

Work item sections
Term

Description

Completed work items

Items in the board's "Done" column (the rightmost column) when the sprint closed.

Work items not completed

Items not in "Done" at sprint close. They're usually moved to the backlog or the next sprint.

Work items completed outside of this sprint

Items in the sprint that were finished before the sprint started or in another sprint.

Work items removed from sprint

Items taken out of the sprint after it started.

Asterisk (*)

Marks an item that was added to the sprint after it started.

Estimate "X → Y"

The item's estimate changed during the sprint, from X to Y.

Total

The sum of the estimation statistic for each section.

Related metrics (often reported with the sprint report)
Term

Description

Committed

Total estimate of the items in the sprint when it started.

Completed

Total estimate of the items that reached "Done" by sprint close.

Velocity

Completed estimate per sprint, usually averaged over several sprints.

Say/Do ratio (commitment reliability)

Completed ÷ committed, as a percentage.

Scope creep

Estimate added after the sprint started, divided by the committed estimate.

Carryover / spillover

Incomplete work moved into the next sprint.

Part 2: Data dictionary for raw data columns
Sprint-level fields
Column

Data type

Description

Example

Sprint ID

Integer

Jira's unique ID for the sprint

142

Sprint Name

Text

Sprint name

"GA Sprint 23"

Sprint State

Text

future, active or closed

closed

Sprint Start Date

Datetime

When the sprint was started

2026-09-01 09:00

Sprint End Date

Datetime

Planned end date

2026-09-14 17:00

Sprint Complete Date

Datetime

When the sprint was actually closed

2026-09-15 10:12

Sprint Goal

Text

Sprint objective

"Launch export API"

Board ID / Board Name

Integer / Text

The board that owns the sprint

12 / "GA Board"

Work item fields
Column

Data type

Description

Example

Issue Key

Text

Work item key (project key + number)

GA-481

Issue ID

Integer

Jira's internal ID

10532

Summary

Text

Work item title

"Add CSV export"

Issue Type

Text

Story, Bug, Task, Epic, Sub-task

Story

Status

Text

Current workflow status

In Review

Status Category

Text

To Do, In Progress or Done

In Progress

Priority

Text

Priority level

High

Assignee

Text

Person responsible

Asher Guni

Reporter

Text

Person who created the item

—

Created

Datetime

When the item was created

—

Updated

Datetime

When the item was last changed

—

Resolved

Datetime

When a resolution was set

—

Resolution

Text

Done, Won't Do, Duplicate and so on

Done

Sprint

Text (multi)

All sprints the item has belonged to

"Sprint 22, Sprint 23"

Story Points / Story point estimate

Number

Estimate in story points

5

Original Estimate

Duration (sec/hrs)

First time estimate

8h

Remaining Estimate

Duration

Time left

2h

Time Spent

Duration

Time logged

6h

Parent / Epic Link

Text

The parent epic or item

GA-400

Labels

Text (multi)

Labels on the item

frontend

Components

Text (multi)

Project components

API

Fix Version

Text (multi)

Target release

v2.3

Project Key / Name

Text

Project the item belongs to

GA

Derived or calculated fields (common in sprint report datasets)
Column

Data type

Description

Added After Start

Boolean

TRUE if the item joined the sprint after it started (the * in the report)

Removed From Sprint

Boolean

TRUE if the item was removed mid-sprint

Completed In Sprint

Boolean

TRUE if the item reached Done before the sprint closed

Initial Estimate

Number

Estimate when the sprint started, or when the item was added

Final Estimate

Number

Estimate when the sprint closed

Estimate Change

Number

Final minus initial estimate

Carried Over

Boolean

TRUE if the item was incomplete and moved to a later sprint

Sprint Count

Integer

How many sprints the item has been in

Cycle Time (days)

Number

Time from first "In Progress" to "Done"

Lead Time (days)

Number

Time from Created to Resolved