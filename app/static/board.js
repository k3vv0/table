// The board for the Table and each Plate (DESIGN.md §1): capture, the four views, merging,
// the daily session and the weekly review. Item text is set with textContent only, since
// titles will later come from synced mail.
(() => {
  const board = document.getElementById("board");
  const API = board.dataset.api;
  const SPACE = board.dataset.space;
  const TODAY = board.dataset.today; // YYYY-MM-DD, US Eastern, from the server
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
    const fields = [title, notes, el("label", { class: "due" }, "Due ", due)];
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
      const body = { title: title.value, notes: notes.value, due_on: due.value || null };
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
    if (item.sources.length > 1) meta.append(el("span", { class: "chip", text: `${item.sources.length} sources` }));

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

  async function render() {
    for (const a of board.querySelectorAll(".views a")) a.classList.toggle("on", a.dataset.view === view);
    try {
      if (view === "session") {
        const s = await api("GET", "/session");
        list.replaceChildren(
          el("p", { class: "lead", text: "Keep or move what's left on today's plate, glance at the week, then clear the inbox." }),
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
        list.replaceChildren(tools, section(title, data.items, view, EMPTY[view]));
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
    view = ["today", "inbox", "later", "done", "session", "review"].includes(wanted) ? wanted : "today";
    if (picking && (view === "session" || view === "review" || view === "done")) stopMerge();
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

  // Hotkeys, like Plate: n to capture, 1-6 to switch views, Escape to back out.
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
    const views = ["today", "inbox", "later", "done", "session", "review"];
    if (/^[1-6]$/.test(e.key)) location.hash = views[Number(e.key) - 1];
  });

  fromHash();
})();
