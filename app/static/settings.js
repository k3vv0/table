// Settings (DESIGN.md §9): push on this device, per-device push choices, and Shortcut
// capture tokens. Text from the server is set with textContent only.
(() => {
  const root = document.getElementById("settings");
  const hasPlate = Boolean(root.dataset.person);

  const el = (tag, props = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else if (v === true) node.setAttribute(k, "");
      else if (v !== false && v != null) node.setAttribute(k, v);
    }
    for (const c of children) if (c) node.append(c);
    return node;
  };

  async function api(method, path, body) {
    const res = await fetch(`/api/me${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 204) return null;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Something went wrong (${res.status}).`);
    return data;
  }

  const when = (iso) => (iso ? new Date(`${iso}Z`).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "never");

  // --- push ------------------------------------------------------------------------------

  const status = document.getElementById("push-status");
  const onBtn = document.getElementById("push-on");
  const testBtn = document.getElementById("push-test");
  const devices = document.getElementById("push-devices");
  const installHint = document.getElementById("push-install");

  const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const isIOS = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

  function deviceName() {
    const ua = navigator.userAgent;
    if (/iPhone/.test(ua)) return "iPhone";
    if (/iPad/.test(ua)) return "iPad";
    if (/Android/.test(ua)) return "Android";
    if (/Windows/.test(ua)) return "Windows browser";
    if (/Mac/.test(ua)) return "Mac browser";
    return "Browser";
  }

  // VAPID public keys come as URL-safe base64; PushManager wants the raw bytes.
  function keyBytes(base64) {
    const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
    return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
  }

  async function currentEndpoint() {
    if (!supported) return null;
    const reg = await navigator.serviceWorker.getRegistration("/");
    const sub = reg && (await reg.pushManager.getSubscription());
    return sub ? sub.endpoint : null;
  }

  async function renderPush() {
    let data;
    try {
      data = await api("GET", "/push");
    } catch (err) {
      status.textContent = err.message;
      return;
    }
    const mine = await currentEndpoint();
    const here = data.devices.find((d) => d.endpoint === mine);
    if (!data.enabled) status.textContent = "Push isn't set up on the server yet (VAPID keys in .env).";
    else if (!supported && isIOS && !standalone) status.textContent = "Open Table from your Home Screen to turn on notifications.";
    else if (!supported) status.textContent = "This browser can't receive push notifications.";
    else if (Notification.permission === "denied") status.textContent = "Notifications are blocked for Table in this browser's settings.";
    else if (here) status.textContent = "This device gets notifications.";
    else status.textContent = "This device doesn't get notifications yet.";
    installHint.hidden = !(isIOS && !standalone);
    onBtn.hidden = !(data.enabled && supported && !here && Notification.permission !== "denied");
    testBtn.hidden = !(data.enabled && data.devices.length);

    devices.replaceChildren(
      ...data.devices.map((d) => {
        const toggle = (field, label) => {
          const box = el("input", { type: "checkbox", checked: d[field] });
          box.addEventListener("change", async () => {
            try {
              await api("PATCH", `/push/${d.id}`, { [field]: box.checked });
            } catch (err) {
              box.checked = !box.checked;
              alertLine(err.message);
            }
          });
          return el("label", {}, box, label);
        };
        return el(
          "li",
          {},
          el("span", { class: "name", text: `${d.device || "Device"}${d.endpoint === mine ? " (this one)" : ""} · last push ${when(d.last_ok_at)}` }),
          toggle("new_items", "New items"),
          toggle("daily", "Daily session reminder"),
          el("button", {
            type: "button",
            class: "btn small ghost",
            text: "Remove",
            onclick: async () => {
              await api("DELETE", `/push/${d.id}`);
              if (d.endpoint === mine) {
                const reg = await navigator.serviceWorker.getRegistration("/");
                const sub = reg && (await reg.pushManager.getSubscription());
                if (sub) await sub.unsubscribe();
              }
              renderPush();
            },
          }),
        );
      }),
    );
    onBtn.dataset.key = data.public_key;
  }

  function alertLine(message) {
    status.textContent = message;
  }

  onBtn.addEventListener("click", async () => {
    try {
      // iOS only allows this from a tap inside the Home Screen app.
      const permission = await Notification.requestPermission();
      if (permission !== "granted") return renderPush();
      const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(onBtn.dataset.key) });
      const json = sub.toJSON();
      await api("POST", "/push", { endpoint: json.endpoint, keys: json.keys, device: deviceName() });
      renderPush();
    } catch (err) {
      alertLine(`Couldn't turn on notifications: ${err.message}`);
    }
  });

  testBtn.addEventListener("click", async () => {
    try {
      const { sent } = await api("POST", "/push/test");
      alertLine(sent ? `Sent to ${sent} device${sent === 1 ? "" : "s"}.` : "Nothing was delivered; try removing and re-adding this device.");
    } catch (err) {
      alertLine(err.message);
    }
  });

  renderPush();

  // --- capture tokens ----------------------------------------------------------------------

  if (!hasPlate) return;
  const form = document.getElementById("token-form");
  const nameInput = document.getElementById("token-name");
  const list = document.getElementById("token-list");
  const fresh = document.getElementById("token-new");
  const freshValue = document.getElementById("token-value");

  async function renderTokens() {
    const tokens = await api("GET", "/capture-tokens");
    list.replaceChildren(
      ...tokens.map((t) =>
        el(
          "li",
          {},
          el("span", { class: "name", text: `${t.name} · made ${when(t.created_at)} · last used ${when(t.last_used_at)}` }),
          el("button", {
            type: "button",
            class: "btn small ghost",
            text: "Revoke",
            onclick: async () => {
              await api("DELETE", `/capture-tokens/${t.id}`);
              renderTokens();
            },
          }),
        ),
      ),
    );
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const made = await api("POST", "/capture-tokens", { name: nameInput.value });
      freshValue.textContent = made.token;
      fresh.hidden = false;
      nameInput.value = "";
      renderTokens();
    } catch (err) {
      freshValue.textContent = err.message;
      fresh.hidden = false;
    }
  });

  document.getElementById("token-copy").addEventListener("click", () => navigator.clipboard?.writeText(freshValue.textContent));

  renderTokens();
})();
