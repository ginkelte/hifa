@SPEC-001
Feature: List filters and ordering

  # Filters are combined with AND across families; a repeated value is OR inside the state,
  # state-type and priority families and AND for labels (SPEC-030 §2.6, SPEC-050 §2.5.3.1).
  # `issue list` is the only command that paginates in v1: its envelope is
  # {items, next_cursor, has_more, total_estimate} (D-011). The other lists carry `items` alone.

  @REQ-F016 @smoke
  Scenario: combined filters are applied with AND
    Given 5 issues in "ENG": 2 with label "Bug", 1 of them assigned to "ana", 1 urgent
    When I run "hifa issue list --team ENG --label Bug --assignee ana --open --json"
    Then the "items" field has 1 element
    And the only identifier returned is the one of the issue meeting the three conditions

  @REQ-F016 @smoke
  Scenario: several labels require all of them to be present
    Given the issue "ENG-20" with labels "Bug" and "Backend"
    And the issue "ENG-21" with only label "Bug"
    When I run "hifa issue list --label Bug --label Backend --json"
    Then the result contains "ENG-20"
    And the result does not contain "ENG-21"

  @REQ-F016 @smoke
  Scenario: repeated states are combined with OR
    Given issues in the states "Todo", "In Progress" and "Done"
    When I run "hifa issue list --state Todo --state 'In Progress' --all --json"
    Then the result contains the issue of "Todo"
    And the result contains the issue of "In Progress"
    And the result does not contain the issue of "Done"

  @REQ-F016 @smoke
  Scenario: repeated priorities are combined with OR and zero is a value, not a NULL
    Given issues with priority 0, 1 and 3
    When I run "hifa issue list --priority 1 --priority 0 --json"
    Then the result contains the issue with priority 1
    And the result contains the issue with priority 0
    And the result does not contain the issue with priority 3

  @REQ-F016 @smoke
  Scenario: the state filter accepts name and type
    Given issues in the states "Todo" and "In Progress"
    When I run "hifa issue list --state-type started --json" and then "hifa issue list --state Todo --json"
    Then the first output only contains issues of the state "In Progress"
    And the second output contains the ones of "Todo"

  @REQ-F016 @smoke
  Scenario: the default limit is 50
    Given a team with 120 issues
    When I run "hifa issue list --json"
    Then the "items" field has 50 elements
    And the "has_more" field is true
    And the "next_cursor" field is not null
    And the "total_estimate" field is null

  @REQ-F016 @smoke
  Scenario Outline: a limit outside 1-200 is rejected
    When I run "hifa issue list --limit <limit> --json"
    Then the exit code is 2
    And the "error.code" field is "validation_error"
    And the "error.field" field is "limit"
    Examples:
      | limit |
      | 0     |
      | 201   |

  @REQ-F016 @smoke
  Scenario Outline: the visibility flags select one layer
    Given the open issue "ENG-30", the closed issue "ENG-31", the archived issue "ENG-32"
    And the issue "ENG-33" archived and then trashed
    When I run "hifa issue list <flags> --json"
    Then the result <expectation>
    Examples:
      | flags            | expectation                                                  |
      | --all            | contains "ENG-30" and "ENG-31" and not "ENG-32" nor "ENG-33" |
      | --all --archived | contains only "ENG-32"                                       |
      | --trashed        | contains only "ENG-33"                                       |

  @REQ-F016 @smoke
  Scenario: archived and trashed together are rejected
    When I run "hifa issue list --archived --trashed --json"
    Then the exit code is 2
    And the "error.code" field is "invalid_option"

  @REQ-F016
  Scenario: --mine and --unassigned cannot be combined
    When I run "hifa issue list --mine --unassigned --json"
    Then the exit code is 2
    And the "error.code" field is "invalid_option"
    And the "error.field" field is "assignee"

  @REQ-F016
  Scenario: --parent lists direct children and no grandchildren
    Given the issue "ENG-40" with the child "ENG-41" and the grandchild "ENG-42"
    When I run "hifa issue list --parent ENG-40 --json"
    Then the result contains "ENG-41"
    And the result does not contain "ENG-42"

  @REQ-F016
  Scenario: --query filters titles and does not search descriptions
    Given the issue "ENG-50" titled "Rotate the tokens" with the description "cron"
    And the issue "ENG-51" titled "Cron cleanup" with the description "rotate"
    When I run "hifa issue list --query Rotate --json"
    Then the result contains "ENG-50"
    And the result does not contain "ENG-51"

  @REQ-F017 @smoke
  Scenario: --sort priority puts Urgent first and No priority last
    Given 4 issues with priority 0, 1, 3 and 4
    When I run "hifa issue list --sort priority --json"
    Then the order of priorities is 1, 3, 4, 0

  @REQ-F017 @smoke
  Scenario: ordering by due_date leaves null dates at the end
    Given 3 issues with due_date "2026-11-10", null and "2026-11-05"
    When I run "hifa issue list --sort due --json"
    Then the order of due_date is "2026-11-05", "2026-11-10", null

  @REQ-F017 @smoke
  Scenario: two runs produce the same order
    Given a team with 10 issues and repeated priorities
    When I run "hifa issue list --json" twice and compare the identifiers
    Then the two lists of identifiers are identical

  @REQ-F017
  Scenario: --reverse inverts the order of a non-null key
    Given 3 issues created in the order "ENG-60", "ENG-61", "ENG-62"
    When I run "hifa issue list --sort created --json" and then the same command with "--reverse"
    Then the first output orders "ENG-60", "ENG-61", "ENG-62"
    And the second output orders "ENG-62", "ENG-61", "ENG-60"

  @REQ-F017 @smoke
  Scenario: --reverse inverts the due dates and leaves the undated last
    Given 3 issues with due_date "2026-11-05", "2026-11-10" and null
    When I run "hifa issue list --sort due --reverse --json"
    Then the order of due_date is "2026-11-10", "2026-11-05", null

  @REQ-F017 @smoke
  Scenario: --reverse inverts the priorities and leaves No priority last
    Given 4 issues with priority 0, 1, 3 and 4
    When I run "hifa issue list --sort priority --reverse --json"
    Then the order of priorities is 4, 3, 1, 0

  @REQ-F017 @smoke
  Scenario: paging across the undated rows neither repeats nor skips a row
    Given 3 issues with due_date "2026-11-05", "2026-11-10", "2026-11-15" and 2 issues without due_date
    When I request "hifa issue list --sort due --limit 2 --json" and then its two next pages
    Then the three pages add up to 5 identifiers
    And the 5 identifiers are distinct
    And the last two items of the three pages are the undated issues

  @REQ-F017 @smoke
  Scenario: the last page reports no more rows and no cursor
    Given a team with 3 issues
    When I request the first page with "--limit 2" and then the next page with its next_cursor
    Then the first page has 2 elements and "has_more" true
    And the second page has 1 element
    And the "has_more" field of the second page is false
    And the "next_cursor" field of the second page is null

  @REQ-F017 @smoke
  Scenario: a page exactly as long as the limit is still the last page
    Given a team with 4 issues
    When I run "hifa issue list --limit 2 --json" and then the next page with its next_cursor
    Then the second page has 2 elements
    And the "has_more" field of the second page is false
    And the "next_cursor" field of the second page is null

  @REQ-F017 @smoke
  Scenario: two consecutive pages cover the list with no duplicates and no holes
    Given 7 issues all with priority 2, so the id is the only tie-breaker
    When I request "hifa issue list --sort priority --limit 3 --json" and then its next two pages
    Then the three pages add up to 7 identifiers
    And the 7 identifiers are distinct
    And the 7 identifiers are the ones of the 7 issues

  @REQ-F017 @smoke
  Scenario: a page whose rows left the result set is empty and does not fail
    Given 3 open issues and a first page of 2 issues
    And the third issue is closed before the second page is requested
    When I request the second page with that next_cursor and the same "--open" filter
    Then the exit code is 0
    And the "items" field is empty
    And the "has_more" field is false
    And the "next_cursor" field is null
    And the "total_estimate" field is null

  @REQ-F017 @smoke
  Scenario: a cursor from another order is rejected, not restarted
    Given a team with 5 issues
    When I request "--sort created --limit 2", keep its next_cursor and run "--sort due --cursor" with it
    Then the exit code is 2
    And the "error.code" field is "validation_error"
    And the "error.field" field is "cursor"
    And stdout is empty

  @REQ-F017 @smoke
  Scenario: a cursor that is not a cursor is rejected
    When I run "hifa issue list --cursor 'bm90LWEtY3Vyc29y' --json"
    Then the exit code is 2
    And the "error.code" field is "validation_error"
    And the "error.field" field is "cursor"
    And stdout is empty
