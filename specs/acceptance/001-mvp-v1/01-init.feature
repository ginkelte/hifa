@SPEC-001
Feature: Database initialization and bootstrap

  # A user with no previous database runs `hifa init` and gets a usable tracker with
  # 1 workspace, 1 team, 6 workflow states (D-013), 4 labels and 1 member.
  # The current schema is user_version = 3 (migrations 001/002/003 from SPEC-010).

  @REQ-F001 @smoke
  Scenario: init creates the database with the schema and the seed data
    Given an empty directory with no database file
    When I run "hifa init --db /tmp/h1/hifa.db --name Acme --team-key ENG"
    Then the exit code is 0
    And the file "/tmp/h1/hifa.db" exists
    And the user_version of the database is 3
    And the table "schema_migration" has 3 rows with versions 1, 2 and 3
    And there are 6 rows in "workflow_state" with positions 1.0 to 6.0
    And the state "Triage" has type "triage"
    And the state "Todo" has type "unstarted" and is_default 1
    And the state "Todo" has position 2.0 and the state "Backlog" has position 3.0
    And exactly one workflow_state has is_default 1
    And there are 4 rows in "label" with names "Bug", "Feature", "Improvement" and "Docs"
    And the 4 labels have a null team_id
    And there is 1 row in "member" and 1 row in "team_membership"
    And the team has key "ENG" and timezone "America/Santiago"

  @REQ-F001 @smoke
  Scenario: init leaves no partial file when seeding fails
    Given a directory with read-only permissions
    When I run "hifa init --db /tmp/ro/hifa.db"
    Then the exit code is not 0
    And no file "/tmp/ro/hifa.db" exists
    And no file "/tmp/ro/hifa.db-journal" exists

  @REQ-F002 @smoke
  Scenario: a second init run does not duplicate data
    Given a database initialized with 1 team, 6 states, 4 labels and 1 member
    When I run "hifa init --db /tmp/h1/hifa.db" a second time
    Then the exit code is 0
    And stdout contains "already initialized"
    And the count of "team" is still 1
    And the count of "workflow_state" is still 6
    And the count of "label" is still 4

  @REQ-F003 @smoke
  Scenario: init rejects a file that is not a hifa database
    Given a file "/tmp/h1/otra.db" with plain text content
    When I run "hifa init --db /tmp/h1/otra.db"
    Then the exit code is 2
    And stderr contains the path "/tmp/h1/otra.db"
    And the file content did not change

  @REQ-F003 @smoke
  Scenario: init with --force replaces a foreign file
    Given a file "/tmp/h1/otra.db" that is not a hifa database
    When I run "hifa init --db /tmp/h1/otra.db --force"
    Then the exit code is 0
    And the table "schema_migration" has 3 rows
    And the user_version of the database is 3

  @REQ-F004 @smoke
  Scenario: the environment variable defines the path when there is no flag
    Given the HIFA_DB variable points to "/tmp/h2/env.db"
    And I do not pass the --db flag
    When I run "hifa db path --json"
    Then the "db_path" field is "/tmp/h2/env.db"
    And the "source" field is "env"

  @REQ-F004 @smoke
  Scenario: the --db flag wins over the environment variable
    Given the HIFA_DB variable points to "/tmp/h2/env.db"
    When I run "hifa db path --db /tmp/h2/flag.db --json"
    Then the "db_path" field is "/tmp/h2/flag.db"
    And the "source" field is "flag"
