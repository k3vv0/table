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
  const VIEWS = ["today", "inbox", "later", "done", "session", "review", "dates", ...(SYNCED ? ["mail"] : [])];
  const list = document.getElementById("list");
  const statusEl = document.getElementById("status");
  const mergeBar = document.getElementById("merge-bar");
  const mergeGo = document.getElementById("merge-go");
  const mergeCount = document.getElementById("merge-count");

  let view = "today";
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

  // Which buttons a row offers depends on where the item sits right now.
  function actionsFor(item, context) {
    const out = [];
    const btn = (text, fn, cls = "btn small") => el("button", { type: "button", class: cls, text, onclick: fn });
    if (item.status === "done") return [btn("Reopen", () => triage(item, "reopen"))];
    const onToday = context === "today" || context === "session-today";
    if (context === "session-today") out.push(btn("Keep", () => toast("Kept for today.")));
    if (!onToday) out.push(btn("Today", () => triage(item, "today")));
    if (onToday) out.push(btn("Tomorrow", () => triage(item, "later", addDays(1))));
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
        toast(action === "dismiss" ? "Dismissed." : s.kind === "date" ? "Date added." : body?.today ? "Accepted, on today's plate." : "Accepted.");
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
    const actions = [btn("Accept", () => decide("accept", {}), "btn small primary")];
    if (s.kind === "item") actions.push(btn("Accept for today", () => decide("accept", { today: true })));
    actions.push(btn("Dismiss", () => decide("dismiss"), "btn small ghost"));
    return el(
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
  }

  async function suggestionsSection(only) {
    let { suggestions } = await api("GET", "/suggestions");
    if (only) suggestions = suggestions.filter((s) => s.kind === only);
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

  async function render() {
    for (const a of board.querySelectorAll(".views a")) a.classList.toggle("on", a.dataset.view === view);
    try {
      if (view === "mail") {
        await renderMail();
      } else if (view === "dates") {
        await renderDates();
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
        const suggested = view === "inbox" ? await suggestionsSection() : null;
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
    view = VIEWS.includes(wanted) ? wanted : "today";
    if (picking && ["session", "review", "done", "mail", "dates"].includes(view)) stopMerge();
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

  // Hotkeys, like Plate: n to capture, 1-8 to switch views, Escape to back out.
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
    if (/^[1-8]$/.test(e.key) && VIEWS[Number(e.key) - 1]) location.hash = VIEWS[Number(e.key) - 1];
  });

  fromHash();
})();
