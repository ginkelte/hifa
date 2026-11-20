@SPEC-001
Feature: Workspace and team labels

  # Two label levels with the same name (partial unique indexes from SPEC-010 R6),
  # and singleSelect groups where the second label replaces the first one.

  @REQ-F024 @smoke
  Scenario: a workspace label and a team label with the same name coexist
    Given a workspace label "Bug"
    When I run "hifa label add --team ENG --name Bug --color '#EB5757'"
    Then the exit code is 0
    And there are 2 rows in "label" with name "Bug"
    And one has a null team_id and the other's team_id is the ULID of "ENG"

  @REQ-F024 @smoke
  Scenario: duplicating a label at the same level is rejected
    Given a workspace label "Bug"
    When I run "hifa label add --name Bug --color '#000000'"
    Then the exit code is 4
    And the error.code is "conflict"

  @REQ-F024 @smoke
  Scenario: in a singleSelect group the second label replaces the first one
    Given a group "Severity" of type singleSelect with the children "High" and "Low"
    And the issue "ENG-40" with the label "High"
    When I run "hifa issue label ENG-40 Low --json"
    Then the exit code is 0
    And the issue has a single label from the group "Severity"
    And that label is "Low"
    And "High" still exists in "label" without being associated with "ENG-40"

  @REQ-F024 @smoke
  Scenario: the JSON answer of the replacement names the label it took off the issue
    Given a group "Severity" of type singleSelect with the children "High" and "Low"
    And the issue "ENG-40" with the label "High"
    When I run "hifa issue label ENG-40 Low --json"
    Then the exit code is 0
    And "replaced.name" is "High"
    And "issue.labels" holds a single element named "Low"

  @REQ-F024 @smoke
  Scenario: renaming a label keeps the issues that wear it
    Given the workspace label "Bug" on the issue "ENG-40"
    When I run "hifa label update Bug --name Defect"
    Then the exit code is 0
    And there is no row in "label" with name "Bug"
    And there is 1 row in "label" with name "Defect"
    And "ENG-40" still carries that label
    And no row was added to "issue_history" for "ENG-40"

  @REQ-F024 @smoke
  Scenario: recoloring a label changes the color and nothing else
    Given the workspace label "Bug" with color "#EB5757"
    When I run "hifa label update Bug --color '#BB87FC'"
    Then the exit code is 0
    And the label "Bug" has color "#BB87FC"
    And the label "Bug" keeps its name

  @REQ-F024 @smoke
  Scenario: label update with no flag is not a silent success
    Given the workspace label "Bug"
    When I run "hifa label update Bug"
    Then the exit code is 2
    And the error.code is "no_changes"

  @REQ-F024 @smoke
  Scenario: renaming a label onto a name of the same level is a conflict
    Given the workspace labels "Bug" and "Defect"
    When I run "hifa label update Bug --name Defect"
    Then the exit code is 4
    And the error.code is "conflict"
    And there are 2 rows in "label" with team_id null

  @REQ-F024 @smoke
  Scenario: a label an issue wears is not retired
    Given the issue "ENG-40" with the workspace label "Bug"
    When I run "hifa label archive Bug"
    Then the exit code is 4
    And the error.code is "conflict"
    And the label "Bug" has a null retired_at

  @REQ-F024 @smoke
  Scenario: a label no issue wears is retired
    Given the workspace label "infra" and no issue wearing it
    When I run "hifa label archive infra"
    Then the exit code is 0
    And the label "infra" has a non-null retired_at
    And no row was deleted from "label"

  @REQ-F024 @smoke
  Scenario: label archive --undo clears retired_at
    Given the retired workspace label "infra"
    When I run "hifa label archive infra --undo"
    Then the exit code is 0
    And the label "infra" has a null retired_at

  @REQ-F024 @smoke
  Scenario: the undo of a retire that never happened is a conflict
    Given the workspace label "Bug" and no retirement
    When I run "hifa label archive Bug --undo"
    Then the exit code is 4
    And the error.code is "conflict"

  @REQ-F024 @smoke
  Scenario: label show prints the one label it was asked for
    Given the workspace label "Bug" with color "#EB5757" and no team
    When I run "hifa label show Bug"
    Then the exit code is 0
    And stdout contains "Bug"
    And stdout contains "#EB5757"

  @REQ-F024 @smoke
  Scenario: label list --team includes the workspace labels the team can wear
    Given the workspace label "Bug" and the team label "infra" of the team "ENG"
    When I run "hifa label list --team ENG --json"
    Then the exit code is 0
    And "items" has 2 elements
    And one of them has a null team_id and the other's team_id is the ULID of "ENG"

  @REQ-F024 @smoke
  Scenario: issue label --create creates the label in the issue's team
    Given the issue "ENG-40" and no label named "infra" anywhere
    When I run "hifa issue label ENG-40 infra --create --color '#5E6AD2'"
    Then the exit code is 0
    And there is 1 row in "label" with name "infra" and color "#5E6AD2"
    And the label "infra" has team_id the ULID of "ENG"
    And "ENG-40" carries that label

  @REQ-F024 @smoke
  Scenario: a color without --create is refused instead of written nowhere
    Given the issue "ENG-40" and the workspace label "Bug"
    When I run "hifa issue label ENG-40 Bug --color '#5E6AD2'"
    Then the exit code is 2
    And the error.code is "invalid_option"

  @REQ-F024 @smoke
  Scenario: detaching a label the issue does not wear is a missing link
    Given the issue "ENG-40" with the label "Bug" and the workspace label "infra"
    When I run "hifa issue unlabel ENG-40 infra"
    Then the exit code is 3
    And the error.code is "not_found"
    And the "error.field" field is "label_link"
    And "ENG-40" still carries only "Bug"

  @REQ-F024 @smoke
  Scenario: unlinking a label name that exists nowhere is also exit 3, not exit 2
    Given the issue "ENG-40" with the label "Bug" and no label named "infra" anywhere
    When I run "hifa issue unlabel ENG-40 infra"
    Then the exit code is 3
    And the error.code is "not_found"
    And the "error.field" field is "label"
    And "ENG-40" still carries "Bug"

  @REQ-F024 @smoke
  Scenario: retiring a label that is already retired is a conflict
    Given the retired workspace label "infra"
    When I run "hifa label archive infra"
    Then the exit code is 4
    And the error.code is "conflict"
    And the label "infra" has a non-null retired_at

  @REQ-F024 @smoke
  Scenario: N labels are N transactions and the first failure decides the exit code
    Given the issue "ENG-40" and the workspace label "Bug" and no label named "infra"
    When I run "hifa issue label ENG-40 Bug infra"
    Then the exit code is 2
    And stdout is empty
    And "ENG-40" carries "Bug"
    And a single row was added to "issue_history" for "ENG-40"
