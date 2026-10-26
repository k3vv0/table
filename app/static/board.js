// The board for the Table and each Plate (DESIGN.md §1): capture, the four views, merging,
// the daily session and the weekly review. Item text is set with textContent only, since
// titles will later come from synced mail.
(() => {
  const board = document.getElementById("board");
  const API = board.dataset.api;
  const SPACE = board.dataset.space;
  const TODAY = board.dataset.today; // YYYY-MM-DD, US Eastern, from the server
  const SYNCED = board.dataset.synced === "1"; // mail and calendar sync into this space
  const MONEYHUB = board.dataset.moneyhub; // where a money-hub goal link points
  const VIEWS = ["today", "inbox", "later", "done", "session", "review", "dates", "vault", ...(SPACE === "table" ? ["trips"] : []), ...(SYNCED ? ["mail"] : [])];
  const list = document.getElementById("list");
  const statusEl = document.getElementById("status");
  const mergeBar = document.getElementById("merge-bar");
  const mergeGo = document.getElementById("merge-go");
  const mergeCount = document.getElementById("merge-count");

  let view = "today";
  let tripId = null; // the trip open under Trips (#trip-<id>)
  let picking = false;
  let picked = [];

  // --- helpers ------------------------------------------------------------------------

  const el = (tag, props = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children) if (c) node.append(c);
    return node;
  };

  async function api(method, path, body) {
    const res = await fetch(API + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Something went wrong (${res.status}).`);
    return data;
  }

  function toast(message) {
    statusEl.textContent = message;
    statusEl.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => (statusEl.hidden = true), 3000);
  }

  // Dates are plain YYYY-MM-DD strings; build them at noon so no timezone shifts the day.
  const asDate = (iso) => new Date(`${iso}T12:00:00`);
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const addDays = (n) => {
    const d = asDate(TODAY);
    d.setDate(d.getDate() + n);
    return iso(d);
  };
  const nextWeekday = (weekday) => {
    // weekday: 0 = Sunday … 6 = Saturday. Always at least tomorrow.
    const d = asDate(TODAY);
    const ahead = (weekday - d.getDay() + 7) % 7 || 7;
    return addDays(ahead);
  };
  const short = (isoDay) =>
    asDate(isoDay).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });

  function dueChip(item) {
    if (!item.due_on) return null;
    const cls = item.due_on < TODAY ? "chip late" : item.due_on === TODAY ? "chip soon" : "chip";
    const word = item.due_on < TODAY ? "Was due" : "Due";
    return el("span", { class: cls, text: item.due_on === TODAY ? "Due today" : `${word} ${short(item.due_on)}` });
  }

  // Web links in notes (date ideas come with them) become real links. Only https, and the
  // text still goes in through textContent.
  function linkified(text, cls) {
    const out = el("span", { class: cls });
    for (const part of text.split(/(https:\/\/[^\s<>"')]+)/)) {
      if (part.startsWith("https://")) out.append(el("a", { href: part, target: "_blank", rel: "noopener noreferrer", text: part.replace(/^https:\/\/(www\.)?/, "").slice(0, 60) }));
      else if (part) out.append(document.createTextNode(part));
    }
    return out;
  }

  const goalUrl = () => `${MONEYHUB}/plan/goals`;

  // --- actions --------------------------------------------------------------------------

  async function triage(item, action, deferUntil) {
    try {
      await api("POST", `/items/${item.id}/triage`, { action, defer_until: deferUntil || null });
      const words = {
        today: "On today's plate.",
        done: "Done.",
        reopen: "Back in the inbox.",
        later: deferUntil ? `Back ${short(deferUntil)}.` : "Moved to later.",
      };
      toast(words[action]);
      await render();
    } catch (err) {
      toast(err.message);
    }
  }

  function laterMenu(item) {
    const options = [
      ["Tomorrow", addDays(1)],
      ["Saturday", nextWeekday(6)],
      ["Sunday", nextWeekday(0)],
      ["Next Monday", nextWeekday(1)],
      ["In two weeks", addDays(14)],
    ];
    const menu = el("div", { class: "menu", role: "menu" });
    for (const [label, day] of options) {
      menu.append(el("button", { type: "button", role: "menuitem", text: `${label} · ${short(day)}`, onclick: () => triage(item, "later", day) }));
    }
    const pick = el("input", { type: "date", min: addDays(1), "aria-label": "Pick a day" });
    pick.addEventListener("change", () => pick.value && triage(item, "later", pick.value));
    menu.append(el("label", { class: "pick" }, "Pick a day ", pick));
    menu.append(el("button", { type: "button", role: "menuitem", text: "No date (weekly review)", onclick: () => triage(item, "later") }));
    return menu;
  }

  function editor(item, row) {
    const form = el("form", { class: "editor" });
    const title = el("input", { name: "title", value: item.title, maxlength: 300, required: true, "aria-label": "Title" });
    const notes = el("textarea", { name: "notes", rows: 3, "aria-label": "Notes", placeholder: "Notes" });
    notes.value = item.notes;
    const due = el("input", { type: "date", name: "due_on", value: item.due_on || "", "aria-label": "Due" });
    const watch = el("input", { type: "checkbox", name: "watch", checked: item.kind === "watch" });
    const goal = el("input", { type: "number", name: "goal", min: 1, value: item.moneyhub_goal_id || "", class: "num", "aria-label": "money-hub goal number" });
    const fields = [title, notes];
    if (/https:\/\//.test(item.notes)) fields.push(linkified(item.notes, "notes-links"));
    fields.push(el("label", { class: "due" }, "Due ", due));
    if (item.kind === "task" || item.kind === "watch") fields.push(el("label", { class: "chk" }, watch, " Watching for something (a reply, an approval)"));
    fields.push(el("label", { class: "due" }, "money-hub goal # ", goal, item.moneyhub_goal_id ? el("a", { href: goalUrl(), target: "_blank", rel: "noopener", text: "Open Goals" }) : null));
    let owner = null;
    if (SPACE === "table") {
      owner = el(
        "select",
        { name: "owner", "aria-label": "Whose" },
        el("option", { value: "", text: "Shared" }),
        el("option", { value: "kev", text: "Kev's" }),
        el("option", { value: "elizabeth", text: "Beth's" }),
      );
      owner.value = item.owner || "";
      fields.push(el("label", { class: "due" }, "Whose ", owner));
    }
    if (item.sources.length > 1) {
      const from = el("ul", { class: "sources" });
      for (const s of item.sources) from.append(el("li", { text: `${s.kind}: ${s.snippet}` }));
      fields.push(el("div", { class: "muted", text: "Merged from:" }), from);
    }
    form.append(
      ...fields,
      el(
        "div",
        { class: "editor-row" },
        el("button", { type: "submit", class: "btn primary", text: "Save" }),
        el("button", { type: "button", class: "btn ghost", text: "Cancel", onclick: () => render() }),
      ),
    );
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = { title: title.value, notes: notes.value, due_on: due.value || null, moneyhub_goal_id: goal.value ? Number(goal.value) : null };
      if (item.kind === "task" || item.kind === "watch") body.kind = watch.checked ? "watch" : "task";
      if (owner) body.owner = owner.value || null;
      try {
        await api("PATCH", `/items/${item.id}`, body);
        toast("Saved.");
        await render();
      } catch (err) {
        toast(err.message);
      }
    });
    row.replaceChildren(form);
    title.focus();
  }

  // The web link an item is about: what a Shortcut shared, or the first https link in its
  // title or notes. Only https, like linkified().
  function itemLink(item) {
    for (const s of item.sources) if (s.kind === "shortcut" && s.ref.startsWith("https://")) return s.ref;
    return `${item.title} ${item.notes}`.match(/https:\/\/[^\s<>"')]+/)?.[0] || null;
  }

  // Which buttons a row offers depends on where the item sits right now.
  function actionsFor(item, context) {
    const out = [];
    const btn = (text, fn, cls = "btn small") => el("button", { type: "button", class: cls, text, onclick: fn });
    const link = itemLink(item);
    if (link) out.push(el("a", { class: "btn small", href: link, target: "_blank", rel: "noopener noreferrer", text: "Open" }));
    if (item.status === "done") return [...out, btn("Reopen", () => triage(item, "reopen"))];
    const onToday = context === "today" || context === "session-today";
    if (context === "session-today") out.push(btn("Keep", () => toast("Kept for today.")));
    if (!onToday) out.push(btn("Today", () => triage(item, "today")));
    if (onToday) out.push(btn("Tomorrow", () => triage(item, "later", addDays(1))));
    // Sunday is when the house gets done. On a Saturday it's already the Tomorrow button.
    const sunday = nextWeekday(0);
    if (!(onToday && sunday === addDays(1))) out.push(btn("Sunday", () => triage(item, "later", sunday)));
    const later = btn("Later", (e) => {
      const wrap = e.currentTarget.parentElement;
      const open = wrap.querySelector(".menu");
      if (open) return open.remove();
      wrap.append(laterMenu(item));
    });
    out.push(later);
    out.push(btn("Done", () => triage(item, "done"), "btn small done"));
    return out;
  }

  function row(item, context) {
    const li = el("li", { class: `item${item.status === "done" ? " is-done" : ""}`, "data-id": item.id });
    const meta = el("div", { class: "meta" });
    const due = dueChip(item);
    if (due) meta.append(due);
    if (item.status === "later" && item.defer_until && context !== "today") meta.append(el("span", { class: "chip", text: `Back ${short(item.defer_until)}` }));
    if (item.status === "later" && item.defer_until && item.defer_until <= TODAY) meta.append(el("span", { class: "chip soon", text: "Back today" }));
    if (SPACE === "table") meta.append(el("span", { class: "chip owner", text: item.owner === "kev" ? "Kev's" : item.owner === "elizabeth" ? "Beth's" : "Shared" }));
    if (item.kind === "watch") meta.append(el("span", { class: "chip watch", text: "Watching" }));
    if (item.kind === "date") meta.append(el("span", { class: "chip weight", text: "Date" }));
    if (item.moneyhub_goal_id) meta.append(el("span", { class: "chip", text: `money-hub goal #${item.moneyhub_goal_id}` }));
    if (item.sources.length > 1) meta.append(el("span", { class: "chip", text: `${item.sources.length} sources` }));
    else if (["email", "calendar"].includes(item.sources[0]?.kind) && item.kind !== "date") meta.append(el("span", { class: "chip", text: item.sources[0].kind === "email" ? "From mail" : "From calendar" }));

    const body = el(
      "button",
      { type: "button", class: "item-body", onclick: () => (picking ? togglePick(item, li) : editor(item, li)) },
      el("span", { class: "title", text: item.title }),
      item.notes ? el("span", { class: "notes", text: item.notes }) : null,
      meta.childNodes.length ? meta : null,
    );
    li.append(body);
    if (!picking) li.append(el("div", { class: "actions" }, ...actionsFor(item, context)));
    if (picked.includes(item.id)) li.classList.add("picked");
    return li;
  }

  // --- merging ---------------------------------------------------------------------------

  function startMerge() {
    picking = true;
    picked = [];
    mergeBar.hidden = false;
    updateMergeBar();
    render();
  }

  function stopMerge() {
    picking = false;
    picked = [];
    mergeBar.hidden = true;
    render();
  }

  function togglePick(item, li) {
    picked = picked.includes(item.id) ? picked.filter((id) => id !== item.id) : [...picked, item.id];
    li.classList.toggle("picked", picked.includes(item.id));
    updateMergeBar();
  }

  function updateMergeBar() {
    mergeCount.textContent = picked.length ? `${picked.length} picked; the first one stays` : "Pick the duplicates";
    mergeGo.disabled = picked.length < 2;
  }

  mergeGo.addEventListener("click", async () => {
    try {
      const [keep, ...rest] = picked;
      await api("POST", "/items/merge", { keep_id: keep, merge_ids: rest });
      toast(`Merged ${rest.length + 1} items into one.`);
      stopMerge();
    } catch (err) {
      toast(err.message);
    }
  });
  document.getElementById("merge-cancel").addEventListener("click", stopMerge);

  // --- views -----------------------------------------------------------------------------

  function section(title, items, context, empty) {
    const wrap = el("div", { class: "section" }, el("h2", { text: title }));
    if (!items.length) {
      if (empty) wrap.append(el("p", { class: "muted", text: empty }));
      return wrap;
    }
    const ul = el("ul", { class: "items" });
    for (const item of items) ul.append(row(item, context));
    wrap.append(ul);
    return wrap;
  }

  function setCounts(counts) {
    for (const [k, v] of Object.entries(counts)) {
      const n = board.querySelector(`[data-count="${k}"]`);
      if (n) n.textContent = v ? String(v) : "";
    }
  }

  const EMPTY = {
    today: "Nothing on today's plate. Pull something from the inbox or capture it above.",
    inbox: "Inbox zero. New captures land here until you triage them.",
    later: "Nothing deferred.",
    done: "Nothing finished in the last 30 days yet.",
  };

  // --- suggestions (DESIGN.md §10) ---------------------------------------------------------
  // What the sweep proposed. Nothing changes until someone taps Accept. Text from mail is
  // someone else's words, so it only ever goes in through textContent.

  function suggestionCard(s) {
    const btn = (text, fn, cls = "btn small") => el("button", { type: "button", class: cls, text, onclick: fn });
    const decide = async (action, body) => {
      try {
        await api("POST", `/suggestions/${s.id}/${action}`, body);
        toast(action === "dismiss" ? "Dismissed." : s.kind === "date" ? "Date added." : s.kind === "trip" ? "Trip added. It's under Trips." : body?.today ? "Accepted, on today's plate." : "Accepted.");
        await render();
      } catch (err) {
        toast(err.message);
      }
    };
    const heading = {
      item: s.item_kind === "watch" ? "Watch for" : "New item",
      note: `Update “${s.item?.title || ""}”`,
      merge: `Merge into “${s.item?.title || ""}”`,
      close: `Mark “${s.item?.title || ""}” done`,
      date: "Add a date",
      goal: `Link “${s.item?.title || ""}” to a money-hub goal`,
      trip: "New trip",
      module: `Option for “${s.payload.trip_title || "a trip"}”`,
    }[s.kind];
    const meta = el("div", { class: "meta" });
    if (s.due_on) meta.append(el("span", { class: "chip", text: `Due ${short(s.due_on)}` }));
    if (s.source?.kind === "email") meta.append(el("span", { class: "chip", text: `Mail from ${s.source.from}: ${s.source.subject}` }));
    if (s.source?.kind === "calendar") meta.append(el("span", { class: "chip", text: `Calendar: ${s.source.summary}, ${short(s.source.on)}` }));
    for (const m of s.merge) meta.append(el("span", { class: "chip", text: `+ ${m.title}` }));
    if (s.kind === "date") {
      if (s.payload.person_name) meta.append(el("span", { class: "chip", text: s.payload.relation ? `${s.payload.person_name} · ${s.payload.relation}` : s.payload.person_name }));
      meta.append(el("span", { class: "chip", text: s.payload.rule }));
      meta.append(el("span", { class: "chip weight", text: s.payload.weight === "planned" ? "Planned: 30 days ahead" : "Light: the day before" }));
    }
    if (s.kind === "goal") meta.append(el("span", { class: "chip", text: `money-hub goal #${s.payload.moneyhub_goal_id}` }));
    if (s.kind === "trip") {
      meta.append(el("span", { class: "chip", text: s.payload.starts_on && s.payload.ends_on ? `${short(s.payload.starts_on)} to ${short(s.payload.ends_on)}` : s.payload.window || "Dates not set" }));
      if (s.payload.places) meta.append(el("span", { class: "chip", text: s.payload.places }));
    }
    const actions = [btn("Accept", () => decide("accept", {}), "btn small primary")];
    if (s.kind === "item") actions.push(btn("Accept for today", () => decide("accept", { today: true })));
    if (s.kind === "date") actions.push(btn("Edit", () => dateSuggestionEditor(s, li, decide)));
    actions.push(btn("Dismiss", () => decide("dismiss"), "btn small ghost"));
    const li = el(
      "li",
      { class: "item suggestion" },
      el(
        "div",
        { class: "item-body" },
        el("span", { class: "kicker", text: heading }),
        s.title ? el("span", { class: "title", text: s.title }) : null,
        s.notes ? linkified(s.notes, "notes full") : null,
        meta.childNodes.length ? meta : null,
        s.rationale ? el("span", { class: "why", text: s.rationale }) : null,
      ),
      el("div", { class: "actions" }, ...actions),
    );
    return li;
  }

  // A suggested date, opened up to fix before it's added: the day the interview got wrong,
  // whose it is, how much lead time. Saving accepts it with the changes.
  function dateSuggestionEditor(s, li, decide) {
    const p = s.payload;
    const form = el("form", { class: "editor" });
    const title = el("input", { name: "title", value: s.title, maxlength: 200, required: true, "aria-label": "Title" });
    const who = el("input", { value: p.person_name || "", placeholder: "Whose (optional)", maxlength: 120, "aria-label": "Person" });
    const relation = el("input", { value: p.relation || "", placeholder: "Who they are (Beth's mom)", maxlength: 200, "aria-label": "Relation" });
    const notes = el("textarea", { rows: 2, "aria-label": "Notes", placeholder: "Notes (budget, what worked last year)" });
    notes.value = p.notes || "";
    const yearShown = p.year || Number(TODAY.slice(0, 4));
    const day = el("input", { type: "date", required: p.day !== null, "aria-label": "Day" });
    if (p.day !== null) day.value = `${yearShown}-${String(p.month).padStart(2, "0")}-${String(p.day).padStart(2, "0")}`;
    const repeat = el(
      "select",
      { "aria-label": "Repeats" },
      el("option", { value: "yearly", text: "Every year" }),
      el("option", { value: "since", text: "Every year, counting from this one" }),
      el("option", { value: "once", text: "Just this once" }),
    );
    repeat.value = !p.recurs ? "once" : p.year ? "since" : "yearly";
    const weight = weightSelect(p.weight);
    const owner = SPACE === "table" ? ownerSelect(p.owner) : null;
    const fields = [title, who, relation, notes];
    // A rule (the second Sunday of May) stays a rule unless a fixed day is picked.
    if (p.day === null) fields.push(el("p", { class: "muted", text: `${p.rule}. Pick a day below to make it a fixed date instead.` }));
    fields.push(el("label", { class: "due" }, "Day ", day), el("label", { class: "due" }, "Repeats ", repeat), el("label", { class: "due" }, "Weight ", weight));
    if (owner) fields.push(el("label", { class: "due" }, "Whose job ", owner));
    form.append(
      ...fields,
      el(
        "div",
        { class: "editor-row" },
        el("button", { type: "submit", class: "btn primary", text: "Save date" }),
        el("button", { type: "button", class: "btn ghost", text: "Cancel", onclick: () => render() }),
      ),
    );
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const body = { title: title.value, person_name: who.value, relation: relation.value, notes: notes.value, weight: weight.value, recurs: repeat.value !== "once" };
      if (owner) body.owner = owner.value || null;
      if (day.value) {
        const [y, m, d] = day.value.split("-").map(Number);
        Object.assign(body, { month: m, day: d, year: repeat.value === "yearly" ? null : y });
      } else {
        body.year = repeat.value === "yearly" ? null : p.year;
      }
      decide("accept", { date: body });
    });
    li.replaceChildren(form);
    title.focus();
  }

  async function suggestionsSection(only) {
    let { suggestions } = await api("GET", "/suggestions");
    // Options on a trip are shown on the trip itself, next to the leg they're for.
    suggestions = suggestions.filter((s) => (only ? s.kind === only : s.kind !== "module"));
    if (!suggestions.length) return null;
    return el(
      "div",
      { class: "section" },
      el("h2", { text: `Suggested (${suggestions.length})` }),
      el("ul", { class: "items" }, ...suggestions.map(suggestionCard)),
    );
  }

  // --- mail (DESIGN.md §10) ------------------------------------------------------------------
  // How email-sort filed recent mail, so a wrong call can be put right.

  const SORTS = { flagged: "Flagged", keep: "Kept", vault: "Vault", noise: "Noise", unsorted: "Not sorted yet" };
  let mailSort = "flagged";

  async function renderMail() {
    const { messages } = await api("GET", `/mail?sort=${mailSort}&limit=100`);
    const tabs = el("div", { class: "list-tools sorts" });
    for (const [key, label] of Object.entries(SORTS)) {
      tabs.append(el("button", { type: "button", class: `btn small${key === mailSort ? " on" : " ghost"}`, text: label, onclick: () => ((mailSort = key), render()) }));
    }
    const ul = el("ul", { class: "items" });
    for (const m of messages) {
      const pick = el("select", { "aria-label": "Sort" });
      for (const [key, label] of Object.entries(SORTS)) if (key !== "unsorted") pick.append(el("option", { value: key, text: label }));
      pick.value = m.sort === "unsorted" ? "keep" : m.sort;
      pick.addEventListener("change", async () => {
        try {
          await api("PATCH", `/mail/${m.id}`, { sort: pick.value, reason: "Changed by hand" });
          toast(`Moved to ${SORTS[pick.value]}.`);
          await render();
        } catch (err) {
          toast(err.message);
        }
      });
      const meta = el("div", { class: "meta" });
      if (m.sent_at) meta.append(el("span", { class: "chip", text: new Date(`${m.sent_at}Z`).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) }));
      if (m.archived) meta.append(el("span", { class: "chip", text: "Archived in Gmail" }));
      meta.append(el("span", { class: "chip", text: m.account }));
      ul.append(
        el(
          "li",
          { class: "item mail" },
          el(
            "div",
            { class: "item-body" },
            el("span", { class: "kicker", text: m.from_name || m.from_addr }),
            el("span", { class: "title", text: m.subject || "(no subject)" }),
            m.sort_reason ? el("span", { class: "why", text: `${m.sorted_by}: ${m.sort_reason}` }) : null,
            meta,
          ),
          el("div", { class: "actions" }, pick),
        ),
      );
    }
    list.replaceChildren(
      el("p", { class: "lead", text: "How the sweep sorted recent mail. Change a sort if it got one wrong. Anything already archived as noise is under Table/Noise in Gmail." }),
      tabs,
      messages.length ? ul : el("p", { class: "muted", text: "Nothing here." }),
    );
  }


  // --- people and dates (DESIGN.md §11) -------------------------------------------------------
  // Planned dates go into the inbox 30 days ahead, with ideas from the morning routine; light
  // ones land on today's plate the day before. Both push on the day.

  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const WEIGHT_TEXT = { planned: "Planned", light: "Light" };

  function whenText(d) {
    if (d.next_on === null) return "Passed";
    if (d.days_away === 0) return "Today";
    if (d.days_away === 1) return "Tomorrow";
    return `${short(d.next_on)} · in ${d.days_away} days`;
  }

  async function saveDate(path, method, body, done) {
    try {
      await api(method, path, body);
      toast(done);
      await render();
    } catch (err) {
      toast(err.message);
    }
  }

  function ownerSelect(value) {
    const pick = el(
      "select",
      { "aria-label": "Whose job" },
      el("option", { value: "", text: "Shared" }),
      el("option", { value: "kev", text: "Kev's" }),
      el("option", { value: "elizabeth", text: "Beth's" }),
    );
    pick.value = value || "";
    return pick;
  }

  function weightSelect(value) {
    const pick = el(
      "select",
      { "aria-label": "Weight" },
      el("option", { value: "light", text: "Light: the day before" }),
      el("option", { value: "planned", text: "Planned: 30 days ahead, with ideas" }),
    );
    pick.value = value || "light";
    return pick;
  }

  function dateEditor(d, li) {
    const form = el("form", { class: "editor" });
    const title = el("input", { name: "title", value: d.title, maxlength: 200, required: true, "aria-label": "Title" });
    const notes = el("textarea", { rows: 2, "aria-label": "Notes", placeholder: "Notes (budget, what worked last year)" });
    notes.value = d.notes;
    const weight = weightSelect(d.weight);
    const owner = SPACE === "table" ? ownerSelect(d.owner) : null;
    const fields = [title, notes, el("label", { class: "due" }, "Weight ", weight)];
    if (owner) fields.push(el("label", { class: "due" }, "Whose job ", owner));
    let day = null;
    let year = null;
    if (d.day !== null) {
      day = el("input", { type: "date", value: `${d.year || 2000}-${String(d.month).padStart(2, "0")}-${String(d.day).padStart(2, "0")}`, "aria-label": "Day" });
      year = el("input", { type: "checkbox", checked: Boolean(d.year) });
      fields.push(el("label", { class: "due" }, "Day ", day), el("label", { class: "chk" }, year, d.recurs ? " Count the years from that one" : " Year matters"));
    } else {
      fields.push(el("p", { class: "muted", text: d.rule }));
    }
    form.append(
      ...fields,
      el(
        "div",
        { class: "editor-row" },
        el("button", { type: "submit", class: "btn primary", text: "Save" }),
        el("button", { type: "button", class: "btn ghost", text: "Cancel", onclick: () => render() }),
        el("button", { type: "button", class: "btn ghost", text: "Remove", onclick: () => saveDate(`/dates/${d.id}`, "PATCH", { active: false }, "Removed.") }),
      ),
    );
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const body = { title: title.value, notes: notes.value, weight: weight.value };
      if (owner) body.owner = owner.value || null;
      if (day && day.value) {
        const [y, m, dd] = day.value.split("-").map(Number);
        Object.assign(body, { month: m, day: dd, year: year.checked || !d.recurs ? y : null });
      }
      saveDate(`/dates/${d.id}`, "PATCH", body, "Saved.");
    });
    li.replaceChildren(form);
    title.focus();
  }

  function dateRow(d) {
    const li = el("li", { class: "item" });
    const meta = el("div", { class: "meta" });
    meta.append(el("span", { class: d.days_away !== null && d.days_away <= 1 ? "chip soon" : "chip", text: whenText(d) }));
    meta.append(el("span", { class: "chip weight", text: WEIGHT_TEXT[d.weight] }));
    if (d.turns) meta.append(el("span", { class: "chip", text: /birthday/i.test(d.title) ? `Turns ${d.turns}` : `${d.turns} years` }));
    if (SPACE === "table") meta.append(el("span", { class: "chip owner", text: d.owner === "kev" ? "Kev's" : d.owner === "elizabeth" ? "Beth's" : "Shared" }));
    if (d.prep_item_id) meta.append(el("span", { class: "chip", text: d.options_suggested ? "Ideas suggested" : "On the board" }));
    li.append(
      el(
        "button",
        { type: "button", class: "item-body", onclick: () => dateEditor(d, li) },
        d.person ? el("span", { class: "kicker", text: d.person.relation ? `${d.person.name} · ${d.person.relation}` : d.person.name }) : null,
        el("span", { class: "title", text: d.title }),
        el("span", { class: "notes", text: d.rule }),
        meta,
      ),
    );
    return li;
  }

  function addDateForm(people) {
    const form = el("form", { class: "capture" });
    const title = el("input", { name: "title", placeholder: "Add a date: Mom's birthday, our anniversary, Sam's graduation…", maxlength: 200, required: true, "aria-label": "What" });
    const list = el("datalist", { id: "people-list" }, ...people.map((p) => el("option", { value: p.name })));
    const who = el("input", { placeholder: "Whose (optional)", list: "people-list", maxlength: 120, "aria-label": "Person", class: "short" });
    const day = el("input", { type: "date", required: true, "aria-label": "Day" });
    const repeat = el(
      "select",
      { "aria-label": "Repeats" },
      el("option", { value: "yearly", text: "Every year" }),
      el("option", { value: "since", text: "Every year, counting from this one" }),
      el("option", { value: "once", text: "Just this once" }),
    );
    const weight = weightSelect("light");
    const owner = SPACE === "table" ? ownerSelect("") : null;
    form.append(
      title,
      el(
        "div",
        { class: "capture-row" },
        who,
        list,
        el("label", { class: "due" }, "On ", day),
        repeat,
        weight,
        owner ? el("label", { class: "due" }, "Whose job ", owner) : null,
        el("button", { type: "submit", class: "btn primary", text: "Add" }),
      ),
    );
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const [y, m, d] = day.value.split("-").map(Number);
      const body = { title: title.value, month: m, day: d, year: repeat.value === "yearly" ? null : y, recurs: repeat.value !== "once", weight: weight.value, person_name: who.value };
      if (owner) body.owner = owner.value || null;
      saveDate("/dates", "POST", body, "Date added.");
    });
    return form;
  }

  function personRow(p) {
    const li = el("li", { class: "item" });
    const edit = () => {
      const form = el("form", { class: "editor" });
      const name = el("input", { name: "title", value: p.name, maxlength: 120, required: true, "aria-label": "Name" });
      const relation = el("input", { value: p.relation, maxlength: 200, placeholder: "Who they are (Beth's mom, friend from USC)", "aria-label": "Relation" });
      const notes = el("textarea", { rows: 3, placeholder: "What they like, sizes, what they got last time. The date ideas read this.", "aria-label": "Notes" });
      notes.value = p.notes;
      form.append(
        name,
        relation,
        notes,
        el(
          "div",
          { class: "editor-row" },
          el("button", { type: "submit", class: "btn primary", text: "Save" }),
          el("button", { type: "button", class: "btn ghost", text: "Cancel", onclick: () => render() }),
        ),
      );
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        saveDate(`/people/${p.id}`, "PATCH", { name: name.value, relation: relation.value, notes: notes.value }, "Saved.");
      });
      li.replaceChildren(form);
    };
    li.append(
      el(
        "button",
        { type: "button", class: "item-body", onclick: edit },
        el("span", { class: "title", text: p.name }),
        p.relation ? el("span", { class: "kicker", text: p.relation }) : null,
        el("span", { class: "notes", text: p.notes || "No notes yet. Add what they like so the ideas fit." }),
      ),
    );
    return li;
  }

  async function renderDates() {
    const [{ dates, presets }, { people }, suggested] = await Promise.all([api("GET", "/dates"), api("GET", "/people"), suggestionsSection("date")]);
    const parts = [
      el("p", { class: "lead", text: "Planned dates show up in the inbox 30 days ahead with three ideas. Light ones land on today's plate the day before. Both get a push on the day." }),
      addDateForm(people),
      suggested || "",
    ];
    const coming = el("div", { class: "section" }, el("h2", { text: "Coming up" }));
    coming.append(dates.length ? el("ul", { class: "items" }, ...dates.map(dateRow)) : el("p", { class: "muted", text: "No dates yet. Add one above, pick from the common ones below, or ask Claude to run the dates interview." }));
    parts.push(coming);
    if (presets.length) {
      const wrap = el("div", { class: "section" }, el("h2", { text: "Common dates" }));
      const row = el("div", { class: "meta presets" });
      for (const p of presets) {
        row.append(el("button", { type: "button", class: "btn small ghost", title: p.rule, text: `+ ${p.title}`, onclick: () => saveDate(`/dates/presets/${p.key}`, "POST", null, `${p.title} added.`) }));
      }
      wrap.append(row);
      parts.push(wrap);
    }
    if (people.length) parts.push(el("div", { class: "section" }, el("h2", { text: "People" }), el("ul", { class: "items" }, ...people.map(personRow))));
    list.replaceChildren(...parts);
  }

  // --- the vault (DESIGN.md §12) ---------------------------------------------------------------
  // Read-later links. Each morning two get picked for today with a summary. Titles, senders
  // and summaries come from mail and the web, so they only go in through textContent.

  const httpsOnly = (url) => (url && url.startsWith("https://") ? url : null);

  async function markRead(id, word = "Done.") {
    try {
      await api("POST", `/items/${id}/triage`, { action: "done" });
      toast(word);
      await render();
    } catch (err) {
      toast(err.message);
    }
  }

  function entryRow(e, pick) {
    const btn = (text, fn, cls = "btn small") => el("button", { type: "button", class: cls, text, onclick: fn });
    const href = httpsOnly(e.url);
    const meta = el("div", { class: "meta" });
    if (e.from) meta.append(el("span", { class: "chip", text: `Mail from ${e.from}` }));
    else if (e.url) meta.append(el("span", { class: "chip", text: e.url.replace(/^https?:\/\/(www\.)?/, "").split("/")[0] }));
    meta.append(el("span", { class: "chip", text: `Saved ${new Date(`${e.created_at}Z`).toLocaleDateString(undefined, { month: "short", day: "numeric" })}` }));
    const actions = [];
    if (href) actions.push(el("a", { class: "btn small primary", href, target: "_blank", rel: "noopener noreferrer", text: e.from ? "Open in Gmail" : "Open" }));
    actions.push(btn("Done", () => markRead(e.id), "btn small done"), btn("Remove", () => markRead(e.id, "Removed from the vault."), "btn small ghost"));
    return el(
      "li",
      { class: pick ? "item suggestion" : "item" },
      el(
        "div",
        { class: "item-body" },
        pick ? el("span", { class: "kicker", text: "Today's read" }) : null,
        el("span", { class: "title", text: e.title }),
        pick ? el("span", { class: "notes full", text: pick.summary }) : e.notes ? el("span", { class: "notes", text: e.notes }) : null,
        meta,
        pick && pick.why ? el("span", { class: "why", text: pick.why }) : null,
      ),
      el("div", { class: "actions" }, ...actions),
    );
  }

  async function picksSection() {
    const { picks } = await api("GET", "/vault");
    const open = picks.filter((p) => p.entry.status !== "done");
    if (!open.length) return null;
    return el("div", { class: "section" }, el("h2", { text: "Today's reads" }), el("ul", { class: "items" }, ...open.map((p) => entryRow(p.entry, p))));
  }

  function saveLinkForm() {
    const form = el("form", { class: "capture" });
    const url = el("input", { name: "url", type: "url", placeholder: "Paste a link to read later", "aria-label": "Link" });
    const title = el("input", { class: "short", placeholder: "Title (optional)", maxlength: 300, "aria-label": "Title" });
    form.append(url, el("div", { class: "capture-row" }, title, el("button", { type: "submit", class: "btn primary", text: "Save" })));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api("POST", "/vault", { url: url.value, title: title.value });
        toast("Saved to the vault.");
        await render();
      } catch (err) {
        toast(err.message);
      }
    });
    return form;
  }

  async function renderVault() {
    const { picks, entries } = await api("GET", "/vault");
    const pickedIds = new Set(picks.map((p) => p.entry.id));
    const open = picks.filter((p) => p.entry.status !== "done");
    const rest = entries.filter((e) => !pickedIds.has(e.id));
    const parts = [
      el("p", { class: "lead", text: "Things to read later: links from the Save to Vault Shortcut or saved here, and mail sorted as vault. Two get picked each morning, with a summary." }),
      saveLinkForm(),
    ];
    if (open.length) parts.push(el("div", { class: "section" }, el("h2", { text: "Today's reads" }), el("ul", { class: "items" }, ...open.map((p) => entryRow(p.entry, p)))));
    const wrap = el("div", { class: "section" }, el("h2", { text: `In the vault (${rest.length})` }));
    wrap.append(rest.length ? el("ul", { class: "items" }, ...rest.map((e) => entryRow(e))) : el("p", { class: "muted", text: "Nothing saved. Share a link to the Save to Vault Shortcut, or paste one above." }));
    parts.push(wrap);
    list.replaceChildren(...parts);
  }

  // --- trips (DESIGN.md §13) -----------------------------------------------------------------
  // Table only. A trip is made of modules: flights, stays, rides and plans. A flight with no
  // flight number is a leg still to fill; the morning routine suggests standby candidates
  // and a paid backup for it. Agent text (titles, notes) only goes in through textContent.

  const STATUS_TEXT = { idea: "Idea", planning: "Planning", set: "Set", away: "Away", home: "Home", reviewed: "Reviewed" };
  const KIND_TEXT = { flight: "Flight", stay: "Stay", ground: "Ride", plan: "Plan" };
  const STATE_TEXT = { option: "Option", chosen: "Chosen", booked: "Booked", dropped: "Dropped" };
  const PAYER_TEXT = { "": "", standby: "Standby", cash: "Paid", points: "Points", work: "Work pays", family: "Free stay", scholarship: "Scholarship" };
  const PEOPLE_TEXT = { kev: "Kev", elizabeth: "Beth" };

  const tripDays = (t) => (t.starts_on && t.ends_on ? `${short(t.starts_on)} to ${short(t.ends_on)}` : t.window || "Dates not set");
  const hhmm = (t) => {
    if (!t) return "";
    const [h, m] = t.split(":").map(Number);
    return `${h % 12 || 12}${m ? `:${String(m).padStart(2, "0")}` : ""}${h < 12 ? "am" : "pm"}`;
  };

  async function tripCall(method, path, body, done) {
    try {
      await api(method, path, body);
      if (done) toast(done);
      await render();
    } catch (err) {
      toast(err.message);
    }
  }

  function travelerPick(selected, all = ["kev", "elizabeth"]) {
    const pick = el("select", { "aria-label": "Who" });
    if (all.length > 1) pick.append(el("option", { value: all.join(","), text: "Both" }));
    for (const t of all) pick.append(el("option", { value: t, text: PEOPLE_TEXT[t] }));
    pick.value = selected && selected.length && selected.length < all.length ? selected.join(",") : all.join(",");
    return pick;
  }

  function addTripForm() {
    const form = el("form", { class: "capture" });
    const title = el("input", { name: "title", placeholder: "Add a trip: Thanksgiving in San Marcos, Chicago work trip…", maxlength: 200, required: true, "aria-label": "Trip" });
    const window_ = el("input", { class: "short", placeholder: "When, roughly (week before Thanksgiving)", maxlength: 300, "aria-label": "Window" });
    const start = el("input", { type: "date", "aria-label": "First day" });
    const end = el("input", { type: "date", "aria-label": "Last day" });
    const who = travelerPick();
    form.append(
      title,
      el("div", { class: "capture-row" }, window_, el("label", { class: "due" }, "From ", start), el("label", { class: "due" }, "to ", end), who, el("button", { type: "submit", class: "btn primary", text: "Add" })),
    );
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const trip = await api("POST", "/trips", { title: title.value, window: window_.value, starts_on: start.value || null, ends_on: end.value || null, travelers: who.value.split(",") });
        toast("Trip added.");
        location.hash = `trip-${trip.id}`;
      } catch (err) {
        toast(err.message);
      }
    });
    return form;
  }

  // One labelled form for adding and editing a place, so every field says what it's for.
  const field = (label, input, hint) => el("label", { class: "field" }, el("span", { class: "field-label", text: label }), input, hint ? el("span", { class: "hint", text: hint }) : null);

  function placeForm(p, onDone) {
    const form = el("form", { class: "editor place-form" });
    const name = el("input", { name: "title", value: p.name || "", maxlength: 120, required: true, placeholder: "Home, San Marcos, Santa Clarita" });
    const airports = el("input", { value: p.airports || "", placeholder: "JFK LGA" });
    const bags = el("input", { value: p.bags_airport || "", placeholder: "LGA", maxlength: 3, class: "num" });
    const hosts = el("input", { value: p.hosts || "", placeholder: "Kev's cousins" });
    const ride = el("input", { value: p.ride || "", placeholder: "Uber from AUS, about 40 minutes" });
    const cost = el("input", { type: "number", min: 0, value: p.ride_cost ?? "", class: "num", placeholder: "60" });
    const home = el("input", { type: "checkbox", checked: Boolean(p.home) });
    const notes = el("textarea", { rows: 2, placeholder: "Anything else worth remembering about going there" });
    notes.value = p.notes || "";
    form.append(
      field("Name", name, "What you call it."),
      field("Airports", airports, "Three-letter codes, best first, separated by spaces. Flights are searched from all of them."),
      field("With checked bags", bags, "Optional: the airport you use instead when you check a bag."),
      field("Who you stay with", hosts, "Optional. Leave blank if you'd need a hotel."),
      field("Usual ride from the airport", ride, "Optional."),
      field("Ride cost, each way ($)", cost, "Optional, a rough number for the trip estimate."),
      el("label", { class: "chk" }, home, " This is home: trips start and end here"),
      field("Notes", notes),
      el("div", { class: "editor-row" }, el("button", { type: "submit", class: "btn primary", text: p.id ? "Save" : "Add place" }), el("button", { type: "button", class: "btn ghost", text: "Cancel", onclick: () => render() })),
    );
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const body = { name: name.value, airports: airports.value, bags_airport: bags.value, hosts: hosts.value, ride: ride.value, ride_cost: cost.value ? Number(cost.value) : null, home: home.checked, notes: notes.value };
      tripCall(p.id ? "PATCH" : "POST", p.id ? `/places/${p.id}` : "/places", body, p.id ? "Saved." : "Place added.");
    });
    onDone(form);
    name.focus();
  }

  function placeRow(p) {
    const li = el("li", { class: "item" });
    const meta = el("div", { class: "meta" });
    if (p.home) meta.append(el("span", { class: "chip weight", text: "Home" }));
    if (p.airports) meta.append(el("span", { class: "chip", text: `Flies from ${p.airports}` + (p.bags_airport ? ` · with bags: ${p.bags_airport}` : "") }));
    if (p.ride_cost !== null) meta.append(el("span", { class: "chip", text: `Ride about $${p.ride_cost}` }));
    li.append(el("button", { type: "button", class: "item-body", onclick: () => placeForm(p, (form) => li.replaceChildren(form)) }, el("span", { class: "title", text: p.name }), p.hosts || p.ride ? el("span", { class: "notes", text: [p.hosts && `Stays with ${p.hosts}`, p.ride].filter(Boolean).join(" · ") }) : null, meta));
    return li;
  }

  function addPlaceButtons(places) {
    const wrap = el("div", { class: "meta presets" });
    const open = (p) => placeForm(p, (form) => wrap.replaceWith(form));
    if (!places.some((p) => p.home)) {
      wrap.append(el("button", { type: "button", class: "btn small primary", text: "+ Set up Home", onclick: () => open({ name: "Home", airports: "JFK LGA", bags_airport: "LGA", home: true }) }));
    }
    wrap.append(el("button", { type: "button", class: "btn small", text: "+ Add a place", onclick: () => open({}) }));
    return wrap;
  }

  async function renderTrips() {
    const [{ trips }, { places }, suggested] = await Promise.all([api("GET", "/trips"), api("GET", "/places"), suggestionsSection("trip")]);
    const parts = [
      el("p", { class: "lead", text: "Each trip is built from flights, stays and rides that either of you can add. Standby candidates and a paid backup show up on each flight, and you get reminders to check loads as the day gets close." }),
      addTripForm(),
      suggested || "",
    ];
    const ul = el("ul", { class: "items" });
    for (const t of trips) {
      const meta = el("div", { class: "meta" });
      meta.append(el("span", { class: t.status === "away" || t.status === "set" ? "chip soon" : "chip", text: STATUS_TEXT[t.status] }));
      meta.append(el("span", { class: "chip", text: tripDays(t) }));
      if (t.travelers.length === 1) meta.append(el("span", { class: "chip owner", text: PEOPLE_TEXT[t.travelers[0]] }));
      if (t.legs.length) meta.append(el("span", { class: "chip", text: `${t.legs.length} flight day${t.legs.length > 1 ? "s" : ""}` }));
      ul.append(el("li", { class: "item" }, el("a", { class: "item-body", href: `#trip-${t.id}` }, el("span", { class: "title", text: t.title }), t.places ? el("span", { class: "notes", text: t.places }) : null, meta)));
    }
    parts.push(el("div", { class: "section" }, el("h2", { text: "Trips" }), trips.length ? ul : el("p", { class: "muted", text: "No trips yet. Add one above." })));
    parts.push(el("div", { class: "section" }, el("h2", { text: "Places" }),
      el("p", { class: "muted", text: "Places you go back to, so a new trip there starts filled in: which airports to fly from, who you stay with, and the usual ride. Tap one to change it." }),
      places.length ? el("ul", { class: "items" }, ...places.map(placeRow)) : "",
      addPlaceButtons(places)));
    list.replaceChildren(...parts);
  }

  function moduleMeta(m, trip) {
    const meta = el("div", { class: "meta" });
    if (m.state) meta.append(el("span", { class: m.state === "booked" ? "chip weight" : m.state === "chosen" ? "chip soon" : "chip", text: STATE_TEXT[m.state] || "Suggested" }));
    if (m.payer) meta.append(el("span", { class: "chip", text: PAYER_TEXT[m.payer] }));
    if (m.cost !== null && m.cost !== undefined) meta.append(el("span", { class: "chip", text: `$${m.cost}` }));
    if (m.points) meta.append(el("span", { class: "chip", text: `${m.points.toLocaleString()} points` }));
    if (m.kind !== "flight" && m.starts_on) meta.append(el("span", { class: "chip", text: m.ends_on && m.ends_on !== m.starts_on ? `${short(m.starts_on)} to ${short(m.ends_on)}` : short(m.starts_on) }));
    if (m.travelers && m.travelers.length && m.travelers !== trip.travelers.join(",")) meta.append(el("span", { class: "chip owner", text: m.travelers.split(",").map((t) => PEOPLE_TEXT[t]).join(" and ") }));
    if (m.confirmation) meta.append(el("span", { class: "chip", text: `Conf. ${m.confirmation}` }));
    return meta;
  }

  function flightLine(m) {
    if (!m.flight) return `${m.from_airports || "?"} → ${m.to_airports || "?"}: leg to fill`;
    const times = [m.departs && `leaves ${hhmm(m.departs)}`, m.arrives && `lands ${hhmm(m.arrives)}`].filter(Boolean).join(", ");
    return `${m.flight} ${m.from_airports} → ${m.to_airports}${times ? `, ${times}` : ""}`;
  }

  function moduleEditor(m, trip, li) {
    const form = el("form", { class: "editor" });
    const title = el("input", { name: "title", value: m.title, maxlength: 200, required: true, "aria-label": "Title" });
    const notes = el("textarea", { rows: 2, placeholder: "Notes", "aria-label": "Notes" });
    notes.value = m.notes;
    const cost = el("input", { type: "number", min: 0, value: m.cost ?? "", class: "num", "aria-label": "Cost" });
    const link = el("input", { value: m.link, placeholder: "https:// link", "aria-label": "Link" });
    const conf = el("input", { value: m.confirmation || "", placeholder: "Confirmation number", maxlength: 60, "aria-label": "Confirmation" });
    form.append(title, notes, link, conf, el("label", { class: "due" }, "Cost $ ", cost),
      el("div", { class: "editor-row" }, el("button", { type: "submit", class: "btn primary", text: "Save" }), el("button", { type: "button", class: "btn ghost", text: "Cancel", onclick: () => render() })));
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      tripCall("PATCH", `/trips/${trip.id}/modules/${m.id}`, { title: title.value, notes: notes.value, link: link.value, confirmation: conf.value, cost: cost.value ? Number(cost.value) : null }, "Saved.");
    });
    li.replaceChildren(form);
  }

  function moduleRow(m, trip) {
    const btn = (text, fn, cls = "btn small") => el("button", { type: "button", class: cls, text, onclick: fn });
    const state = (s, word) => () => tripCall("PATCH", `/trips/${trip.id}/modules/${m.id}`, { state: s }, word);
    const li = el("li", { class: `item${m.state === "dropped" ? " is-done" : ""}` });
    const actions = [];
    const flight = m.kind === "flight" && m.flight;
    const leg = m.kind === "flight" && !m.flight; // a leg to fill: its candidates get chosen, not it
    if (m.state === "option" && !leg) actions.push(btn(flight ? "Flying this one" : "Choose", state("chosen", "Chosen."), "btn small primary"));
    if (m.state === "chosen") actions.push(btn(flight ? "Made it" : "Booked", state("booked", flight ? "Made it. No more check reminders for this day." : "Booked.")), btn(flight ? "Didn't make it" : "Drop", state("dropped", "Dropped."), "btn small ghost"));
    if (m.state === "option") actions.push(btn("Drop", state("dropped", "Dropped."), "btn small ghost"));
    if (m.state === "dropped" || m.state === "booked") actions.push(btn("Reopen", state("option", "Back to an option."), "btn small ghost"));
    const href = httpsOnly(m.link);
    if (href) actions.push(el("a", { class: "btn small", href, target: "_blank", rel: "noopener noreferrer", text: "Open" }));
    li.append(
      el("button", { type: "button", class: "item-body", onclick: () => moduleEditor(m, trip, li) },
        el("span", { class: "kicker", text: KIND_TEXT[m.kind] }),
        el("span", { class: "title", text: m.kind === "flight" ? flightLine(m) : m.title }),
        m.notes ? el("span", { class: "notes", text: m.notes }) : null,
        moduleMeta(m, trip)),
      el("div", { class: "actions" }, ...actions),
    );
    return li;
  }

  function suggestedRow(s, trip) {
    const btn = (text, fn, cls = "btn small") => el("button", { type: "button", class: cls, text, onclick: fn });
    const decide = (action, body, word) => tripCall("POST", `/suggestions/${s.id}/${action}`, body, word);
    const flight = s.kind === "flight" && s.flight;
    const actions = [];
    if (s.module_id) actions.push(btn("Update the option", () => decide("accept", {}, "Updated."), "btn small primary"));
    else {
      actions.push(btn(flight ? "Flying this one" : "Choose", () => decide("accept", { state: "chosen" }, "Chosen."), "btn small primary"));
      actions.push(btn("Keep as an option", () => decide("accept", {}, "Added as an option.")));
    }
    const href = httpsOnly(s.link);
    if (href) actions.push(el("a", { class: "btn small", href, target: "_blank", rel: "noopener noreferrer", text: "Open" }));
    actions.push(btn("Dismiss", () => decide("dismiss", undefined, "Dismissed."), "btn small ghost"));
    return el("li", { class: "item suggestion" },
      el("div", { class: "item-body" },
        el("span", { class: "kicker", text: s.module_id ? `Refresh: ${KIND_TEXT[s.kind]}` : `Suggested ${KIND_TEXT[s.kind].toLowerCase()}` }),
        el("span", { class: "title", text: s.kind === "flight" ? flightLine(s) : s.title }),
        s.notes ? linkified(s.notes, "notes full") : null,
        moduleMeta({ ...s, state: null }, trip),
        s.rationale ? el("span", { class: "why", text: s.rationale }) : null),
      el("div", { class: "actions" }, ...actions));
  }

  function addModuleForm(trip) {
    const form = el("form", { class: "capture" });
    const kind = el("select", { "aria-label": "Kind" }, ...Object.entries(KIND_TEXT).map(([k, v]) => el("option", { value: k, text: v })));
    const title = el("input", { name: "title", placeholder: "Add to the trip: a flight leg, Beth's apartment, Uber to San Marcos…", maxlength: 200, "aria-label": "Title" });
    const start = el("input", { type: "date", value: trip.starts_on || "", "aria-label": "Day" });
    const end = el("input", { type: "date", "aria-label": "Until" });
    const who = travelerPick(trip.travelers, trip.travelers);
    const payer = el("select", { "aria-label": "Who pays" }, ...Object.entries(PAYER_TEXT).map(([k, v]) => el("option", { value: k, text: v || "Who pays?" })));
    const from = el("input", { class: "short", placeholder: "From: JFK LGA", "aria-label": "From airports" });
    const to = el("input", { class: "short", placeholder: "To: AUS SAT", "aria-label": "To airports" });
    const flightNo = el("input", { class: "short", placeholder: "Flight (blank = leg to fill)", maxlength: 20, "aria-label": "Flight" });
    const departs = el("input", { type: "time", "aria-label": "Leaves" });
    const cost = el("input", { type: "number", min: 0, class: "num", placeholder: "$", "aria-label": "Cost" });
    const flightRow = el("div", { class: "capture-row" }, from, to, flightNo, el("label", { class: "due" }, "Leaves ", departs));
    const sync = () => {
      flightRow.hidden = kind.value !== "flight";
      end.parentElement.hidden = kind.value !== "stay";
    };
    kind.addEventListener("change", sync);
    form.append(title, el("div", { class: "capture-row" }, kind, el("label", { class: "due" }, "On ", start), el("label", { class: "due" }, "until ", end), who, payer, cost), flightRow,
      el("div", { class: "capture-row" }, el("button", { type: "submit", class: "btn primary", text: "Add" })));
    sync();
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const body = { kind: kind.value, title: title.value, starts_on: start.value || null, ends_on: kind.value === "stay" ? end.value || null : null, travelers: who.value.split(","), payer: payer.value, cost: cost.value ? Number(cost.value) : null };
      if (kind.value === "flight") Object.assign(body, { from_airports: from.value, to_airports: to.value, flight: flightNo.value, departs: departs.value });
      tripCall("POST", `/trips/${trip.id}/modules`, body, "Added.");
    });
    return form;
  }

  function tripHeader(trip) {
    const wrap = el("div", { class: "section" });
    const show = () => {
      const meta = el("div", { class: "meta" });
      meta.append(el("span", { class: "chip soon", text: STATUS_TEXT[trip.status] }), el("span", { class: "chip", text: tripDays(trip) }));
      meta.append(el("span", { class: "chip owner", text: trip.travelers.map((t) => PEOPLE_TEXT[t]).join(" and ") }));
      if (trip.moneyhub_goal_id) meta.append(el("a", { class: "chip", href: goalUrl(), target: "_blank", rel: "noopener", text: `money-hub goal #${trip.moneyhub_goal_id}` }));
      const copy = el("button", { type: "button", class: "btn small", text: "Copy text for family", onclick: copySummary });
      wrap.replaceChildren(
        el("p", {}, el("a", { href: "#trips", text: "← All trips" })),
        el("div", { class: "item" }, el("button", { type: "button", class: "item-body", onclick: edit }, el("span", { class: "title", text: trip.title }), trip.places ? el("span", { class: "kicker", text: trip.places }) : null, trip.notes ? el("span", { class: "notes full", text: trip.notes }) : null, meta), el("div", { class: "actions" }, copy)),
      );
    };
    const copySummary = async () => {
      try {
        const res = await fetch(`${API}/trips/${trip.id}/summary`);
        const text = await res.text();
        if (!res.ok) throw new Error("Couldn't make the summary.");
        if (navigator.share) await navigator.share({ text });
        else {
          await navigator.clipboard.writeText(text);
          toast("Copied. Paste it into a text.");
        }
      } catch (err) {
        if (err.name !== "AbortError") toast(err.message);
      }
    };
    const edit = () => {
      const form = el("form", { class: "editor" });
      const title = el("input", { name: "title", value: trip.title, maxlength: 200, required: true, "aria-label": "Name" });
      const status = el("select", { "aria-label": "Status" }, ...Object.entries(STATUS_TEXT).map(([k, v]) => el("option", { value: k, text: v })));
      status.value = trip.status;
      const window_ = el("input", { value: trip.window, placeholder: "When, roughly", "aria-label": "Window" });
      const places = el("input", { value: trip.places, placeholder: "Places, in order: Austin, San Marcos", "aria-label": "Places" });
      const start = el("input", { type: "date", value: trip.starts_on || "", "aria-label": "First day" });
      const end = el("input", { type: "date", value: trip.ends_on || "", "aria-label": "Last day" });
      const who = travelerPick(trip.travelers);
      const goal = el("input", { type: "number", min: 1, value: trip.moneyhub_goal_id || "", class: "num", "aria-label": "money-hub goal number" });
      const notes = el("textarea", { rows: 3, placeholder: "Notes", "aria-label": "Notes" });
      notes.value = trip.notes;
      form.append(title, window_, places, el("div", { class: "capture-row" }, el("label", { class: "due" }, "Status ", status), el("label", { class: "due" }, "From ", start), el("label", { class: "due" }, "to ", end), who),
        el("p", { class: "muted", text: "Set: dates are firm, and the prep checklist goes up." }), el("label", { class: "due" }, "money-hub goal # ", goal), notes,
        el("div", { class: "editor-row" }, el("button", { type: "submit", class: "btn primary", text: "Save" }), el("button", { type: "button", class: "btn ghost", text: "Cancel", onclick: show })));
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        tripCall("PATCH", `/trips/${trip.id}`, { title: title.value, status: status.value, window: window_.value, places: places.value, starts_on: start.value || null, ends_on: end.value || null, travelers: who.value.split(","), notes: notes.value, moneyhub_goal_id: goal.value ? Number(goal.value) : null }, "Saved.");
      });
      wrap.replaceChildren(form);
    };
    show();
    return wrap;
  }

  async function renderTrip(id) {
    const trip = await api("GET", `/trips/${id}`);
    const byId = Object.fromEntries(trip.modules.map((m) => [m.id, m]));
    const parts = [tripHeader(trip)];

    // Flights, a day at a time: the leg, its candidates and backups, and agent options.
    const legDays = [...new Set([...trip.modules.filter((m) => m.kind === "flight" && m.starts_on).map((m) => m.starts_on), ...trip.suggested.filter((s) => s.kind === "flight" && s.starts_on).map((s) => s.starts_on)])].sort();
    if (legDays.length) {
      const wrap = el("div", { class: "section" }, el("h2", { text: "Flights" }));
      for (const day of legDays) {
        const mods = trip.modules.filter((m) => m.kind === "flight" && m.starts_on === day).sort((a, b) => (a.departs || "99").localeCompare(b.departs || "99"));
        const sugg = trip.suggested.filter((s) => s.kind === "flight" && s.starts_on === day).sort((a, b) => (a.departs || "99").localeCompare(b.departs || "99"));
        const made = mods.some((m) => m.state === "booked");
        wrap.append(el("h3", { class: "day", text: `${short(day)}${made ? " · made it" : ""}` }), el("ul", { class: "items" }, ...mods.map((m) => moduleRow(m, trip)), ...sugg.map((s) => suggestedRow(s, trip))));
      }
      parts.push(wrap);
    }

    // Stays, rides and plans, with agent options for them.
    const others = trip.modules.filter((m) => m.kind !== "flight");
    const otherSugg = trip.suggested.filter((s) => s.kind !== "flight");
    parts.push(el("div", { class: "section" }, el("h2", { text: "Stays, rides and plans" }),
      others.length || otherSugg.length ? el("ul", { class: "items" }, ...others.map((m) => moduleRow(m, trip)), ...otherSugg.map((s) => suggestedRow(s, trip))) : el("p", { class: "muted", text: "Nothing yet. Add where you're sleeping and how you get around." })));

    parts.push(addModuleForm(trip));

    // Day by day: what's on, and what's missing.
    if (trip.strip.length) {
      const ul = el("ul", { class: "strip" });
      for (const d of trip.strip) {
        const on = [...d.stays, ...d.modules].map((mid) => byId[mid]).filter(Boolean);
        const meta = el("div", { class: "meta" });
        for (const m of on) meta.append(el("span", { class: m.state === "booked" || m.state === "chosen" ? "chip weight" : "chip", text: m.kind === "flight" ? (m.flight || "Flight to fill") : `${KIND_TEXT[m.kind]}: ${m.title}` }));
        for (const g of d.gaps) meta.append(el("span", { class: "chip late", text: g }));
        ul.append(el("li", {}, el("span", { class: "strip-day", text: short(d.on) }), meta));
      }
      parts.push(el("div", { class: "section" }, el("h2", { text: "Day by day" }), ul));
    }

    // The prep checklist: ordinary Table items, due before you leave.
    if (trip.prep.length) {
      const ul = el("ul", { class: "items" });
      for (const i of trip.prep) {
        const done = i.status === "done";
        const meta = el("div", { class: "meta" });
        if (i.due_on) meta.append(el("span", { class: !done && i.due_on <= TODAY ? "chip soon" : "chip", text: `Due ${short(i.due_on)}` }));
        if (i.owner) meta.append(el("span", { class: "chip owner", text: PEOPLE_TEXT[i.owner] }));
        ul.append(el("li", { class: `item${done ? " is-done" : ""}` }, el("div", { class: "item-body" }, el("span", { class: "title", text: i.title.replace(` · ${trip.title}`, "") }), meta),
          el("div", { class: "actions" }, done ? el("button", { type: "button", class: "btn small", text: "Reopen", onclick: () => tripCall("POST", `/items/${i.id}/triage`, { action: "reopen" }, "Reopened.") }) : el("button", { type: "button", class: "btn small done", text: "Done", onclick: () => tripCall("POST", `/items/${i.id}/triage`, { action: "done" }, "Done.") }))));
      }
      parts.push(el("div", { class: "section" }, el("h2", { text: "Before you go" }), ul));
    } else if (!trip.prep_made) {
      parts.push(el("p", { class: "muted", text: "When the trip is set, a prep checklist goes up here: plants, the key, the sublease, packing." }));
    }
    list.replaceChildren(...parts);
  }

  async function render() {
    for (const a of board.querySelectorAll(".views a")) a.classList.toggle("on", a.dataset.view === view);
    try {
      if (view === "trips") {
        await (tripId ? renderTrip(tripId) : renderTrips());
      } else if (view === "mail") {
        await renderMail();
      } else if (view === "dates") {
        await renderDates();
      } else if (view === "vault") {
        await renderVault();
      } else if (view === "session") {
        const [s, suggested] = await Promise.all([api("GET", "/session"), suggestionsSection()]);
        list.replaceChildren(
          el("p", { class: "lead", text: "Keep or move what's left on today's plate, glance at the week, then clear the inbox." }),
          suggested || "",
          section("On today's plate", s.today, "session-today", "Nothing left for today."),
          section("Due in the next 7 days", s.due_this_week, "session", "Nothing due this week."),
          section("Inbox to triage", s.inbox, "inbox", "Inbox is clear."),
        );
      } else if (view === "review") {
        const r = await api("GET", "/review");
        list.replaceChildren(
          el("p", { class: "lead", text: "Once a week: clear what's overdue, decide on old captures, and look over everything deferred." }),
          section("Overdue", r.overdue, "review", "Nothing overdue."),
          section("In the inbox over a week", r.stale_inbox, "inbox", "No old captures."),
          section("Deferred", r.later, "later", "Nothing deferred."),
          section("Done this week", r.done_this_week, "done", "Nothing finished this week yet."),
        );
      } else {
        const data = await api("GET", `/items?view=${view}`);
        setCounts(data.counts);
        const tools = el("div", { class: "list-tools" });
        if (view !== "done" && data.items.length > 1 && !picking) {
          tools.append(el("button", { type: "button", class: "btn ghost small", text: "Merge duplicates", onclick: startMerge }));
        }
        const title = { today: "Today", inbox: "Inbox", later: "Later", done: "Done in the last 30 days" }[view];
        const suggested = view === "inbox" ? await suggestionsSection() : view === "today" ? await picksSection() : null;
        list.replaceChildren(tools, suggested || "", section(title, data.items, view, EMPTY[view]));
        return;
      }
      const counts = await api("GET", "/items?view=today");
      setCounts(counts.counts);
    } catch (err) {
      list.replaceChildren(el("p", { class: "muted", text: err.message }));
    }
  }

  function fromHash() {
    const wanted = location.hash.slice(1);
    const trip = /^trip-(\d+)$/.exec(wanted);
    tripId = trip && SPACE === "table" ? Number(trip[1]) : null;
    view = tripId ? "trips" : VIEWS.includes(wanted) ? wanted : "today";
    if (picking && ["session", "review", "done", "mail", "dates", "vault", "trips"].includes(view)) stopMerge();
    render();
  }
  window.addEventListener("hashchange", fromHash);

  // --- capture ---------------------------------------------------------------------------

  const capture = document.getElementById("capture");
  capture.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = new FormData(capture);
    const body = { title: form.get("title"), today: form.get("today") === "on", due_on: form.get("due_on") || null };
    try {
      await api("POST", "/items", body);
      capture.reset();
      toast(body.today ? "Added to today." : "Added to the inbox.");
      await render();
    } catch (err) {
      toast(err.message);
    }
  });

  // Hotkeys, like Plate: n to capture, 1-9 to switch views, Escape to back out.
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if (e.key === "Escape") {
      if (picking) stopMerge();
      else if (typing) document.activeElement.blur();
      return;
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "n") {
      e.preventDefault();
      document.getElementById("capture-title").focus();
    }
    if (/^[1-9]$/.test(e.key) && VIEWS[Number(e.key) - 1]) location.hash = VIEWS[Number(e.key) - 1];
  });

  fromHash();
})();
