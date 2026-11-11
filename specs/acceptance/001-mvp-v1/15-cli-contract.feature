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
  Scenario Outline: exit codes are stable per error type
    When I run "<command>" with --json
    Then the exit code is <code>
    Examples:
      | command                                   | code |
      | hifa issue show ENG-999                   | 3    |
      | hifa issue edit ENG-1 --sprint 5          | 2    |
      | hifa label add --name Bug --color '#000'  | 4    |
