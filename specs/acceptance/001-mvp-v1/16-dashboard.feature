@SPEC-001
Feature: HTTP dashboard

  # Single-process local server (port 7777, SPEC-040 §2 / D-012) with server-rendered
  # views under /{team}/…, htmx fragments under /_/… and JSON API under /api/v1.
  # The schema is user_version = 3 (migrations 001/002/003 from SPEC-010).

  @REQ-F033 @smoke @slow
  Scenario: serve listens on loopback and on the default port
    Given an initialized database
    When I run "hifa serve --no-open" in background
    Then the process responds at "http://127.0.0.1:7777/healthz" with status 200
    And the "schema_version" field of the response is 3
    And there is a single process listening on port 7777

  @REQ-F033 @smoke @slow
  Scenario: serve prints the effective URL
    When I run "hifa serve --port 8500 --no-open" in background
    Then stdout contains "http://127.0.0.1:8500"
    And the "port" field of the startup JSON is 8500

  @REQ-F034 @smoke @slow
  Scenario: GET /api/v1/issues returns the envelope with a maximum limit of 200
    Given 250 issues in the database
    When I execute a GET to "/api/v1/issues?limit=200" and then a GET to "/api/v1/issues?limit=500"
    Then the first status is 200
    And the "items" field of the first response has 200 elements
    And the "has_more" field of the first response is true
    And the second status is 422

  @REQ-F034 @smoke @slow
  Scenario: a nonexistent identifier returns 404 with the error envelope
    When I execute a GET to "/api/v1/issues/ENG-999"
    Then the status is 404
    And the "error.code" field is "not_found"
    And the "error.field" field is "ref"

  @REQ-F034 @smoke @slow
  Scenario: POST /api/v1/issues creates the issue and responds 201
    When I execute a POST to "/api/v1/issues" with the body {"team":"ENG","title":"From the API"}
    Then the status is 201
    And the response includes the "Location" header
    And the "identifier" field starts with "ENG-"

  @REQ-F034 @smoke @slow
  Scenario: the OpenAPI contract is generated from the models
    When I execute a GET to "/openapi.json"
    Then the status is 200
    And the document defines the schema "IssueCreate"
    And the document defines the path "/api/v1/issues"

  @REQ-F035 @smoke @slow
  Scenario: the list view renders the rows on the server
    Given 3 open issues in the database
    When I execute a GET to "/ENG/list"
    Then the status is 200
    And the HTML contains the 3 issues of the database
    And the HTML does not contain the string "Loading"

  @REQ-F035 @smoke @slow
  Scenario: the htmx rows fragment is returned without the full layout
    When I execute a GET to "/_/issues?view=list" with the header "HX-Request: true"
    Then the status is 200
    And the HTML does not contain the "<html>" tag
    And the HTML contains "<tr" elements

  @REQ-F035 @smoke @slow
  Scenario: the detail view shows the sanitized description and comments
    Given the issue "ENG-80" with description "**bold**" and 1 comment
    When I execute a GET to "/ENG/issue/ENG-80"
    Then the status is 200
    And the HTML contains the "<strong>" tag
    And the HTML contains the body of the comment

  @REQ-F036 @smoke @slow
  Scenario: the palette returns at most 10 mixed results
    Given 30 issues with the word "log" in the title
    When I execute a GET to "/_/palette?q=log" with the header "HX-Request: true"
    Then the status is 200
    And the fragment has 10 results
    And each result declares a kind among issue, team, label or command

  @REQ-F037 @smoke @slow
  Scenario: doctor reports schema, integrity and counts
    Given an initialized database with 4 issues
    When I run "hifa doctor --json"
    Then the "schema_version" field is 3
    And the "quick_check" field is "ok"
    And the "counts.issue" field is 4
    And the exit code is 0

  @REQ-F037 @smoke @slow
  Scenario: doctor detects the out-of-sync FTS index and --fix-fts repairs it
    Given rows were deleted from "issue_fts" without touching "issue"
    When I run "hifa doctor --json" and then "hifa doctor --fix-fts --json"
    Then the first report mentions "fts" in "findings"
    And the count of "issue_fts" ends up equal to the one of "issue"

  # --- the dashboard write paths (T-20.1; SPEC-070 §2.1.2, §2.1.3, §2.6.9, §5.7; D-053, D-054) ------
  #
  # Fixtures this block assumes, declared once instead of in every scenario:
  #   * an initialized database: team ENG (key ENG) with the 6 seeded states of SPEC-100 §2.5, member
  #     Ginkelte as the viewer, and every issue a scenario names, created with `hifa issue create`;
  #   * `<id of STATE>` is the ULID the fixture holds for that workflow state, and it is what a move
  #     payload carries (`state_id`), never a name and never a position;
  #   * a layer (`archived_at`, `trashed`) is put in place by the fixture with a direct UPDATE, never
  #     through a use case: a fixture-applied layer writes no `issue_history` row, so the row a scenario
  #     counts is the one the request under test wrote. A `Given` that names a layer states the other
  #     one too wherever the `Then` depends on it ("in trash and not archived", "visible and not
  #     archived"), so a null in a `Then` is the request's doing and not an unstated fixture default;
  #   * comments cannot be seeded by a verb today: `hifa issue comment` is published by SPEC-050 §2.5.3
  #     but is unimplemented in the Typer tree (P1), so the fixture inserts the `comment` rows directly,
  #     and "delete with the confirmation trashes the issue and keeps its comments" only asserts that
  #     the count survives the delete — it never creates a comment through the API;
  #   * in a `When I execute a POST to … with the form {…}` step the braces are the
  #     `application/x-www-form-urlencoded` body; `with the body {…}` stays the JSON body (PATCH);
  #   * `the "HX-Request: true" header` is the fragment guard of R5, present on every `/_/*` call.

  @REQ-F013 @REQ-F035
  Scenario: the edit form saves the field and redirects to the detail
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a POST to "/ENG/issue/ENG-13" with the form {"title":"Fix SSO login"}
    Then the status is 303
    And the "Location" header is "/ENG/issue/ENG-13"
    And the title of "ENG-13" is "Fix SSO login"
    And there is exactly 1 row in "issue_history" for "ENG-13" with field "title"

  @REQ-F013 @REQ-F035
  Scenario: an edit that changes nothing writes no history row
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a POST to "/ENG/issue/ENG-13" with the form {"title":"Fix login"}
    Then the status is 303
    And no row was added to "issue_history" for "ENG-13"

  @REQ-F013 @REQ-F035
  Scenario: the inline edit answers the row it changed
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a PATCH to "/_/issues/ENG-13?view=list" with the header "HX-Request: true" and the body {"title":"Fix SSO login"}
    Then the status is 200
    And the HTML contains "id=\"issue-ENG-13\""
    And the HTML contains "Fix SSO login"
    And the HTML does not contain "<html>"
    And the response carries the header "HX-Trigger" with "issue-updated"

  @REQ-F013 @REQ-F035
  Scenario: the panel edit answers the panel
    Given the issue "ENG-13" in the state "Todo" with description "first draft"
    When I execute a PATCH to "/_/issues/ENG-13?view=panel" with the header "HX-Request: true" and the body {"description":"**final** draft"}
    Then the status is 200
    And the HTML contains "<aside id=\"panel\""
    And the HTML contains "<strong>final</strong>"
    And the HTML does not contain "<html>"

  @REQ-F013 @REQ-F035
  Scenario: an empty title is refused and nothing is written
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a PATCH to "/_/issues/ENG-13?view=list" with the header "HX-Request: true" and the body {"title":""}
    Then the status is 422
    And the HTML contains "is-invalid"
    And the title of "ENG-13" is "Fix login"
    And no row was added to "issue_history" for "ENG-13"

  @REQ-F013 @REQ-F035
  Scenario: an edit of an unknown identifier is 404 and creates nothing
    When I execute a POST to "/ENG/issue/ENG-999" with the form {"title":"ghost"}
    Then the status is 404
    And no issue has the identifier "ENG-999"

  @REQ-F015 @REQ-F035
  Scenario: the move form appends the issue to the target column
    Given the issue "ENG-12" in the state "In Progress" and the issue "ENG-13" in the state "Todo"
    When I execute a POST to "/ENG/issue/ENG-13/move" with the form {"state_id":"<id of In Progress>"}
    Then the status is 303
    And the "Location" header is "/ENG/issue/ENG-13"
    And the state of "ENG-13" is "In Progress"
    And "ENG-13" is the last issue of the column "In Progress"

  @REQ-F015 @REQ-F035
  Scenario: the drop answers 204 with no body and lands between its neighbours
    Given the issues "ENG-12", "ENG-13" and "ENG-15" in the state "In Progress" in that order
    # the fixture order is "ENG-12", "ENG-13", "ENG-15"; the issue moved is ENG-15, which is neither
    # where the fixture put it nor where the answer must leave it
    When I execute a POST to "/_/issues/ENG-15/move" with the header "HX-Request: true" and the body {"state_id":"<id of In Progress>","before":"ENG-12","after":"ENG-13"}
    Then the status is 204
    And the response body is empty
    And the response carries the header "HX-Trigger" with "issue-moved"
    And the column "In Progress" reads "ENG-12", "ENG-15", "ENG-13"

  @REQ-F015 @REQ-F035
  Scenario: a move of an archived issue is refused and changes nothing
    Given the issue "ENG-13" in the state "Todo" archived at "2026-11-10T12:00:00Z"
    When I execute a POST to "/ENG/issue/ENG-13/move" with the form {"state_id":"<id of In Progress>"}
    Then the status is 409
    And the HTML contains "ENG-13 is archived; restore it first"
    And the state of "ENG-13" is "Todo"

  @REQ-F015 @REQ-F035
  Scenario: archive without the confirmation is refused
    Given the issue "ENG-12" in the state "In Progress" not archived
    When I execute a POST to "/ENG/issue/ENG-12/archive" with the form {}
    Then the status is 422
    And the HTML contains "confirmation_required"
    And the HTML contains "name=\"confirm\""
    And the HTML contains "<details class=\"confirm\" open>"
    And the HTML contains "is-invalid"
    And the archived_at of "ENG-12" is null
    And no row was added to "issue_history" for "ENG-12"

  @REQ-F015 @REQ-F035
  Scenario: archive with the confirmation takes the issue out of the list and keeps its state
    Given the issue "ENG-12" in the state "In Progress" not archived
    When I execute a POST to "/ENG/issue/ENG-12/archive" with the form {"confirm":"1"}
    Then the status is 303
    And the "Location" header is "/ENG/list"
    And the archived_at of "ENG-12" is not null
    And the state of "ENG-12" is "In Progress"
    And there is exactly 1 row in "issue_history" for "ENG-12" with field "archived_at"

  @REQ-F015 @REQ-F035
  Scenario: an archive of an already-archived issue is a no-op
    # layers: archived_at set, trashed 0. The fixture applies the layer with a direct UPDATE and writes
    # no `issue_history` row, so a row counted after the request is the request's doing and not a
    # default of the fixture — and there must be none.
    Given the issue "ENG-12" in the state "In Progress" archived at "2026-11-10T12:00:00Z"
    When I execute a POST to "/_/issues/ENG-12/archive" with the header "HX-Request: true" and the form {"confirm":"1"}
    Then the status is 204
    And the response body is empty
    And the response carries the header "HX-Trigger" with "issue-archived"
    And no row was added to "issue_history" for "ENG-12"
    And the archived_at of "ENG-12" is "2026-11-10T12:00:00Z"

  @REQ-F015 @REQ-F035
  Scenario: a parent with open sub-issues is not archived from the dashboard
    Given the issue "ENG-12" with two open sub-issues
    When I execute a POST to "/_/issues/ENG-12/archive" with the header "HX-Request: true" and the form {"confirm":"1"}
    Then the status is 422
    And the HTML contains "cannot archive ENG-12: it has 2 open sub-issues"
    And the archived_at of "ENG-12" is null
    And no row was added to "issue_history" for "ENG-12"

  @REQ-F015 @REQ-F035
  Scenario: an issue in the trash is not archived
    Given the issue "ENG-12" in trash and not archived
    When I execute a POST to "/_/issues/ENG-12/archive" with the header "HX-Request: true" and the form {"confirm":"1"}
    Then the status is 422
    And the HTML contains "ENG-12 is in the trash; restore it first"
    And the archived_at of "ENG-12" is null

  @REQ-F015 @REQ-F035
  Scenario: delete without the confirmation is refused
    Given the issue "ENG-11" visible and not archived
    When I execute a POST to "/ENG/issue/ENG-11/delete" with the form {}
    Then the status is 422
    And the HTML contains "confirm the delete of ENG-11"
    And the HTML contains "name=\"confirm\""
    And the HTML contains "<details class=\"confirm\" open>"
    And the HTML contains "is-invalid"
    And the "issue" row with identifier "ENG-11" has trashed 0
    And no row was added to "issue_history" for "ENG-11"

  @REQ-F015 @REQ-F035
  Scenario: delete with the confirmation trashes the issue and keeps its comments
    Given the issue "ENG-11" with 2 comments
    When I execute a POST to "/ENG/issue/ENG-11/delete" with the form {"confirm":"1"}
    Then the status is 303
    And the "Location" header is "/ENG/list"
    And the "issue" row with identifier "ENG-11" has trashed 1
    And the count of "comment" for that issue is 2

  @REQ-F015 @REQ-F035
  Scenario: an archived issue can be deleted
    Given the issue "ENG-12" archived at "2026-11-10T12:00:00Z"
    When I execute a POST to "/ENG/issue/ENG-12/delete" with the form {"confirm":"1"}
    Then the status is 303
    And the "Location" header is "/ENG/list"
    And the "issue" row with identifier "ENG-12" has trashed 1
    And the archived_at of "ENG-12" is "2026-11-10T12:00:00Z"

  @REQ-F015 @REQ-F035
  Scenario: restore clears both layers and writes one history row each
    Given the issue "ENG-12" archived at "2026-11-10T12:00:00Z" and in trash
    When I execute a POST to "/ENG/issue/ENG-12/restore" with the form {}
    Then the status is 303
    And the "Location" header is "/ENG/issue/ENG-12"
    And the archived_at of "ENG-12" is null
    And the "issue" row with identifier "ENG-12" has trashed 0
    And there is exactly 1 row in "issue_history" for "ENG-12" with field "archived_at"
    And there is exactly 1 row in "issue_history" for "ENG-12" with field "trashed"

  @REQ-F015 @REQ-F035
  Scenario: restore of a visible issue is a no-op that still answers
    Given the issue "ENG-12" visible and not archived
    When I execute a POST to "/_/issues/ENG-12/restore" with the header "HX-Request: true" and the form {}
    Then the status is 204
    And no row was added to "issue_history" for "ENG-12"

  @REQ-F015 @REQ-F035
  Scenario: the fragment restore of an archived issue answers 204 and clears the layer
    # layers: archived_at set, trashed 0; the fixture applies them directly and writes no history row,
    # so the one `archived_at` row counted after the request is this restore's. The client half — the
    # panel closes and the collection is re-read from `refresh_url` (§5.7 step 6) — is browser-verified.
    Given the issue "ENG-12" in the state "In Progress" archived at "2026-11-10T12:00:00Z"
    When I execute a POST to "/_/issues/ENG-12/restore" with the header "HX-Request: true" and the form {}
    Then the status is 204
    And the response body is empty
    And the response carries the header "HX-Trigger" with "issue-restored"
    And the archived_at of "ENG-12" is null
    And there is exactly 1 row in "issue_history" for "ENG-12" with field "archived_at"

  @REQ-F015 @REQ-F035
  Scenario: the panel of an archived issue offers Restore and no other write
    Given the issue "ENG-12" archived at "2026-11-10T12:00:00Z"
    When I execute a GET to "/_/issues/ENG-12/panel" with the header "HX-Request: true"
    Then the status is 200
    And the HTML contains "/ENG/issue/ENG-12/restore"
    And the HTML does not contain "/ENG/issue/ENG-12/delete"
    And the HTML does not contain "/ENG/issue/ENG-12/archive"

  @REQ-F015 @REQ-F035
  Scenario: a write in read-only mode is forbidden
    Given a server started with "--read-only" and the issue "ENG-12" not archived
    When I execute a POST to "/ENG/issue/ENG-12/archive" with the form {"confirm":"1"}
    Then the status is 403
    And the HTTP status of a POST to "/_/issues/ENG-12/delete" with the header "HX-Request: true" and the form {"confirm":"1"} is 403
    And the archived_at of "ENG-12" is null
