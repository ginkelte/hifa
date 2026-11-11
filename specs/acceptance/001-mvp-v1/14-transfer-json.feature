@SPEC-001
Feature: JSON export and import

  # Full round-trip that preserves ULID, number and identifier.
  # `schema_version` of the dump = `user_version` of the database = 3 (migrations 001/002/003).

  @REQ-F031 @smoke
  Scenario: the JSON round-trip preserves identifiers
    Given a database with 5 issues and 3 comments
    When I run "hifa export --format json --out /tmp/h1/dump.json"
    And I run "hifa init --db /tmp/h2/nuevo.db --name Acme"
    And I run "hifa import /tmp/h1/dump.json --format json --db /tmp/h2/nuevo.db"
    Then the exit code is 0
    And the identifiers of the 5 issues are identical to the source
    And the ULIDs of the 5 issues are identical to the source
    And the "schema_version" field of the dump is 3
