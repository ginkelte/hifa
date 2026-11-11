@SPEC-001
Feature: Teams and workflow states

  # Each team has a short key and states with a free-form name and a fixed type.
  # The state verbs are published (SPEC-050 §2.5.6: `hifa state list|create|update`), so these
  # scenarios run through the CLI; deleting a state does not exist (D-035) and those scenarios are
  # tagged @deferred. Key rotation still has no command: that scenario stays at the domain level
  # (see Open Questions of SPEC-001 §10.1, item 8).

  @REQ-F005 @smoke
  Scenario: new team with a valid key
    Given a database initialized in the workspace "Acme"
    When I run "hifa team create PLAT Platform --icon 🚀"
    Then the exit code is 0
    And a team exists with key "PLAT" and name "Platform"
    And the "icon" field of the team is "🚀"

  @REQ-F005 @smoke
  Scenario Outline: an invalid team key is rejected
    When I run "hifa team create <key> X"
    Then the exit code is 2
    And the error.code is "validation_error"
    And the "error.field" field is "key"
    Examples:
      | key          |
      | E            |
      | E-G          |
      | 1NG          |
      | WAYTOOLONGKEY |

  @REQ-F005 @smoke
  Scenario: a duplicate key is detected regardless of case
    Given an existing team with key "ENG"
    When I run "hifa team create Eng Duplicate"
    Then the exit code is 4
    And the error.code is "conflict"
    And the count of "team" did not change

  @REQ-F006 @smoke
  Scenario: changing the key rewrites the existing identifiers
    Given a team "ENG" with the issues "ENG-1" and "ENG-2"
    When the key of the team "ENG" is changed to "PLAT"
    Then the exit code is 0
    And the issue with number 1 has identifier "PLAT-1"
    And the issue with number 2 has identifier "PLAT-2"
    And there is 1 row in "team_previous_key" with key "ENG" for that team
    And there are 2 rows in "issue_history" with field "identifier"

  @REQ-F006 @smoke
  Scenario: an old identifier still resolves
    Given a team that changed from "ENG" to "PLAT" with the issue "PLAT-3"
    When I run "hifa issue show ENG-3 --json"
    Then the exit code is 0
    And the "identifier" field is "PLAT-3"
    And stderr contains "identifier renamed"

  # D-031: `archive_team` is declared but not implemented, so these two scenarios are not
  # runnable yet. The @deferred tag keeps them out of the runnable set until the use case lands.
  @REQ-F007 @smoke @deferred
  Scenario: archiving a team with open issues requires --yes
    Given a team "ENG" with 2 open issues
    When I run "hifa team archive ENG"
    Then the exit code is 4
    And the error.code is "conflict"
    And the team "ENG" has no archived_at

  @REQ-F007 @smoke @deferred
  Scenario: an archived team disappears from listings but keeps its issues
    Given a team "OPS" with no open issues with the issue "OPS-1"
    When I run "hifa team archive OPS --yes" and then "hifa team list --json" and then "hifa issue show OPS-1 --json"
    Then the team "OPS" does not appear in the list
    And the third invocation exits with exit code 0
    And the "identifier" field of the output is "OPS-1"

  @REQ-F008 @smoke
  Scenario: new state with a free-form name and a fixed type
    When I run "hifa state create 'In Review' --type started"
    Then the exit code is 0
    And a state "In Review" exists with type "started"
    And its position is greater than the one of "Done"

  @REQ-F008 @smoke
  Scenario: a state type outside the enum is rejected
    When I run "hifa state create 'QA' --type qa"
    Then the exit code is 2
    And the error.code is "validation_error"
    And the count of "workflow_state" did not change

  @REQ-F008 @smoke
  Scenario: the canonical order of a new team puts Todo before Backlog
    Given an initialized database with the team "ENG"
    When I run "hifa state list"
    Then the exit code is 0
    And the states are ordered "Triage", "Todo", "Backlog", "In Progress", "Done", "Canceled"
    And the state "Todo" has position 2.0 and is_default 1
    And the state "Backlog" has position 3.0

  @REQ-F008 @smoke
  Scenario: a reorder writes the order and no migration
    Given a team "ENG" with the states "Todo" at 2.0 and "Backlog" at 3.0
    When I run "hifa state update Backlog --after Todo"
    Then the exit code is 0
    And the position of "Backlog" is greater than the position of "Todo"
    And the table "schema_migration" still has 3 rows

  # D-035: deleting a state is not implemented — what happens to the issues that live in it is
  # undefined. The @deferred tag keeps these two out of the runnable set until the decision is made.
  @REQ-F009 @smoke @deferred
  Scenario: deleting a state with issues requires reassigning them
    Given the state "Backlog" has 3 issues
    When the state "Backlog" of the team "ENG" is deleted
    Then the exit code is 4
    And the error.code is "conflict"
    And the error message names the 3 affected issues
    And the state "Backlog" still exists

  @REQ-F009 @smoke @deferred
  Scenario: deleting a state while reassigning its issues happens in one transaction
    Given the state "Backlog" has 3 issues
    When the state "Backlog" of the team "ENG" is deleted moving its issues to "Todo"
    Then the exit code is 0
    And no state named "Backlog" exists
    And the 3 issues have state "Todo"
    And there are 3 new rows in "issue_history" with field "state_id"

  @REQ-F010 @smoke
  Scenario Outline: temporal milestones are derived from the destination type
    Given the issue "ENG-5" with a null completed_at and a null canceled_at
    When I run "hifa issue move ENG-5 --state <state>"
    Then the "<milestone>" field of the issue is not null
    Examples:
      | state       | milestone    |
      | In Progress | started_at   |
      | Done        | completed_at |
      | Canceled    | canceled_at  |

  @REQ-F010 @smoke
  Scenario: going back to an open state clears the completion milestone
    Given the issue "ENG-5" completed with a non-null completed_at
    When I run "hifa issue move ENG-5 --state 'In Progress'"
    Then the completed_at field of the issue is null
    And the started_at field keeps its original value
