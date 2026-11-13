@SPEC-001
Feature: Hierarchy and relations

  # Sub-issues with bounded depth and directional relations.
  # The hierarchy is created with `issue create --parent`; the tree and the directed `blocked_by`
  # reading are read models of the web edge (SPEC-050 §2.5 publishes no tree verb; SPEC-070 §2.3.4
  # declares `direction` and the `blocked_by` label). The CLI prints the stored `Relation` row, whose
  # `type` is never `blocked_by` (SPEC-050 §2.4.2, D-046).
  # The codes asserted here are the ones of SPEC-020 §6 and D-047: a hierarchy or relation
  # refusal is `validation_error` (exit 2) except a duplicate edge, which is `conflict` (exit 4).

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
    Then the exit code is 2
    And the error.rule is "PARENT_DEPTH_EXCEEDED"
    And stderr mentions the maximum depth of 5
    And the count of "issue" did not change

  @REQ-F020
  Scenario: moving a subtree past the depth bound is rejected
    Given the chain "ENG-1" with "ENG-2" and "ENG-2" with "ENG-3"
    And the chain "ENG-4" with "ENG-5" and "ENG-5" with "ENG-6"
    When I run "hifa issue edit ENG-4 --parent ENG-3"
    Then the exit code is 2
    And the error.rule is "PARENT_DEPTH_EXCEEDED"
    And the parent of "ENG-4" is null

  @REQ-F020
  Scenario: a parent from another team is rejected
    Given the "ENG" team with "ENG-1" and the "OPS" team with "OPS-1"
    When I run "hifa issue create 'Child' --team ENG --parent OPS-1"
    Then the exit code is 2
    And the error.rule is "PARENT_TEAM_MISMATCH"
    And the count of "issue" did not change

  @REQ-F020
  Scenario: a hierarchy cycle is rejected
    Given "ENG-1" with sub-issue "ENG-2" and "ENG-2" with sub-issue "ENG-3"
    When I run "hifa issue edit ENG-1 --parent ENG-3"
    Then the exit code is 2
    And the error.rule is "PARENT_CYCLE"
    And the parent of "ENG-1" is null

  @REQ-F020 @smoke
  Scenario: creating a sub-issue appends it after its siblings
    Given "ENG-1" with two sub-issues with sub_issue_sort_order 1024 and 2048
    When I run "hifa issue create 'Third' --parent ENG-1"
    Then the new sub-issue has sub_issue_sort_order 3072.0

  @REQ-F020 @smoke
  Scenario: archiving a parent does not reparent nor cascade
    Given "ENG-1" with the closed sub-issue "ENG-2"
    When I run "hifa issue archive ENG-1 --yes" and then "hifa issue show ENG-2 --json"
    Then the exit code is 0
    And the parent of "ENG-2" is still "ENG-1"
    And "ENG-2" is not archived
    And the count of "issue" did not change
    And "ENG-2" is listed by "hifa issue list --parent ENG-1 --all"

  @REQ-F021 @smoke
  Scenario: blocks is stored once and both ends read the same row
    Given the issues "ENG-1" and "ENG-2"
    When I run "hifa issue relate ENG-1 blocks ENG-2"
    Then there is 1 row in "issue_relation" with type "blocks"
    And "hifa issue show ENG-1 --with relations --json" returns exactly one relation, with issue_id "ENG-1" and related_issue_id "ENG-2"
    And "hifa issue show ENG-2 --with relations --json" returns exactly one relation, with issue_id "ENG-1" and related_issue_id "ENG-2"
    And both outputs carry the same relation id and the type "blocks"
    And neither output carries a "direction" or a "blocked_by" key

  @REQ-F021 @REQ-F035
  Scenario: the web read model resolves the far end as blocked_by
    Given "ENG-1" blocks "ENG-2"
    When the issue detail read model of "ENG-2" is built
    Then its "relations" holds one entry with type "blocks" and direction "in"
    And the relations panel of "ENG-2" shows it as blocked_by
    And the issue detail read model of "ENG-1" holds the same relation with direction "out"

  @REQ-F021
  Scenario: blocked_by is an input alias of the reverse blocks
    Given the issues "ENG-1" and "ENG-2"
    When I run "hifa issue relate ENG-2 blocked_by ENG-1"
    Then there is 1 row in "issue_relation" with issue_id "ENG-1", related_issue_id "ENG-2" and type "blocks"
    And the relation of "ENG-1" with "ENG-2" appears as blocks

  @REQ-F021 @smoke
  Scenario: a relation with itself is rejected
    When I run "hifa issue relate ENG-1 related ENG-1"
    Then the exit code is 2
    And the error.code is "validation_error"
    And the error.rule is "RELATION_SELF"
    And the count of "issue_relation" did not change

  @REQ-F021 @smoke
  Scenario: the exact duplicate relation is rejected
    Given "ENG-1" related to "ENG-2" as "related"
    When I run "hifa issue relate ENG-1 related ENG-2"
    Then the exit code is 4
    And the error.code is "conflict"
    And the error.rule is "RELATION_DUPLICATE"
    And the count of "issue_relation" did not change

  @REQ-F021 @smoke
  Scenario: the symmetric edge written from the other end is rejected
    Given "ENG-1" related to "ENG-2" as "related"
    When I run "hifa issue relate ENG-2 related ENG-1"
    Then the exit code is 4
    And the error.code is "conflict"
    And the error.rule is "RELATION_DUPLICATE"
    And the count of "issue_relation" did not change
