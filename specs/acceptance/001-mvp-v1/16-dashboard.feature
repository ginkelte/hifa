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
