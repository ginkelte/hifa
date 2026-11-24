# Intent: one field, in place

## Problem
Correcting one wrong detail means filling an edit form and saving every field: a one-field fix costs a whole form and writes back stale values.

## User
The person who keeps the tracker on their own machine: a correction becomes one gesture at the field, writing nothing else.

## Alternatives not chosen
- A faster whole-form save: the form is the cost, not its speed.
- A modal per field: still a separate form, one per field.

## Failure test
A week in, a due date is still corrected by opening the edit form and pressing Save.

## Risks, by cost of being wrong
- An edit only the dashboard can do. P1/P2 make that a bug, and the request names only the detail page and the panel. It holds only while each field maps to an `hifa issue edit` flag.
- An in-place save that resends every field: a stale value overwrites a concurrent change, silently.
- "Every field" collapsing to the seven on the form; references (project, cycle, parent) surface late.
- A refused value with no message at the field: the reader learns nothing.
- Reading "no separate form" as "delete the form": that deletes the no-JS path (REQ-N006, P5).

## Non-goals
- Labels: `issue label` / `unlabel` own them; no label field here.
- Comments, bulk edit, list rows, board cards.
- Any capability the CLI lacks.

## Done, as an observation
Changing one field and leaving it writes that field alone, history names it, and a reader without JavaScript still edits it from a form.
