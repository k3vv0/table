---
name: dates-interview
description: Interview Kev (or Beth) about birthdays, anniversaries, holidays and one-off dates like graduations, then file each one as a date suggestion they accept on Table's Dates view. Use when someone asks to set up, review or add people and dates in Table.
---

# Dates interview

Table remembers key dates so nobody has to (DESIGN.md §11). You ask, then **file each date
as a suggestion**; nothing is added until Kev or Beth taps Accept on the board. You can't
add, edit or remove a date directly, and you never touch Beth's Plate: the agent token
gets a 403 there by design, so don't look for a way around it.

All calls go through `python scripts/agent_api.py METHOD PATH ['JSON']`.

## 1. Read what's there

```
python scripts/agent_api.py GET /api/table/dates
python scripts/agent_api.py GET /api/plates/kev/dates
python scripts/agent_api.py GET /api/table/suggestions
python scripts/agent_api.py GET /api/plates/kev/suggestions
```

`dates` lists what each space already has; `presets` lists the common dates not added
yet (Valentine's, Girlfriend's Day, Mother's Day, Father's Day, Thanksgiving, Christmas,
New Year's Eve, Halloween, Tax Day). Don't ask about anything already there or pending.

## 2. Ask in a few rounds

Ask a handful of questions at once, each with your proposed default, so a "yes" is enough:

1. **Partner and couple dates.** Beth's birthday, your anniversary (dating, wedding),
   Valentine's, Girlfriend's Day (Aug 1; it slipped once, so suggest it).
2. **Family.** Parents', siblings', nieces' and nephews' birthdays; Mother's Day and
   Father's Day; anyone else's anniversary you mark.
3. **Friends and others** worth a text or a card.
4. **One-offs coming up.** Graduations (there are some in 2027), weddings, showers,
   retirements, big trips. Ask for the exact day; if it isn't known yet, file it with
   the best guess and say so in the rationale.
5. **Household.** Holidays you host, Tax Day.

For each person, also ask what they like (it goes in their notes on the board, and
date-options reads it). You can't write person notes yourself: tell Kev to add them on
the Dates view, or put them in the date's notes.

## 3. Decide space, weight and owner

- **Space.** A date only Kev plans for, especially anything about Beth (her birthday, your
  anniversary, Valentine's, Girlfriend's Day), goes on **Kev's Plate** so the planning
  stays a surprise. Family and friends you both mark go on **the Table**.
- **Weight.** `planned` when it needs lead time: a gift to ship, a reservation, travel,
  a party (partner's birthday, anniversary, Mother's/Father's Day, graduations,
  weddings). `light` when a text, call or card the day before is enough.
- **Owner** (Table only): `kev` or `elizabeth` when it's clearly one person's job (each of
  you handles your own family), blank when shared.

## 4. File each date

```
python scripts/agent_api.py POST /api/plates/kev/suggestions '{
  "kind": "date",
  "payload": {
    "title": "Beth'"'"'s birthday",
    "month": 11, "day": 10, "year": 1998,
    "weight": "planned",
    "person_name": "Beth", "relation": "partner",
    "notes": "Budget around $200"
  },
  "rationale": "From the dates interview"
}'
```

- A fixed day: `month` and `day`. A rule (the 2nd Sunday of May): `month`, `nth` (1-4,
  or -1 for last) and `weekday` (0 = Monday ... 6 = Sunday).
- `year` on a yearly date is the first one (birth year, wedding year), so the board can
  say "turns 30"; leave it out if unknown.
- A one-off: `"recurs": false` and `year` required.
- Table dates take `"owner": "kev"` or `"elizabeth"` in the payload.
- For a common date, file it the same way with the preset's fields; Kev can also just tap
  it under "Common dates".

A 409 means it's already there or pending; skip it. A 422 says what's wrong with the date.

## 5. Wrap up

Tell Kev how many dates you filed on each board, and that they're under Dates (and at the
top of the inbox) to accept. Say which ones were guesses.
