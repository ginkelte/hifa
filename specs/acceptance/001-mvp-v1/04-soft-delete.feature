@SPEC-001
Feature: Layered deletion

  # delete (trash), restore and archive are distinct operations and all reversible:
  # physical deletion does not exist (D-015), so a hard-delete flag is an unknown
  # flag.

  @REQ-F015 @smoke
  Scenario: delete removes the issue from listings and board but keeps it
    Given the issue "ENG-11" in the state "Todo"
    When I run "hifa issue delete ENG-11 --yes" and then "hifa issue list --json" and then "hifa issue list --trashed --json"
    Then the issue "ENG-11" does not appear in the second output
    And the issue "ENG-11" appears in the third output
    And the "issue" row with identifier "ENG-11" has trashed 1

  @REQ-F015 @smoke
  Scenario: restore returns the issue to the listings
    Given the issue "ENG-11" in trash
    When I run "hifa issue restore ENG-11" and then "hifa issue list --json"
    Then the issue "ENG-11" appears in the list
    And its trashed is 0

  @REQ-F015 @smoke
  Scenario: archive sets archived_at without changing the state
    Given the issue "ENG-12" in the state "In Progress"
    When I run "hifa issue archive ENG-12 --yes"
    Then the archived_at field of the issue is not null
    And the state_name of the issue is still "In Progress"

  @REQ-F015 @smoke
  Scenario: archiving twice is a no-op that writes nothing
    Given the issue "ENG-12" archived at "2026-11-10T12:00:00Z"
    When I run "hifa issue archive ENG-12 --yes"
    Then the exit code is 0
    And the archived_at of "ENG-12" is still "2026-11-10T12:00:00Z"
    And no row was added to "issue_history" for "ENG-12"

  @REQ-F015 @smoke
  Scenario: an issue in trash is not archived
    Given the issue "ENG-12" in trash
    When I run "hifa issue archive ENG-12 --yes --json"
    Then the exit code is 2
    And the "error.code" field of stderr is "validation_error"
    And the archived_at of "ENG-12" is null

  @REQ-F015 @smoke
  Scenario: an archived issue can be trashed
    Given the issue "ENG-12" archived at "2026-11-10T12:00:00Z"
    When I run "hifa issue delete ENG-12 --yes --json"
    Then the exit code is 0
    And the archived_at of "ENG-12" is still "2026-11-10T12:00:00Z"
    And the trashed of "ENG-12" is 1
    And there is exactly 1 row in "issue_history" for "ENG-12" with field "trashed"

  @REQ-F015 @smoke
  Scenario: a parent with open sub-issues is not archived
    Given the issue "ENG-12" with two open sub-issues
    When I run "hifa issue archive ENG-12 --yes --json"
    Then the exit code is 2
    And the "error.code" field of stderr is "validation_error"
    And stderr mentions "CLOSE_WITH_OPEN_CHILDREN"
    And the archived_at of "ENG-12" is null
    And no row was added to "issue_history" for "ENG-12"

  @REQ-F015 @smoke
  Scenario: archive without --yes in a non-interactive session is refused
    Given the issue "ENG-12" not archived
    When I run "hifa issue archive ENG-12 --no-input"
    Then the exit code is 2
    And the "error.code" field of stderr is "confirmation_required"
    And the archived_at of "ENG-12" is null

  @REQ-F015 @smoke
  Scenario: restore is the undo of archive
    Given the issue "ENG-12" archived
    When I run "hifa issue archive ENG-12 --undo" and then "hifa issue list --json"
    Then the exit code of the first command is 0
    And the archived_at of "ENG-12" is null
    And the issue "ENG-12" appears in the second output
    And the last history row of "ENG-12" has field "archived_at" and new_value null

  @REQ-F015 @smoke
  Scenario: restore of a visible issue is a no-op
    Given the issue "ENG-12" visible and not archived
    When I run "hifa issue restore ENG-12"
    Then the exit code is 0
    And no row was added to "issue_history" for "ENG-12"

  @REQ-F015 @smoke
  Scenario: a physical deletion flag is unknown and does not touch the issue
    Given the issue "ENG-13" with 2 comments
    When I run "hifa issue delete ENG-13 --hard --yes"
    Then the exit code is 2
    And the error.code is "invalid_option"
    And the issue "ENG-13" still exists with 2 comments

  @REQ-F015 @smoke
  Scenario Outline: archive and restore reject the physical deletion flag
    Given the issue "ENG-13"
    When I run "<command>" with --json
    Then the exit code is 2
    And the "error.code" field of stderr is "invalid_option"
    And the issue "ENG-13" still exists
    Examples:
      | command                          |
      | hifa issue archive ENG-13 --hard |
      | hifa issue restore ENG-13 --hard |

  @REQ-F015 @smoke
  Scenario: delete keeps comments, attachments and relations
    Given the issue "ENG-13" with 2 comments, 1 attachment and 1 relation
    When I run "hifa issue delete ENG-13 --yes"
    Then the exit code is 0
    And the "issue" row with identifier "ENG-13" has trashed 1
    And the count of "comment" for that issue is 2
    And the count of "attachment" for that issue is 1
    And the count of "issue_relation" for that issue is 1
