// Registers the service worker (DESIGN.md §9). A file rather than an inline script, so the
// page's Content-Security-Policy can refuse every inline script.
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
