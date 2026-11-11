@SPEC-001
Feature: List filters and ordering

  # Filters are combined with AND and the order is deterministic.
  # Every list uses the pageable envelope {items, next_cursor, has_more, total_estimate} (D-011).

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
  Scenario: the state filter accepts name and type
    Given issues in the states "Todo" and "In Progress"
    When I run "hifa issue list --state-type started --json" and then "hifa issue list --state Todo --json"
    Then the first output only contains issues of the state "In Progress"
    And the second output contains the ones of "Todo"

  @REQ-F016 @smoke
  Scenario: the default limit is 50 and the accepted range is 1-200
    Given a team with 120 issues
    When I run "hifa issue list --json" and then "hifa issue list --limit 201 --json"
    Then the "items" field of the first output has 50 elements
    And the "has_more" field of the first output is true
    And the second invocation exits with exit code 2
    And the "error.code" field of the second invocation is "validation_error"

  @REQ-F017 @smoke
  Scenario: the default order puts Urgent first and No priority last
    Given 4 issues with priority 0, 1, 3 and 4
    When I run "hifa issue list --json"
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
