@SPEC-001
Feature: Comments and attachments

  # Comments with threads (parent_id) and attachments as links with metadata.
  # There are no `comment *` verbs in SPEC-050 §2.5: resolving a comment
  # is expressed at the domain level until 050 publishes the verb (Open Questions).

  @REQ-F022 @smoke
  Scenario: comment with a thread through parent_id
    Given the issue "ENG-30" with no comments
    When I run "hifa issue comment ENG-30 'Reviewing this'" and then "hifa issue comment ENG-30 'Reply' --parent $LAST_COMMENT"
    Then the exit code is 0
    And there are 2 rows in "comment" for "ENG-30"
    And the second comment has parent_id equal to the id of the first one

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
