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

  @REQ-F013 @REQ-F035 @slow
  Scenario: the page and the panel both carry the composer for a visible issue
    # the positive that a page rendering no composer fails: the box is not decoration, it is the
    # only way a comment is written from this edge (§2.6.10, D-060).
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a GET to "/ENG/issue/ENG-13" and then a GET to "/_/issues/ENG-13/panel" with the header "HX-Request: true"
    Then the first status is 200
    And the second status is 200
    And the first response contains "name=\"body\""
    And the second response contains "name=\"body\""
    And the first response contains "action=\"/ENG/issue/ENG-13/comment\""
    And the second response contains "/_/comments/ENG-13"

  @REQ-F013 @REQ-F035 @slow
  Scenario: a parked issue offers no composer
    Given the issue "ENG-13" in the trash
    When I execute a GET to "/ENG/issue/ENG-13" and then a GET to "/_/issues/ENG-13/panel" with the header "HX-Request: true"
    Then the first status is 200
    And the second status is 200
    And the first response contains "/ENG/issue/ENG-13/restore"
    And the second response contains "/ENG/issue/ENG-13/restore"
    And none of the answers contains "name=\"body\""

  @REQ-F013 @REQ-F035 @slow
  Scenario: an archived issue offers no composer either
    # the other half of the gate: a composer rendered only for a visible issue passes the trashed
    # scenario above and renders on the archived page, where the write is refused (§2.6.10, D-060).
    Given the issue "ENG-13" in the state "Todo" archived at "2026-11-10T12:00:00Z"
    When I execute a GET to "/ENG/issue/ENG-13" and then a GET to "/_/issues/ENG-13/panel" with the header "HX-Request: true"
    Then the first status is 200
    And the second status is 200
    And the first response contains "/ENG/issue/ENG-13/restore"
    And the second response contains "/ENG/issue/ENG-13/restore"
    And none of the answers contains "name=\"body\""

  @REQ-F013 @slow
  Scenario: a comment posted without JavaScript lands on the comments of the page
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a POST to "/ENG/issue/ENG-13/comment" with the form {"body":"First look at this"}
    Then the status is 303
    And the location is "/ENG/issue/ENG-13#comments"
    And the HTML of "/ENG/issue/ENG-13" contains "First look at this"

  @REQ-F013 @slow
  Scenario: the comment fragment answers the one list item it wrote
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a POST to "/_/comments/ENG-13" with the form {"body":"From the panel"} and the header "HX-Request: true"
    Then the status is 200
    And the HTML contains "<li class=\"comment\""
    And the HTML contains "From the panel"

  @REQ-F013 @slow
  Scenario: a comment with no body is refused and the box comes back
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a POST to "/ENG/issue/ENG-13/comment" with the form {"body":"   "}
    Then the status is 422
    And the HTML contains "a comment needs a body"
    And the HTML contains "name=\"body\""

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
  #   * an initialized database: team ENG (key ENG) with the seven seeded states of SPEC-100 §2.5, member
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

  # --- the write guard (HIFA-54; SPEC-070 §2.1.7, R28, D-057) ---------------------------------------
  #
  # The guard is a property of the caller, not of the instance (R18) and not of the payload
  # (`confirm=1`, §2.1.2): a state-changing method whose `Sec-Fetch-Site` says `cross-site` or
  # `same-site`, or whose `Origin` — read only when the fetch metadata is absent — names another
  # authority, is refused before the route is matched. A `When` that names neither header is the
  # declared non-browser client, which is what every other scenario of this file already is — these
  # eleven name it instead of relying on it.

  @REQ-S005 @REQ-F035
  Scenario: a cross-site write is refused and nothing is written
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a PATCH to "/_/issues/ENG-13?view=list" with the header "HX-Request: true" and the header "Origin: https://evil.example" and the header "Sec-Fetch-Site: cross-site" and the body {"title":"Fix SSO login"}
    Then the status is 403
    And the body mentions "cross-site write refused"
    And the title of "ENG-13" is "Fix login"
    And no row was added to "issue_history" for "ENG-13"

  @REQ-S005 @REQ-F035
  Scenario: a same-origin write is accepted
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a PATCH to "/_/issues/ENG-13?view=list" with the header "HX-Request: true" and the header "Sec-Fetch-Site: same-origin" and the body {"title":"Fix SSO login"}
    Then the status is 200
    And the title of "ENG-13" is "Fix SSO login"
    And there is exactly 1 row in "issue_history" for "ENG-13" with field "title"

  @REQ-S005 @REQ-F035
  Scenario: a same-site write is refused
    # One host and one port serve this edge, so no page of it can be `same-site` to another: the
    # value is necessarily a sibling origin, which is the case the guard exists to deny.
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a PATCH to "/_/issues/ENG-13?view=list" with the header "HX-Request: true" and the header "Origin: https://hifa.example" and the header "Sec-Fetch-Site: same-site" and the body {"title":"Fix SSO login"}
    Then the status is 403
    And the body mentions "cross-site write refused"
    And the title of "ENG-13" is "Fix login"
    And no row was added to "issue_history" for "ENG-13"

  @REQ-S005 @REQ-F035
  Scenario: a write from a client with no origin information is accepted
    # neither `Origin` nor `Sec-Fetch-Site`: the non-browser client of SPEC-070 §2.1.7, which is what
    # the test client, an agent and `curl` are. The absence is the contract, not a gap in the test.
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a PATCH to "/_/issues/ENG-13?view=list" with the header "HX-Request: true" and the body {"title":"Fix SSO login"}
    Then the status is 200
    And the title of "ENG-13" is "Fix SSO login"

  @REQ-S005 @REQ-F035
  Scenario: an Origin that names the served authority is accepted without fetch metadata
    # The fallback, and the tunnel case: the browser's `Origin` says `https` while the socket this
    # process accepts is plain `http` behind cloudflared. Only the authority is compared
    # (SPEC-070 §2.1.7), so the two agree and the write is served.
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a PATCH to "/_/issues/ENG-13?view=list" with the header "HX-Request: true" and the header "Origin: https://127.0.0.1:7777" and the body {"title":"Fix SSO login"}
    Then the status is 200
    And the title of "ENG-13" is "Fix SSO login"

  @REQ-S005 @REQ-F035
  Scenario: an Origin that names another authority is refused without fetch metadata
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a PATCH to "/_/issues/ENG-13?view=list" with the header "HX-Request: true" and the header "Origin: https://evil.example" and the body {"title":"Fix SSO login"}
    Then the status is 403
    And the body mentions "cross-site write refused"
    And the title of "ENG-13" is "Fix login"
    And no row was added to "issue_history" for "ENG-13"

  @REQ-S005 @REQ-F035
  Scenario: an opaque Origin is refused
    # `Origin: null` is what a sandboxed frame, a `file://` document and a redirect that stripped the
    # header send. It names no authority at all, so it is neither "another origin" nor absent, and
    # the guard refuses it: no page of this application produces that value.
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a PATCH to "/_/issues/ENG-13?view=list" with the header "HX-Request: true" and the header "Origin: null" and the body {"title":"Fix SSO login"}
    Then the status is 403
    And the body mentions "cross-site write refused"
    And the title of "ENG-13" is "Fix login"

  @REQ-S005 @REQ-F035
  Scenario: a cross-site read is still served
    Given 3 open issues in the database
    When I execute a GET to "/ENG/board" with the header "Origin: https://evil.example" and the header "Sec-Fetch-Site: cross-site"
    Then the status is 200
    And the HTML contains the 3 issues of the database
    And the HTML does not contain "cross-site write refused"

  @REQ-S005 @REQ-F035
  Scenario: a cross-site write to an unknown issue is a refusal and not a 404
    # deliberate order: the guard answers before the route, so the refusal cannot depend on the
    # identifier resolving — otherwise a 404 would prove the guard was not in the way.
    When I execute a POST to "/ENG/issue/ENG-999" with the header "Origin: https://evil.example" and the header "Sec-Fetch-Site: cross-site" and the form {"title":"ghost"}
    Then the status is 403
    And the body mentions "cross-site write refused"
    And no issue has the identifier "ENG-999"

  @REQ-S005 @REQ-F035
  Scenario: read-only mode keeps its own 403 for a same-origin write
    # The same write route as the accepted case, on a read-only instance: the guard lets it through
    # and the route answers with the instance's own refusal, whose text is not the guard's — the two
    # 403s of SPEC-070 §6 are told apart by their message.
    Given a server started with "--read-only" and the issue "ENG-12" not archived
    When I execute a PATCH to "/_/issues/ENG-12?view=list" with the header "HX-Request: true" and the header "Sec-Fetch-Site: same-origin" and the body {"title":"Hijacked"}
    Then the status is 403
    And the body mentions "read-only"
    And no row was added to "issue_history" for "ENG-12"

  @REQ-S005 @REQ-F035
  Scenario: a cross-site write on a read-only instance is the guard's refusal
    # The other side of the same order: the guard answers first, so a read-only instance answers a
    # cross-site write with the caller's refusal and never with `server in read-only mode`.
    Given a server started with "--read-only" and the issue "ENG-12" not archived
    When I execute a PATCH to "/_/issues/ENG-12?view=list" with the header "HX-Request: true" and the header "Origin: https://evil.example" and the header "Sec-Fetch-Site: cross-site" and the body {"title":"Hijacked"}
    Then the status is 403
    And the body mentions "cross-site write refused"
    And no row was added to "issue_history" for "ENG-12"

  # --- the trash's read surface, and the panel's move (D-059; SPEC-070 §2.1.1, §2.1.2, §2.6.4,
  # §2.6.9, §5.7, §5.7.1, R29) ----------------------------------------------------------------------
  #
  # Two routes read through the trash and nothing else does: the detail and the panel answer `200` for
  # an issue with `trashed = 1` and render the issue with `Restore` as its **only** control, and the
  # `row` and `card` fragments keep the read's default and answer `404` for exactly the identifier
  # whose page answers `200` (R29). Every `Then` here asserts the control that must be present *and*
  # the ones that must not, because an absence-only assertion passes on a page that renders nothing.
  # A trashed fixture is built the way the block above builds an archived one — a direct UPDATE, which
  # writes no `issue_history` row — so a row counted after a request is the request's doing.

  @REQ-F015 @REQ-F035
  Scenario: the detail page of a trashed issue is served with Restore as its only control
    # the four absences are the four controls of the action group (§2.6.9): the edit form, the move
    # `<select name="state_id">`, the archive block and the delete block. `Fix login` is the positive
    # half: the page renders the issue and not an empty shell with one button.
    Given the issue "ENG-13" in the state "Todo" with title "Fix login" in trash and not archived
    When I execute a GET to "/ENG/issue/ENG-13"
    Then the status is 200
    And the HTML contains "Fix login"
    And the HTML contains "/ENG/issue/ENG-13/restore"
    And the HTML does not contain "action=\"/ENG/issue/ENG-13\""
    And the HTML does not contain "name=\"state_id\""
    And the HTML does not contain "/ENG/issue/ENG-13/archive"
    And the HTML does not contain "/ENG/issue/ENG-13/delete"

  @REQ-F015 @REQ-F035
  Scenario: the panel of a trashed issue is served with Restore as its only control
    # the same four absences as the page, through the fragment route and the `/_/` handles the
    # panel footer renders (`hx-post` to the layer route, no `hx-target`: the success is a 204).
    Given the issue "ENG-13" in the state "Todo" with title "Fix login" in trash and not archived
    When I execute a GET to "/_/issues/ENG-13/panel" with the header "HX-Request: true"
    Then the status is 200
    And the HTML contains "<aside id=\"panel\""
    And the HTML contains "Fix login"
    And the HTML contains "/_/issues/ENG-13/restore"
    And the HTML does not contain "name=\"state_id\""
    And the HTML does not contain "/_/issues/ENG-13/archive"
    And the HTML does not contain "/_/issues/ENG-13/delete"
    And the HTML does not contain "<html>"

  @REQ-F015 @REQ-F035
  Scenario: the ULID alias of a trashed issue answers 303 to its canonical page
    # the alias keeps R6 and nothing else: an alias that answers 404 where its canonical URL answers
    # 200 is two URLs disagreeing about one row (D-059).
    Given the issue "ENG-13" in trash and not archived
    When I execute a GET to "/ENG/issue/<ULID of ENG-13>"
    Then the status is 303
    And the "Location" header is "/ENG/issue/ENG-13"

  @REQ-F015 @REQ-F035
  Scenario: a trashed issue is absent from the board, the list, the search page, the palette and the counters
    # one `When`, the five collection surfaces of §2.1.1. `ENG-80` is the visible neighbour: without it
    # a counter that counted nothing at all would satisfy the last assertion.
    Given the issue "ENG-13" in trash and not archived and the issue "ENG-80" in the state "Todo" visible and not archived
    When I execute a GET to "/ENG/board" and then a GET to "/ENG/list" and then a GET to "/search?q=ENG-13" and then a GET to "/_/palette?q=ENG-13" with the header "HX-Request: true" and then a GET to "/_/counts?team=ENG" with the header "HX-Request: true"
    Then none of the answers contains "ENG-13"
    And the counters count 1 issue for "ENG"

  @REQ-F015 @REQ-F035
  Scenario: the row and card fragments keep the trash out while the page serves it
    # the asymmetry R29 declares and the one an implementer gets wrong: the page and the panel pass
    # `include_trashed=True`, the collection members never do, so the same identifier is a 404 as a
    # member and a 200 as a page. The page's status is the positive half of that sentence: two `404`s
    # alone also pass on a tree where the detail answers `404` as well, which is the bug D-059 closed.
    Given the issue "ENG-13" in the state "Todo" with title "Fix login" in trash and not archived
    When I execute a GET to "/_/issues/ENG-13/row?view=list" with the header "HX-Request: true" and then a GET to "/_/issues/ENG-13/card" with the header "HX-Request: true" and then a GET to "/ENG/issue/ENG-13"
    Then the first status is 404
    And the second status is 404
    And the third status is 200
    And the third response contains "Fix login"

  @REQ-F015 @REQ-F035
  Scenario: a move naming no state of the team answers the catalogue's 404
    # the id is a real state of another team, which is the case §6 names: the reference resolves and
    # the resolution says this issue's team does not have it. Not a 422: there is no field to mark.
    Given the issue "ENG-13" in the state "Todo" visible and not archived and the team "OPS" with its state "Todo"
    When I execute a POST to "/_/issues/ENG-13/move" with the header "HX-Request: true" and the form {"state_id":"<id of Todo of OPS>"}
    Then the status is 404
    And the body mentions "workflow state does not exist"
    And the state of "ENG-13" is "Todo"
    And no row was added to "issue_history" for "ENG-13"

  @REQ-F015 @REQ-F035
  Scenario: a move from the panel footer answers 204 and hands the client the collection
    # the payload is the footer's: `state_id` alone, no neighbour, so the server reads it as
    # `position="last"` (§2.6.4). The client half — the panel closes and `page["refresh_url"]` is
    # re-read into `#issues-tbody`, focus on the row or the card of that collection (§5.7.1 steps 4-5)
    # — is browser-verified; what a test can pin is the one answer both clients of the route get.
    Given the issue "ENG-13" in the state "Todo" visible and not archived
    When I execute a POST to "/_/issues/ENG-13/move" with the header "HX-Request: true" and the form {"state_id":"<id of In Progress>"}
    Then the status is 204
    And the response body is empty
    And the response carries the header "HX-Trigger" with "issue-moved"
    And the response carries the header "HX-Revision" with the revision the request observed
    And the state of "ENG-13" is "In Progress"
    And "ENG-13" is the last issue of the column "In Progress"

  @REQ-F015 @REQ-F035
  Scenario: a move refused from the panel re-renders that panel and marks it for the reader
    # the footer's form swaps nothing (`hx-swap="none"`, because its success is a bodyless 204), so a
    # refusal has to name its own target or the marked panel is fetched and thrown away (§2.6.9).
    Given the issue "ENG-13" in the state "Todo" in trash and not archived
    When I execute a POST to "/_/issues/ENG-13/move" with the header "HX-Request: true" and the form {"state_id":"<id of In Progress>"}
    Then the status is 422
    And the HTML contains "<aside id=\"panel\""
    And the HTML contains "ENG-13 is in the trash; restore it first"
    And the response carries the header "HX-Retarget" with "#panel"
    And the response carries the header "HX-Reswap" with "innerHTML"
    And the state of "ENG-13" is "Todo"

  @REQ-F015 @REQ-F035
  Scenario: a delete of an issue already in the trash is a no-op that still answers
    # `trash_issue` returns the stored row unchanged and reads no clock (§5.7 step 7), so both writes
    # of the layer land on the same 204 — with the timestamp and the layer exactly where they were.
    Given the issue "ENG-12" in the state "In Progress" in trash and not archived and updated at "2026-11-10T12:00:00Z"
    When I execute a POST to "/_/issues/ENG-12/delete" with the header "HX-Request: true" and the form {"confirm":"1"}
    Then the status is 204
    And the response body is empty
    And the response carries the header "HX-Trigger" with "issue-deleted"
    And no row was added to "issue_history" for "ENG-12"
    And the "issue" row with identifier "ENG-12" has trashed 1
    And the updated_at of "ENG-12" is "2026-11-10T12:00:00Z"

  @REQ-F015 @REQ-F035
  Scenario: the delete confirmation block renders its own three strings and its refusal
    # the block is the archive one with the verb and the message replaced (§2.6.9): the summary and the
    # button read `Delete`, the line and the label name the trash, and the 422 re-renders the block
    # `open` with the catalogue's `form_error` as its message.
    Given the issue "ENG-13" in the state "Todo" visible and not archived
    When I execute a POST to "/ENG/issue/ENG-13/delete" with the form {}
    Then the status is 422
    And the HTML contains "<details class=\"confirm\" open>"
    And the HTML contains "<summary class=\"btn\">Delete</summary>"
    And the HTML contains "This sends ENG-13 to the trash. Restore brings it back."
    And the HTML contains "Yes, delete ENG-13"
    And the HTML contains "btn btn-danger\">Delete</button>"
    And the HTML contains "confirm the delete of ENG-13"
    And the HTML contains "is-invalid"
    And the "issue" row with identifier "ENG-13" has trashed 0
    And no row was added to "issue_history" for "ENG-13"

  @REQ-F015 @REQ-F035
  Scenario: an archive sends the reader to the list and leaves the detail reachable
    # the split reason of §2.1.2: archive and delete both answer 303 to the list because the issue has
    # just left every listing, and after an archive the detail still answers 200 — which is where the
    # no-JS reader undoes the write with the group's single control.
    Given the issue "ENG-12" in the state "In Progress" visible and not archived
    When I execute a POST to "/ENG/issue/ENG-12/archive" with the form {"confirm":"1"} and then a GET to "/ENG/issue/ENG-12"
    Then the first status is 303
    And the "Location" header is "/ENG/list"
    And the second status is 200
    And the second response contains "/ENG/issue/ENG-12/restore"
    And the second response does not contain "name=\"state_id\""

  @REQ-F013 @REQ-F015 @REQ-F035
  Scenario: a visible issue's page and panel carry the four controls of the action group
    # the positive the two trashed scenarios above are the absence of (`vm.can_write` is the group's
    # one gate, and the layer picks the branch inside it): a visible issue renders the four controls on
    # the page and on the panel, and no `Restore`, which a trashed issue alone offers (§2.6.9, D-059).
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a GET to "/ENG/issue/ENG-13" and then a GET to "/_/issues/ENG-13/panel" with the header "HX-Request: true"
    Then the first status is 200
    And the second status is 200
    And the first response contains "name=\"state_id\""
    And the second response contains "name=\"state_id\""
    And the first response contains "/ENG/issue/ENG-13/archive"
    And the second response contains "/ENG/issue/ENG-13/archive"
    And the first response contains "/ENG/issue/ENG-13/delete"
    And the second response contains "/ENG/issue/ENG-13/delete"
    And the first response contains "action=\"/ENG/issue/ENG-13\""
    And the second response contains "action=\"/ENG/issue/ENG-13\""
    And none of the answers contains "/ENG/issue/ENG-13/restore"
