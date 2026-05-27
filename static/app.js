let emails = [];

// ── API helper ──────────────────────────────────────────────────────────────

async function api(method, path, body = null) {
  const opts = { method, headers: {} };
  if (body) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch("/api" + path, opts);
  if (res.status === 401) { showSetup(false); throw new Error("Not authenticated"); }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Request failed");
  }
  return res.json();
}

// ── Init ─────────────────────────────────────────────────────────────────────

async function init() {
  const params = new URLSearchParams(window.location.search);
  if (params.has("connected")) window.history.replaceState({}, "", "/");

  try {
    const status = await api("GET", "/auth/status");
    if (!status.has_credentials) {
      showSetup(true);
    } else if (status.authenticated) {
      renderAccountSwitcher(status.accounts, status.current_account);
      await showDashboard();
    } else {
      showSetup(false);
    }
  } catch (_) {
    showSetup(false);
  }
}

// ── Auth ─────────────────────────────────────────────────────────────────────

function showSetup(missingCredentials) {
  document.getElementById("setup-panel").classList.remove("hidden");
  document.getElementById("dashboard").classList.add("hidden");
  document.getElementById("account-switcher").classList.add("hidden");

  const alert = document.getElementById("no-creds-alert");
  const btn = document.getElementById("connect-btn");
  alert.classList.toggle("hidden", !missingCredentials);
  btn.disabled = !!missingCredentials;
}

async function showDashboard() {
  document.getElementById("setup-panel").classList.add("hidden");
  document.getElementById("dashboard").classList.remove("hidden");
  document.getElementById("account-switcher").classList.remove("hidden");
  await Promise.all([loadEmails(), refreshStats()]);
}

function connectGmail() {
  window.location.href = "/api/auth/login";
}

function addAccount() {
  closeAccountMenu();
  window.location.href = "/api/auth/add-account";
}

async function disconnect() {
  closeAccountMenu();
  if (!confirm("Disconnect this account? Unsubscribe history for it will be kept.")) return;
  const result = await api("DELETE", "/auth/logout");
  if (result.next_account) {
    const status = await api("GET", "/auth/status");
    renderAccountSwitcher(status.accounts, status.current_account);
    await showDashboard();
  } else {
    showSetup(false);
  }
}

// ── Account switcher ─────────────────────────────────────────────────────────

function renderAccountSwitcher(accounts, current) {
  document.getElementById("current-account-label").textContent = current || "";
  const list = document.getElementById("account-menu-list");
  list.innerHTML = accounts.map(email => {
    const active = email === current ? " active" : "";
    return `<button class="account-menu-item${active}" onclick="switchAccount('${esc(email)}')">${esc(email)}</button>`;
  }).join("");
}

async function switchAccount(email) {
  closeAccountMenu();
  await api("POST", "/auth/switch/" + encodeURIComponent(email));
  document.getElementById("current-account-label").textContent = email;
  const status = await api("GET", "/auth/status");
  renderAccountSwitcher(status.accounts, status.current_account);
  emails = [];
  renderTable();
  await Promise.all([loadEmails(), refreshStats()]);
}

function toggleAccountMenu() {
  document.getElementById("account-menu").classList.toggle("hidden");
}

function closeAccountMenu() {
  document.getElementById("account-menu").classList.add("hidden");
}

document.addEventListener("click", e => {
  const switcher = document.getElementById("account-switcher");
  if (switcher && !switcher.contains(e.target)) closeAccountMenu();
});

// ── Data ─────────────────────────────────────────────────────────────────────

async function loadEmails() {
  const data = await api("GET", "/emails");
  emails = data.emails;
  renderTable();
}

async function refreshStats() {
  const s = await api("GET", "/stats");
  document.getElementById("stat-total").textContent   = s.total   ?? 0;
  document.getElementById("stat-success").textContent = s.success ?? 0;
  document.getElementById("stat-pending").textContent = s.pending ?? 0;
  document.getElementById("stat-failed").textContent  = s.failed  ?? 0;
  document.getElementById("unsub-all-btn").classList.toggle("hidden", (s.pending ?? 0) === 0);
}

// ── Scan ─────────────────────────────────────────────────────────────────────

async function scan() {
  const btn = document.getElementById("scan-btn");
  const msg = document.getElementById("scan-msg");

  btn.disabled = true;
  btn.innerHTML = '<div class="spinner"></div> Scanning…';
  msg.textContent = "Searching your inbox — this may take a few seconds…";

  try {
    const result = await api("POST", "/scan");
    const parts = [];
    if (result.found > 0) {
      parts.push(`Found ${result.found} new sender${result.found !== 1 ? "s" : ""} (${result.total} total tracked)`);
    } else {
      parts.push(`No new senders found. ${result.total} total tracked.`);
    }
    if (result.body_link_found > 0) {
      parts.push(`${result.body_link_found} via body-link parsing`);
    }
    if (result.bounces_reconciled > 0) {
      parts.push(`${result.bounces_reconciled} prior unsubscribe${result.bounces_reconciled !== 1 ? "s" : ""} flagged as bounced`);
    }
    msg.textContent = parts.join(" · ");
    await loadEmails();
    await refreshStats();
  } catch (e) {
    msg.textContent = "Scan failed: " + e.message;
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> Scan Emails`;
  }
}

// ── Unsubscribe ───────────────────────────────────────────────────────────────

async function unsubscribeOne(id) {
  const row = document.getElementById("row-" + id);
  const btn = row && row.querySelector("button");
  if (btn) { btn.disabled = true; btn.textContent = "…"; }

  try {
    const result = await api("POST", "/unsubscribe/" + id);
    const idx = emails.findIndex(e => e.id === id);
    if (idx >= 0) {
      emails[idx].status = result.status;
      emails[idx].error_message = result.error || null;
    }
    renderTable();
    await refreshStats();
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = "Unsubscribe"; }
    alert("Failed: " + e.message);
  }
}

async function unsubscribeAll() {
  const count = emails.filter(e => e.status === "pending").length;
  if (!confirm(`Send unsubscribe requests to ${count} sender${count !== 1 ? "s" : ""}?`)) return;

  const btn = document.getElementById("unsub-all-btn");
  btn.disabled = true;
  btn.textContent = "Processing…";

  try {
    const result = await api("POST", "/unsubscribe-all");
    document.getElementById("scan-msg").textContent =
      `Done — ${result.success} unsubscribed, ${result.failed} failed.`;
    await loadEmails();
    await refreshStats();
  } catch (e) {
    alert("Failed: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Unsubscribe All Pending";
  }
}

// ── Render ────────────────────────────────────────────────────────────────────

function renderTable() {
  const tbody = document.getElementById("email-tbody");
  const table = document.getElementById("email-table");
  const empty = document.getElementById("empty-state");

  if (emails.length === 0) {
    table.classList.add("hidden");
    empty.classList.remove("hidden");
    return;
  }

  empty.classList.add("hidden");
  table.classList.remove("hidden");

  tbody.innerHTML = emails.map(e => {
    const name = esc(e.sender_name || e.sender_email);
    const emailAddr = esc(e.sender_email);
    const subject = esc(e.latest_subject || "");
    const method = e.unsubscribe_method === "mailto" ? "EMAIL"
                 : e.unsubscribe_method === "http_body" ? "BODY LINK"
                 : "HTTP";
    const methodClass = e.unsubscribe_method === "http_body"
                 ? "method-tag method-tag-body" : "method-tag";
    const canAction = e.status === "pending" || e.status === "failed";

    return `<tr id="row-${e.id}">
      <td>
        <div class="sender-name">${name}</div>
        ${e.sender_name ? `<div class="sender-email">${emailAddr}</div>` : ""}
      </td>
      <td><div class="subject" title="${subject}">${subject}</div></td>
      <td><span class="${methodClass}">${method}</span></td>
      <td>${badge(e.status, e.error_message)}</td>
      <td>
        ${canAction ? `<button class="btn btn-ghost btn-sm" onclick="unsubscribeOne(${e.id})">Unsubscribe</button>` : ""}
      </td>
    </tr>`;
  }).join("");
}

function badge(status, error) {
  if (status === "success") return '<span class="badge badge-success">&#10003; Done</span>';
  if (status === "failed")  return `<span class="badge badge-failed" title="${esc(error || "")}">&#10007; Failed</span>`;
  return '<span class="badge badge-pending">&#8987; Pending</span>';
}

function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Boot ─────────────────────────────────────────────────────────────────────

init();
