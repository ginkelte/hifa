@SPEC-001
Feature: Hierarchy and relations

  # Sub-issues with bounded depth and directional relations.
  # The hierarchy is created with `issue create --parent`; the tree and the blocked_by are
  # port read models (SPEC-050 §2.5 does not publish a tree verb; see Open Questions).

  @REQ-F020 @smoke
  Scenario: a three-level tree renders completely
    Given "ENG-1" with sub-issue "ENG-2" and "ENG-2" with sub-issue "ENG-3"
    When I query the tree of "ENG-1" as text
    Then the exit code is 0
    And stdout shows three lines with indentation of 0, 2 and 4 spaces

  @REQ-F020 @smoke
  Scenario: nesting level 6 is rejected
    Given a chain of 5 nested issues from "ENG-1" to "ENG-5"
    When I run "hifa issue create 'Level 6' --parent ENG-5"
    Then the exit code is 4
    And stderr mentions the maximum depth of 5
    And the count of "issue" did not change

  @REQ-F020 @smoke
  Scenario: creating a sub-issue appends it after its siblings
    Given "ENG-1" with two sub-issues with sub_issue_sort_order 1024 and 2048
    When I run "hifa issue create 'Third' --parent ENG-1"
    Then the new sub-issue has sub_issue_sort_order 3072.0

  @REQ-F021 @smoke
  Scenario: blocks is stored once and blocked_by is derived
    Given the issues "ENG-1" and "ENG-2"
    When I run "hifa issue relate ENG-1 blocks ENG-2" and then "hifa issue show ENG-2 --with relations --json" and then "hifa issue show ENG-1 --with relations --json"
    Then there is 1 row in "issue_relation" with type "blocks"
    And the relation of "ENG-2" with "ENG-1" appears as blocked_by
    And the relations of "ENG-1" do not include any blocked_by

  @REQ-F021 @smoke
  Scenario: a relation with itself is rejected
    When I run "hifa issue relate ENG-1 related ENG-1"
    Then the exit code is 4
    And the count of "issue_relation" did not change

  @REQ-F021 @smoke
  Scenario: the exact duplicate relation is rejected
    Given "ENG-1" related to "ENG-2" as "related"
    When I run "hifa issue relate ENG-1 related ENG-2"
    Then the exit code is 4
    And the error.code is "conflict"
