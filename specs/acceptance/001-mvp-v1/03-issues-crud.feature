@SPEC-001
Feature: Issues — creation, reading and updating

  # The identifier is team.key + '-' + number, the number is per team and is never reused
  # (`MAX(number) + 1` in the same transaction, with no counter in `team`: SPEC-010 R2).

  @REQ-F011 @smoke
  Scenario: the first issue of a team gets the number 1
    Given a newly created team "ENG"
    When I run "hifa issue create --team ENG --title 'Broken login' --json"
    Then the exit code is 0
    And the "identifier" field is "ENG-1"
    And the "number" field is 1
    And the "priority" field is 0

  @REQ-F011 @smoke
  Scenario: numbers are independent per team
    Given the team "ENG" with 2 issues
    When I run "hifa issue create --team PLAT --title 'First for PLAT' --json"
    Then the identifier of the new issue is "PLAT-1"

  @REQ-F011 @smoke
  Scenario: a number in trash is not reused
    Given a team "ENG" with the issues "ENG-1" and "ENG-2"
    When I run "hifa issue delete ENG-2 --yes" and then "hifa issue create --team ENG --title 'Next' --json"
    Then the identifier of the new issue is "ENG-3"

  @REQ-F011 @smoke
  Scenario: two processes creating issues at the same time do not collide
    Given a team "ENG" with no issues
    When I launch 20 processes "hifa issue create --team ENG --title 'parallel'" simultaneously
    Then the 20 commands end with exit code 0
    And there are 20 issues in "ENG"
    And the numbers are exactly 1 to 20 with no gaps
    And there are no duplicate identifiers

  @REQ-F012 @smoke
  Scenario Outline: show accepts the identifier or the number with team
    Given a database with the issue "ENG-7"
    When I run "hifa issue show <ref> --team ENG --json"
    Then the exit code is 0
    And the "identifier" field is "ENG-7"
    Examples:
      | ref   |
      | ENG-7 |
      | 7     |

  @REQ-F012 @smoke
  Scenario: show accepts the ULID of the issue
    Given a database with the issue "ENG-7" and its ULID in the id column
    When I run "hifa issue show $ULID --json"
    Then the exit code is 0
    And the "identifier" field is "ENG-7"

  @REQ-F012 @smoke
  Scenario: show with a nonexistent reference returns exit code 3
    When I run "hifa issue show ENG-999 --json"
    Then the exit code is 3
    And the "error.code" field of stderr is "not_found"
    And the "error.field" field of stderr is "ref"

  @REQ-F013 @smoke
  Scenario: edit writes one history row per changed field
    Given the issue "ENG-4" with priority 0 and title "Old"
    When I run "hifa issue edit ENG-4 --priority 2 --title 'New' --estimate 3"
    Then the exit code is 0
    And the issue has priority 2 and estimate 3.0
    And there are 3 rows in "issue_history" for "ENG-4"
    And the recorded fields are "priority", "title" and "estimate"

  @REQ-F013 @smoke
  Scenario: edit with an identical value generates no history
    Given the issue "ENG-4" with priority 2
    When I run "hifa issue edit ENG-4 --priority 2"
    Then the exit code is 0
    And no row was added to "issue_history" for "ENG-4"

  @REQ-F013 @smoke
  Scenario: edit with an unknown flag aborts without writing
    When I run "hifa issue edit ENG-4 --sprint 5"
    Then the exit code is 2
    And the error.code is "invalid_option"
    And stderr mentions "--sprint"
    And the issue "ENG-4" did not change its updated_at

  @REQ-F014 @smoke
  Scenario Outline: moving to a completed or canceled state is idempotent by type
    Given the issue "ENG-9" in the state "Todo"
    When I run "hifa issue move ENG-9 --state <state>" twice
    Then the exit code is 0 on both runs
    And the state_type of the issue is "<type>"
    And the "<milestone>" field is not null
    Examples:
      | state    | type      | milestone    |
      | Done     | completed | completed_at |
      | Canceled | canceled  | canceled_at  |

  @REQ-F014 @smoke
  Scenario: going back to the default unstarted state clears completed_at
    Given the issue "ENG-9" completed
    When I run "hifa issue move ENG-9 --state Todo --json"
    Then the "state_name" field is "Todo"
    And the completed_at field of the issue is null
