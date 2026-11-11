@SPEC-001
Feature: CSV export and import

  # The 11 documented columns and validation before writing.
  # `--on-conflict` ∈ {skip, update, fail} with default `fail` (SPEC-080 / SPEC-050 §2.5).

  @REQ-F029 @smoke
  Scenario: the CSV export emits the columns in the agreed order
    Given a team "ENG" with 2 issues
    When I run "hifa export --format csv --team ENG --out -"
    Then the first line is "Identifier,Title,Description,Assignee,Priority,Status,Labels,Project,Cycle,Estimate,Due Date"
    And there are 3 lines in total

  @REQ-F029 @smoke
  Scenario: priority is exported as a derived label
    Given the issue "ENG-61" with priority 1
    When I run "hifa export --format csv --team ENG --out -"
    Then the row of "ENG-61" has "Urgent" in the Priority column

  @REQ-F029 @smoke
  Scenario: labels containing a comma are emitted between quotes
    Given the issue "ENG-62" with the labels "Bug" and "Needs, triage"
    When I run "hifa export --format csv --team ENG --out -"
    Then the row of "ENG-62" has the Labels field between double quotes
    And the field parses back into 2 labels

  @REQ-F030 @smoke
  Scenario: dry-run validates and reports without writing
    Given a CSV with 3 new rows and 1 row with a nonexistent state
    When I run "hifa import archivo.csv --team ENG --dry-run --json"
    Then the "would_create" field is 3
    And the "errors" field has 1 element with the row number
    And the count of "issue" did not change

  @REQ-F030 @smoke
  Scenario: a non-ISO date aborts the import before inserting
    Given a CSV with 2 rows and a date "15/11/2026"
    When I run "hifa import archivo.csv --team ENG"
    Then the exit code is 2
    And the error.code is "validation_error"
    And stderr mentions row 2
    And the count of "issue" did not change

  @REQ-F030 @smoke
  Scenario: on-conflict update updates the existing issue
    Given a CSV with Identifier "ENG-70" and title "New title"
    And the issue "ENG-70" exists with title "Old"
    When I run "hifa import archivo.csv --team ENG --on-conflict update"
    Then the exit code is 0
    And the issue "ENG-70" has title "New title"
    And there is history of field "title" for "ENG-70"

  @REQ-F030 @smoke
  Scenario: on-conflict fail is the default behavior
    Given a CSV with an already existing Identifier "ENG-70"
    When I run "hifa import archivo.csv --team ENG --json"
    Then the exit code is 4
    And the error.code is "conflict"
    And the issue "ENG-70" keeps its original title
