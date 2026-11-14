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
    When I run "hifa issue move ENG-3 --state Todo --after ENG-1"
    Then the board_order of "ENG-3" is 1536.0
    And the order of the "Todo" column is ENG-1, ENG-3, ENG-2
    And the board_order of "ENG-1" and "ENG-2" did not change

  @REQ-F019 @smoke
  Scenario: moving to the end adds the 1024 step
    Given the last issue of "Todo" has board_order 4096
    When I run "hifa issue move ENG-9 --state Todo"
    Then the board_order of "ENG-9" is 5120.0

  @REQ-F019 @smoke
  Scenario: a gap smaller than the threshold triggers the column rebalance
    Given the issues "ENG-1" and "ENG-2" in "Todo" with board_order 1024.0 and 1024.0000005
    And "ENG-9" in "Done" with board_order 1024.0
    When I run "hifa issue move ENG-9 --state Todo --after ENG-1"
    Then the rebalance renumbers "ENG-1" and "ENG-2" to multiples of 1024.0
    And the order of the "Todo" column is ENG-1, ENG-9, ENG-2
    And no board_order is repeated inside the "Todo" column

  # The fixture is the point of the next scenario. "In Progress" starts **degraded**: "ENG-6" and
  # "ENG-7" are 5e-7 apart, closer than `MIN_GAP = 1e-6` (SPEC-020 §2.4), and move 1 enters that
  # column, so the rebalance of SPEC-040 R9 trigger 1 runs on the **first** move instead of waiting
  # for the 29 lucky splits of one slot that a clean column never produces (measured: 0 rebalances in
  # 300 seeded runs of the clean-column fixture this scenario used to declare).
  # "The order the moves implied" is the order of an abstract replay of the same 200 moves: a move
  # with an `--after` anchor puts the issue immediately after it in the target column, a move with no
  # anchor appends it, every other issue keeps its relative order — and a repair (SPEC-040 R9)
  # preserves that order and renumbers the column with step 1024.0.
  # Draws are `random.Random(20261113)`: per move the issue, then the target state, then one value in
  # `[0, 1)` — below 0.25 (or an empty target column) means no anchor, otherwise the anchor is a
  # second draw over the other issues of the target column.
  @REQ-F019 @smoke @slow
  Scenario: 200 random moves keep every column in the implied order and its positions unique
    Given the team "ENG" with the states "Todo", "In Progress" and "Done" and no "Triage" column
    And "ENG-1".."ENG-4" in "Todo", "ENG-5".."ENG-8" in "In Progress" and "ENG-9".."ENG-12" in "Done"
    And every column starts on the 1024.0 ladder, in identifier order: 1024.0, 2048.0, 3072.0, 4096.0
    And the exception is "ENG-7" at 2048.0000005, so "ENG-6" and "ENG-7" are 5e-7 apart and "In Progress" starts degraded
    And a sample of 200 moves seeded with 20261113, whose move 1 is "ENG-12" into "In Progress" with no anchor
    And moves 2-200 of the sample move a random issue other than "ENG-6" and "ENG-7" to a random state, its own included
    And each of those moves takes a random other issue of the target column as the `--after` anchor, or no anchor one draw in four
    When I run the 200 moves of the sample in order
    Then every column is in the order the moves imply, as the replay above defines it
    And no board_order is repeated inside a column
    And no column has two neighbours closer than 1e-6, the gap that the repair of move 1 erases
