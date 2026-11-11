@SPEC-001
Feature: Search

  # FTS5 with bm25, filtered by visibility at query time. The envelope is the one of
  # SPEC-090 §2: {hits, total, has_more, backend, took_ms}. The only corrective action
  # on the index is `hifa db reindex` (D-003).

  @REQ-F025 @smoke
  Scenario: search finds by title and returns a snippet
    Given the issue "ENG-50" with title "Broken login on Safari"
    When I run "hifa search 'login' --json"
    Then the "total" field is 1
    And the "backend" field is "fts"
    And the result includes a snippet field with the word "login"

  @REQ-F025 @smoke
  Scenario: an issue in trash does not appear in search
    Given the issue "ENG-50" with title "Broken login on Safari"
    When I run "hifa issue delete ENG-50 --yes" and then "hifa search 'login' --json"
    Then the "total" field is 0
    And the "hits" field is empty

  @REQ-F025 @smoke
  Scenario: trash and restore keep the result without reindexing
    Given the issue "ENG-50" in trash with title "Broken login on Safari"
    When I run "hifa issue restore ENG-50" and then "hifa search 'login' --json"
    Then the "total" field is 1
    And no reindexing was run

  @REQ-F025 @smoke
  Scenario: db reindex rebuilds the FTS index
    Given the "issue_fts" index was emptied manually
    When I run "hifa db reindex --json"
    Then the "reindexed" field is greater than or equal to 1
    And the count of "issue_fts" equals the count of "issue"
    And searching an existing title returns 1 result

  @REQ-S002 @smoke
  Scenario Outline: a hostile FTS query is treated as literal text
    Given the issue "ENG-51" with title "Broken login"
    When I run "hifa search <query> --json"
    Then the exit code is 0
    And the "total" field is 0
    And no issue is returned
    Examples:
      | query         |
      | "*" OR 1=1    |
      | NEAR(login)   |
      | -login        |
      | title:eng     |
