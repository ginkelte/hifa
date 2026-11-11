@SPEC-001
Feature: Change history

  # Every mutation leaves a per-field trace with actor and parseable values.
  # The history is read with `issue show --with history` (the port does not publish its own verb).

  @REQ-F028 @smoke
  Scenario: history is ordered chronologically and resolves the actor
    Given the issue "ENG-60" with 3 changes made by the member "ana"
    When I run "hifa issue show ENG-60 --with history --json"
    Then the "history" field has 3 elements
    And each entry has actor "ana"
    And the created_at values are in ascending order

  @REQ-F028 @smoke
  Scenario: history values are parseable JSON
    Given the issue "ENG-60" with priority changed from 0 to 2
    When I run "hifa issue show ENG-60 --with history --json"
    Then the entry of field "priority" has old_value "0" and new_value "2"
    And both values can be parsed as JSON
