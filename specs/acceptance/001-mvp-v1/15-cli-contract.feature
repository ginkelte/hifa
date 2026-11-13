@SPEC-001
Feature: CLI contract with --json

  # A single JSON document on stdout, stable exit codes, zero ANSI.
  # Single entity: canonical object without a wrapper (SPEC-050 §2.4.2). List: envelope
  # {items, next_cursor, has_more, total_estimate}. Error: stderr with
  # {"error":{"code","message","field"}} and empty stdout.

  @REQ-F032 @smoke
  Scenario: a list emits the pageable envelope without a wrapper
    When I run "hifa issue list --json"
    Then stdout is a single JSON object
    And the object has exactly the keys "items", "next_cursor", "has_more" and "total_estimate"
    And the object does not have the key "ok"

  @REQ-F032 @smoke
  Scenario: the error goes to stderr with code, message and field
    When I run "hifa issue show ENG-999 --json"
    Then the exit code is 3
    And stdout is empty
    And the "error.code" field of stderr is "not_found"
    And the "error.field" field of stderr is "ref"
    And the "error.message" field of stderr is not empty

  @REQ-F032 @smoke
  Scenario: with --json there is no human text nor ANSI sequences on stdout
    When I run "hifa issue create --team ENG --title 'With color' --json"
    Then stdout does not contain the ANSI escape character
    And stdout parses as JSON with no additional text

  @REQ-F032 @smoke
  Scenario: a mutation emits the canonical object without a wrapper
    When I run "hifa issue edit ENG-1 --priority 2 --json"
    Then stdout is a single JSON object
    And the object has the key "identifier" and the key "updated_at"
    And the object does not have the key "items"

  @REQ-F032 @smoke
  Scenario: a mutation prints one row with the issue columns
    When I run "hifa issue move ENG-1 --state 'In Progress'"
    Then the exit code is 0
    And stdout has 2 lines
    And the header is "IDENTIFIER  P  STATE  ASSIGNEE  TITLE  LABELS  DUE"

  @REQ-F032 @smoke
  Scenario Outline: --quiet prints only the affected identifier
    When I run "<command> --quiet"
    Then stdout is exactly "ENG-1"
    Examples:
      | command                            |
      | hifa issue edit ENG-1 --priority 2 |
      | hifa issue move ENG-1 --state Todo |
      | hifa issue archive ENG-1 --yes     |
      | hifa issue restore ENG-1           |

  @REQ-F032 @smoke
  Scenario Outline: the write verbs run without a server
    Given an initialized workspace and no "hifa serve" process
    When I run "<command> --json" with the socket module instrumented
    Then the exit code is 0
    And no socket was opened
    Examples:
      | command                                     |
      | hifa issue edit ENG-1 --priority 2          |
      | hifa issue move ENG-1 --state 'In Progress' |
      | hifa issue archive ENG-1 --yes              |
      | hifa issue restore ENG-1                    |

  @REQ-F032 @smoke
  Scenario Outline: exit codes are stable per error type
    When I run "<command>" with --json
    Then the exit code is <code>
    Examples:
      | command                                   | code |
      | hifa issue show ENG-999                   | 3    |
      | hifa issue edit ENG-1 --sprint 5          | 2    |
      | hifa label add --name Bug --color '#000'  | 4    |
      | hifa issue edit ENG-1                     | 2    |
      | hifa issue move ENG-1 --state Nope        | 3    |
      | hifa issue archive ENG-999                | 3    |
      | hifa issue restore ENG-999                | 3    |
