const BASE = "http://127.0.0.1:8765";
const HEALTH_TIMEOUT_MS = 400;

async function fetchWithTimeout(url, options, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

chrome.downloads.onCreated.addListener(async (item) => {
  if (!/^https?:\/\//i.test(item.url)) {
    return;
  }

  let token;
  try {
    const healthRes = await fetchWithTimeout(`${BASE}/ext/health`, { method: "POST" }, HEALTH_TIMEOUT_MS);
    if (!healthRes.ok) {
      return;
    }
    const health = await healthRes.json();
    if (!health.token) {
      return;
    }
    token = health.token;
  } catch {
    return;
  }

  try {
    const flagRes = await fetch(`${BASE}/ext/flag`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Ext-Token": token,
      },
      body: JSON.stringify({ url: item.url }),
    });
    if (flagRes.status !== 202) {
      return;
    }
  } catch {
    return;
  }

  chrome.downloads.cancel(item.id, () => {
    chrome.downloads.erase({ id: item.id });
  });
});
