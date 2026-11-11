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
    And one has a null team_id and the other has team_id "ENG"

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
