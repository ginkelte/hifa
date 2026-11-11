@SPEC-001
Feature: Dashboard hardening

  # Path traversal, local bind, sanitization and htmx guard.
  # There is no CSRF token nor SameSite cookie (SPEC-000 REQ-S005): the only write
  # guard is the `HX-Request` header, and without it the fragment responds 400.

  @REQ-S001 @smoke
  Scenario Outline: a path escaping the static root returns 404
    When I execute a GET to "<path>"
    Then the status is 404
    Examples:
      | path                        |
      | /static/../hifa.db          |
      | /static/%2e%2e/hifa.db      |
      | /static/../../etc/passwd    |

  @REQ-S003 @smoke
  Scenario: a host other than loopback requires the explicit flag and warns
    When I run "hifa serve --host 0.0.0.0 --no-open" in background
    Then the exit code is 0
    And stderr contains "without authentication"
    And the process listens on "0.0.0.0:7777"

  @REQ-S003 @smoke
  Scenario: the server does not emit CORS headers
    When I execute a GET to "/healthz" with the header "Origin: http://evil.test"
    Then the response has no "Access-Control-Allow-Origin" header

  @REQ-S004 @smoke
  Scenario: markdown with script is sanitized before rendering
    Given the issue "ENG-90" with description "<script>alert(1)</script> and <img src=x onerror=alert(1)>"
    When I execute a GET to "/ENG/issue/ENG-90"
    Then the HTML does not contain "<script"
    And the HTML does not contain "onerror"
    And the HTML does not contain "javascript:"

  @REQ-S005 @smoke
  Scenario: an htmx write without the HX-Request header returns 400
    When I execute a PATCH to "/_/issues/ENG-1?view=list" without the "HX-Request" header
    Then the status is 400
    And the body mentions "fragment requires htmx"
