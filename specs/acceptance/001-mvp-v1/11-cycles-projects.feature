@SPEC-001
Feature: Cycles, projects and milestones

  # Cycles numbered per team with validated ranges; projects with progress by type.
  # SPEC-050 §2.5 does not publish milestone verbs nor a cycle overlap flag:
  # those steps are expressed at the domain level (see Open Questions of SPEC-001 §10.1).

  @REQ-F026 @smoke
  Scenario: cycles are numbered consecutively per team
    Given the team "ENG" with no cycles
    When I run "hifa cycle create --team ENG --starts 2026-11-02 --ends 2026-11-15" and then "hifa cycle create --team ENG --starts 2026-11-16 --ends 2026-11-29"
    Then the numbers of the cycles are 1 and 2

  @REQ-F026 @smoke
  Scenario: a cycle ending before it starts is rejected
    When I run "hifa cycle create --team ENG --starts 2026-11-15 --ends 2026-11-02"
    Then the exit code is 2
    And the error.code is "validation_error"
    And the count of "cycle" did not change

  @REQ-F026 @smoke
  Scenario: an overlapping cycle is rejected without explicit authorization
    Given cycle 1 of the team "ENG" from 2026-11-02 to 2026-11-15
    When I run "hifa cycle create --team ENG --starts 2026-11-10 --ends 2026-11-20"
    Then the exit code is 4
    And the error.code is "conflict"
    And the error message names the conflicting cycle 1
    And the count of "cycle" did not change

  @REQ-F026 @smoke
  Scenario: an authorized overlapping cycle is written and doctor reports it
    Given cycle 1 of the team "ENG" from 2026-11-02 to 2026-11-15
    When the cycle from 2026-11-10 to 2026-11-20 is created in "ENG" authorizing the overlap
    Then the exit code is 0
    And there is 1 new row in "cycle"
    And the report of "hifa doctor" includes an overlapping cycle finding

  @REQ-F027 @smoke
  Scenario: project progress is computed by state type
    Given a project with 4 issues: 2 completed, 1 started and 1 unstarted
    When I run "hifa project show $PROJECT_ID --json"
    Then the "progress" field is 50

  @REQ-F027 @smoke
  Scenario: a project with no issues reports progress 0
    When I run "hifa project show $EMPTY_PROJECT --json"
    Then the "progress" field is 0

  @REQ-F027 @smoke
  Scenario: milestones accept the four states
    Given an existing project
    When the milestones "Beta" with status "next" and "Gamma" with status "done" are added to the project $PROJECT_ID
    Then there are 2 rows in "project_milestone"
    And one has status "next" and the other status "done"
