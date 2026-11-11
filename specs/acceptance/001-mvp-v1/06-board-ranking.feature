@SPEC-001
Feature: Board and fractional ranking

  # The board groups by state and the order inside the column is fractional.
  # `hifa board` does not exist in SPEC-050 §2.5: the board is queried through the read model
  # (see Open Questions of SPEC-001 §10.1).

  @REQ-F018 @smoke
  Scenario: the board groups by state ordered by position
    Given the team "ENG" with the 6 states at positions 1.0 to 6.0 and 3 issues in "Todo"
    When I query the board of the team "ENG" in JSON
    Then the "columns" field has 6 elements
    And the first column has state_name "Triage"
    And the "Todo" column reports count 3
    And the "In Progress" column reports wip 0

  @REQ-F018 @smoke
  Scenario: inside the column the issues are ordered by board_order
    Given 3 issues in "Todo" with board_order 1024, 3072 and 2048
    When I query the board of the team "ENG" in JSON
    Then the order of the "Todo" column is 1024, 2048, 3072

  @REQ-F019 @smoke
  Scenario: moving between two issues uses the average of the neighbors
    Given the issues "ENG-1" and "ENG-2" in "Todo" with board_order 1024 and 2048
    When I run "hifa issue move ENG-3 --state Todo --after ENG-1 --before ENG-2"
    Then the board_order of "ENG-3" is 1536.0
    And the "rebalanced" field is false
    And the board_order of "ENG-1" and "ENG-2" did not change

  @REQ-F019 @smoke
  Scenario: moving to the end adds the 1024 step
    Given the last issue of "Todo" has board_order 4096
    When I run "hifa issue move ENG-9 --state Todo"
    Then the board_order of "ENG-9" is 5120.0

  @REQ-F019 @smoke
  Scenario: a gap smaller than the threshold triggers the column rebalance
    Given two issues with board_order 1024.0 and 1024.00005 in "Todo"
    When I run "hifa issue move ENG-9 --state Todo --after ENG-1 --before ENG-2"
    Then the "rebalanced" field is true
    And the board_order of the column are multiples of 1024.0
    And the relative order of the existing issues did not change
