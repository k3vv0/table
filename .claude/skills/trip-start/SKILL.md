---
name: trip-start
description: Suggest a trip on the Table for each date with travel in its notes (Thanksgiving, Christmas, graduations) that is 45 days out or less and has no trip yet. Use in the morning routine before trip-options.
---

# Trip start

Kev and Beth plan most travel around a few dates: Thanksgiving with Kev's family, Christmas
with Beth's family in Georgia, the 2027 graduations. A trip should exist well before the
day, so standby options have time to show up. Your job is to **suggest** that trip; a
person accepts it on the board (DESIGN.md §13). You never write trips yourself.

All calls go through `python scripts/agent_api.py METHOD PATH ['JSON']`.

## 1. Gather

```
python scripts/agent_api.py GET /api/table/dates
python scripts/agent_api.py GET /api/table/trips
python scripts/agent_api.py GET /api/table/places
python scripts/agent_api.py GET /api/table/suggestions
```

Keep Table dates whose `notes` or title mention travel (travel, trip, fly, flight, visit,
or a place they'd travel to) and whose `days_away` is 45 or less. Skip one if a trip
already has its `key_date_id`, or a pending `trip` suggestion has the same title.

## 2. Shape the trip

- **title**: plain, with the place: "Thanksgiving in San Marcos", "Christmas in Atlanta",
  "Isaac's graduation in Evanston".
- **window**: in words, from what's known. They travel on weekends when they can (Kev
  avoids taking days off to travel), so a holiday trip usually runs from the weekend
  before to the weekend after. Say it as a window ("Sat Nov 21 to Sun Nov 29, roughly"),
  and only set `starts_on`/`ends_on` when the notes give firm days.
- **places**: in order, e.g. "Austin, San Marcos". Use names from `/places` when they fit.
- **travelers**: `["kev", "elizabeth"]` unless the notes say one of them.
- **notes**: what the date's notes say about the plan, in a sentence or two.
- **status**: `"planning"`.

## 3. File it

```
python scripts/agent_api.py POST /api/table/suggestions '{
  "kind": "trip",
  "payload": {"title": "Christmas in Atlanta", "window": "Sat Dec 19 to Sun Dec 27, roughly",
              "places": "Atlanta", "travelers": ["kev", "elizabeth"], "status": "planning",
              "key_date_id": <date id>, "notes": "Gifts for Beth's mom, dad and Isaac."},
  "rationale": "Christmas is 45 days out and its notes say travel to Beth's family in Georgia.",
  "run_id": <run id>
}'
```

A 409 means it's already there or suggested; move on. At most 3 a run.
