@SPEC-001
Feature: Comments and attachments

  # Comments with threads (parent_id) and attachments as links with metadata.
  # SPEC-050 §2.5 publishes `hifa issue comment REF [BODY]` and nothing else under `comment`;
  # resolving or editing a comment is still expressed at the domain level until a verb lands
  # (Open Questions 001 §10.1).

  @REQ-F022 @smoke
  Scenario: comment with a thread through parent_id
    Given the issue "ENG-30" with no comments
    When I run "hifa issue comment ENG-30 'Reviewing this'" and then "hifa issue comment ENG-30 'Reply' --parent $LAST_COMMENT"
    Then the exit code is 0
    And there are 2 rows in "comment" for "ENG-30"
    And the second comment has parent_id equal to the id of the first one

  @REQ-F022 @slow
  Scenario: the third reply is the deepest one that lands
    Given the issue "ENG-30" with a comment thread two deep
    When I run "hifa issue comment ENG-30 'Third level' --parent $LAST_COMMENT"
    Then the exit code is 0
    And there are 3 rows in "comment" for "ENG-30"

  @REQ-F022 @slow
  Scenario: a reply deeper than three is refused and nothing is written
    Given the issue "ENG-30" with a comment thread three deep
    When I run "hifa issue comment ENG-30 'Too deep' --parent $LAST_COMMENT"
    Then the exit code is 2
    And stderr contains "the thread is 3 deep; reply to a shallower comment"
    And stderr contains "field: parent_id"
    And there are 3 rows in "comment" for "ENG-30"

  @REQ-F022 @slow
  Scenario: an empty body is refused before anything is written
    Given the issue "ENG-30" with no comments
    When I run "hifa issue comment ENG-30 '   '"
    Then the exit code is 2
    And stderr contains "a comment needs a body"
    And there are 0 rows in "comment" for "ENG-30"

  @REQ-F022 @slow
  Scenario: a parked issue takes no comment
    Given the issue "ENG-30" in the trash
    When I run "hifa issue comment ENG-30 'Anyone there?'"
    Then the exit code is 2
    And stderr contains "ENG-30 is in the trash; restore it first"
    And there are 0 rows in "comment" for "ENG-30"

  @REQ-F022 @smoke
  Scenario: resolving a comment hides it from the default listing
    Given the issue "ENG-30" with 2 comments
    When the first comment of "ENG-30" is resolved and its comments are listed
    Then the default listing returns 1 comment
    And the listing including resolved ones returns 2

  @REQ-F022 @smoke
  Scenario: editing a comment sets edited_at and keeps created_at
    Given a comment with created_at "2026-11-01T10:00:00.000Z"
    When that comment is edited with the text "Corrected text"
    Then the edited_at field is not null
    And the created_at field is still "2026-11-01T10:00:00.000Z"

  @REQ-F023 @smoke
  Scenario: attachment stores url and metadata without copying files
    Given the issue "ENG-31"
    When I run "hifa issue attach ENG-31 --title Screenshot --url file:///home/ana/captura.png --source-type file"
    Then there is 1 row in "attachment" for "ENG-31"
    And the source_type field of the attachment is "file"
    And the url field of the attachment is "file:///home/ana/captura.png"
    And no file was copied to the database directory

  @REQ-F023 @smoke
  Scenario: a file:// attachment outside HOME is rejected
    When I run "hifa issue attach ENG-31 --title A --url file:///etc/passwd --source-type file"
    Then the exit code is 2
    And the error.code is "validation_error"
    And the count of "attachment" did not change
