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

  @REQ-F028 @smoke
  Scenario: one row per column changed, in the fixed order
    Given the issue "ENG-61" with title "Old", the derived branch "eng-61-old", no description and priority 0
    When I run "hifa issue edit ENG-61 --priority 2 --title 'New' --description 'body'"
    Then "issue_history" has 4 rows for "ENG-61"
    And the fields are, in this order, "title", "description", "priority" and "branch_name"

  @REQ-F028 @smoke
  Scenario: the actor of the change is the resolved member
    Given the issue "ENG-62"
    When I run "hifa --member ana issue edit ENG-62 --priority 1"
    Then the last history row of "ENG-62" has actor "ana"

  @REQ-F028 @smoke
  Scenario: moving to another state is a state_id change
    Given the issue "ENG-63" in the state "Todo"
    When I run "hifa issue move ENG-63 --state 'In Progress'"
    Then "issue_history" has exactly one row for "ENG-63" with field "state_id"
    And "issue_history" also has one row with field "board_order" and one row with field "started_at"
    And its old_value is the ULID of "Todo" and its new_value the ULID of "In Progress"
    And no row stores the display name "In Progress"

  @REQ-F028 @smoke
  Scenario: a move records the board_order it assigned
    Given the issue "ENG-66" in the state "Todo" with board_order 1024.0
    And the issue "ENG-67" in the state "In Progress" with board_order 2048.0
    When I run "hifa issue move ENG-66 --state 'In Progress'"
    Then "issue_history" has exactly one row for "ENG-66" with field "board_order"
    And its old_value is 1024.0
    And its new_value is greater than 2048.0 and equals the board_order of "ENG-66"

  @REQ-F028 @smoke
  Scenario: archive and restore are history rows of archived_at
    Given the issue "ENG-64"
    When I run "hifa issue archive ENG-64 --yes" and then "hifa issue restore ENG-64"
    Then the exit code of the first command is 0
    And the exit code of the second command is 0
    And the row of the archive has field "archived_at" and new_value not null
    And the row of the restore has field "archived_at" and new_value null

  @REQ-F028 @smoke
  Scenario: restore of an issue that is archived and trashed writes one row per layer
    Given the issue "ENG-68" archived and in trash
    When I run "hifa issue restore ENG-68 --json"
    Then the exit code is 0
    And "issue_history" has exactly 2 rows for "ENG-68"
    And the first row has field "archived_at" and new_value null
    And the second row has field "trashed" and new_value "0"

  @REQ-F028 @smoke
  Scenario: a command that changes nothing writes no row
    Given the issue "ENG-65" with priority 3 in the state "Todo" as the last issue of that column
    When I run "hifa issue edit ENG-65 --priority 3" and then "hifa issue move ENG-65 --state Todo"
    Then the exit code of the first command is 0
    And the exit code of the second command is 0
    And "issue_history" has 0 rows for "ENG-65"
