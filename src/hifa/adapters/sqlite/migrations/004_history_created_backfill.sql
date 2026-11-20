-- A data migration: no CREATE, no ALTER, no DROP. `create_issue` used to echo the issue's own
-- identifier into `new_value` of its `created` row. The row records an event and the event is
-- "the issue was created", so the value is NULL and the timeline says so. One scan of
-- `issue_history`; a second run matches no row.
UPDATE issue_history
   SET new_value = NULL
 WHERE field = 'created' AND new_value IS NOT NULL;
