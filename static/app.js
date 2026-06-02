// Longest Table — frontend

let PARTICIPANTS = [];
let ASSIGNMENTS_LOADED = false;
let EMAIL_DRAFT_MODE = "invite";
let EMAIL_DRAFTS_LOADED = false;

function _text(v) {
  return String(v || "").trim().toLowerCase();
}

function _num(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function compareParticipants(a, b, mode) {
  const tableA = _num(a.table_number, Number.MAX_SAFE_INTEGER);
  const tableB = _num(b.table_number, Number.MAX_SAFE_INTEGER);
  const orderA = _text(a.order_id);
  const orderB = _text(b.order_id);
  const firstA = _text(a.first_name);
  const firstB = _text(b.first_name);
  const lastA = _text(a.last_name);
  const lastB = _text(b.last_name);
  const emailA = _text(a.email);
  const emailB = _text(b.email);
  const capA = a.is_captain ? 1 : 0;
  const capB = b.is_captain ? 1 : 0;

  if (mode === "captains_first" && capA !== capB) return capB - capA;
  if (mode === "email") {
    return emailA.localeCompare(emailB) || lastA.localeCompare(lastB) || firstA.localeCompare(firstB);
  }
  if (mode === "first_last") {
    return firstA.localeCompare(firstB) || lastA.localeCompare(lastB) || orderA.localeCompare(orderB);
  }
  if (mode === "last_first") {
    return lastA.localeCompare(lastB) || firstA.localeCompare(firstB) || orderA.localeCompare(orderB);
  }
  if (mode === "order_name") {
    return orderA.localeCompare(orderB) || lastA.localeCompare(lastB) || firstA.localeCompare(firstB);
  }

  // Default: table number, then order #, then name
  return tableA - tableB || orderA.localeCompare(orderB) || lastA.localeCompare(lastB) || firstA.localeCompare(firstB);
}

// -------- Tabs
document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    const tabName = btn.dataset.tab;
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + tabName).classList.add("active");
    if (tabName === "tables") {
      loadAssignments();
    }
    if (tabName === "admin") {
      loadAdminOverview();
    }
  });
});

// -------- Participants
async function loadParticipants() {
  const res = await fetch("/api/participants");
  PARTICIPANTS = await res.json();
  renderParticipants();
}

function renderParticipants() {
  const q = document.getElementById("search").value.toLowerCase().trim();
  const tableFilter = document.getElementById("filter-table").value.trim();
  const captainsOnly = document.getElementById("filter-captains").checked;
  const missingEmailOnly = document.getElementById("filter-missing-email").checked;
  const sortMode = document.getElementById("participant-sort").value || "table_order";
  const tbody = document.querySelector("#participants-table tbody");
  tbody.innerHTML = "";
  const filtered = PARTICIPANTS.filter(p => {
    const matchesSearch = !q || (
      (p.first_name || "").toLowerCase().includes(q) ||
      (p.last_name || "").toLowerCase().includes(q) ||
      (p.email || "").toLowerCase().includes(q) ||
      (p.order_id || "").toLowerCase().includes(q) ||
      (p.buyer_last || "").toLowerCase().includes(q)
    );
    const matchesTable = !tableFilter || String(p.table_number || "") === tableFilter;
    const matchesCaptain = !captainsOnly || !!p.is_captain;
    const matchesMissingEmail = !missingEmailOnly || !(p.email || "").trim();
    return matchesSearch && matchesTable && matchesCaptain && matchesMissingEmail;
  });
  filtered.sort((a, b) => compareParticipants(a, b, sortMode));
  document.getElementById("participant-count").textContent =
    `${filtered.length} of ${PARTICIPANTS.length} people`;

  for (const p of filtered) {
    const tr = document.createElement("tr");
    tr.dataset.id = p.id;
    tr.innerHTML = `
      <td contenteditable="true" data-field="order_id">${escape(p.order_id)}</td>
      <td contenteditable="true" data-field="table_number">${p.table_number || ""}</td>
      <td contenteditable="true" data-field="first_name">${escape(p.first_name)}</td>
      <td contenteditable="true" data-field="last_name">${escape(p.last_name)}</td>
      <td contenteditable="true" data-field="email">${escape(p.email)}</td>
      <td contenteditable="true" data-field="phone">${escape(p.phone)}</td>
      <td contenteditable="true" data-field="age_range">${escape(p.age_range)}</td>
      <td class="captain-cell">
        <input type="checkbox" data-field="is_captain" ${p.is_captain ? "checked" : ""} />
      </td>
      <td>${escape(p.buyer_first)} ${escape(p.buyer_last)}</td>
      <td><button class="del-btn" title="Delete">✕</button></td>
    `;
    tbody.appendChild(tr);

    tr.querySelectorAll("td[contenteditable]").forEach(cell => {
      cell.addEventListener("blur", () => savePerson(p.id, cell.dataset.field, cell.textContent.trim()));
    });
    tr.querySelector('input[type=checkbox]').addEventListener("change", (e) => {
      savePerson(p.id, "is_captain", e.target.checked);
    });
    tr.querySelector(".del-btn").addEventListener("click", async () => {
      if (!confirm(`Delete ${p.first_name} ${p.last_name}?`)) return;
      await fetch(`/api/participants/${p.id}`, { method: "DELETE" });
      await loadParticipants();
    });
  }
}

async function savePerson(id, field, value) {
  const statusEl = document.getElementById("participants-status");
  if (statusEl) statusEl.textContent = "Saving...";

  const body = {};
  body[field] = value;
  const res = await fetch(`/api/participants/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    if (statusEl) statusEl.textContent = "Save failed";
    setTimeout(() => {
      if (statusEl) statusEl.textContent = "";
    }, 2000);
    await loadParticipants();
    return;
  }

  const p = PARTICIPANTS.find(x => x.id === id);
  if (p) p[field] = (field === "is_captain") ? (value ? 1 : 0) : value;

  if (ASSIGNMENTS_LOADED) {
    await loadAssignments();
    await loadParticipants();
    if (statusEl) statusEl.textContent = "Saved. Generate again to refresh tables";
  } else if (statusEl) {
    statusEl.textContent = "Saved";
  }

  setTimeout(() => {
    if (statusEl) statusEl.textContent = "";
  }, 2000);
}

document.getElementById("search").addEventListener("input", renderParticipants);
document.getElementById("filter-table").addEventListener("input", renderParticipants);
document.getElementById("filter-captains").addEventListener("change", renderParticipants);
document.getElementById("filter-missing-email").addEventListener("change", renderParticipants);
document.getElementById("participant-sort").addEventListener("change", renderParticipants);

document.getElementById("btn-add").addEventListener("click", async () => {
  const order_id = prompt("Order # (groups with same number sit together):");
  if (!order_id) return;
  const first_name = prompt("First name:") || "";
  const last_name = prompt("Last name:") || "";
  await fetch("/api/participants", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ order_id, first_name, last_name }),
  });
  await loadParticipants();
});

// -------- Rules
async function loadRules() {
  const res = await fetch("/api/rules");
  const data = await res.json();
  const form = document.getElementById("rules-form");
  for (const [k, v] of Object.entries(data.rules)) {
    const el = form.elements[k];
    if (!el) continue;
    if (el.type === "checkbox") el.checked = !!v;
    else el.value = v;
  }
  form.elements["table_count"].value = data.table_count || 0;
}

document.getElementById("rules-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const rules = {};
  const keys = ["seats_per_table", "min_singles_per_table",
    "min_children_per_table", "min_teens_per_table",
    "one_captain_per_table", "keep_groups_together",
    "split_oversize_groups", "spread_evenly", "spread_seniors"];
  for (const k of keys) {
    const el = form.elements[k];
    if (!el) continue;
    rules[k] = el.type === "checkbox" ? el.checked : el.value;
  }
  const table_count = form.elements["table_count"].value;
  const res = await fetch("/api/rules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rules, table_count }),
  });
  if (res.ok) {
    document.getElementById("rules-status").textContent = "✓ Saved";
    setTimeout(() => document.getElementById("rules-status").textContent = "", 2000);
  }
});

// -------- Tables
document.getElementById("btn-generate").addEventListener("click", generateAssignments);

async function loadAssignments() {
  const res = await fetch("/api/assign");
  const data = await res.json();
  ASSIGNMENTS_LOADED = !!((data.tables || []).length || (data.summary && data.summary.is_locked));
  renderAssignments(data);
}

async function generateAssignments() {
  const res = await fetch("/api/assign", { method: "POST" });
  const data = await res.json();
  ASSIGNMENTS_LOADED = !!(data.tables || []).length;
  renderAssignments(data);
  await loadParticipants();
}

document.getElementById("btn-lock").addEventListener("click", async () => {
  if (!confirm("Are you sure you want to LOCK assignments? This will save the current layout to the database. Automatic reshuffling will stop.")) return;
  const res = await fetch("/api/assign", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "lock" }),
  });
  if (res.ok) generateAssignments();
});

document.getElementById("btn-unlock").addEventListener("click", async () => {
  if (!confirm("Unlock assignments? This will allow the algorithm to re-calculate everything from scratch based on rules.")) return;
  const res = await fetch("/api/assign", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "unlock" }),
  });
  if (res.ok) generateAssignments();
});

function renderAssignments(data) {
  ASSIGNMENTS_LOADED = !!((data.tables || []).length || (data.summary && data.summary.is_locked));
  const grid = document.getElementById("tables-grid");
  const warns = document.getElementById("warnings");
  const s = data.summary;
  
  // Show/Hide Lock buttons
  const isLocked = !!s.is_locked;
  document.getElementById("btn-lock").style.display = isLocked || !(data.tables || []).length ? "none" : "inline-block";
  document.getElementById("btn-unlock").style.display = isLocked ? "inline-block" : "none";
  document.getElementById("lock-notice").style.display = isLocked ? "block" : "none";
  document.getElementById("btn-generate").style.display = isLocked ? "none" : "inline-block";

  const singlesInfo = s.num_singles > 0
    ? ` • ${s.num_singles} singles in ${s.num_singles_tables} singles-table(s)`
    : "";
  document.getElementById("summary").textContent =
    `${s.total_people} people • ${s.num_tables} tables • ${s.total_capacity} seats • ${s.captains_available} captains${singlesInfo}`;

  warns.innerHTML = "";
  for (const w of data.warnings || []) {
    const div = document.createElement("div");
    div.className = "warning" + (w.startsWith("STRONG") ? " strong" : "");
    div.textContent = w;
    warns.appendChild(div);
  }

  // Ensure tokens exist for all tables, then fetch signup progress
  Promise.all([
    fetch("/api/ensure-tokens", { method: "POST" }).then(r => r.json()),
    fetch("/api/signup-progress").then(r => r.json()),
  ]).then(([tokens, progress]) => {
    renderTableCards(data, tokens, progress);
  }).catch(() => renderTableCards(data, {}, { totals_by_table: {}, filled_by_table: {} }));
}

function renderTableCards(data, tokens, progress) {
  const grid = document.getElementById("tables-grid");
  grid.innerHTML = "";
  const totalsByTable = progress.totals_by_table || {};
  for (const t of data.tables || []) {
    const card = document.createElement("div");
    card.className = "table-card" + (t.captain ? "" : " no-captain") + (t.is_singles_table ? " singles-table" : "");
    const captainName = t.captain
      ? `<strong>${escape(t.captain.first_name)} ${escape(t.captain.last_name)}</strong>`
      : `<em style="color:#c62828">No captain</em>`;
    const badge = t.is_singles_table ? '<span class="badge">Singles Table</span>' : '';
    const token = tokens[t.number];
    const filled = progress.filled_by_table[t.number] || 0;
    const total = totalsByTable[t.number] || t.people.length || 0;
    const pct = total > 0 ? Math.round((filled / total) * 100) : 0;
    const signupLink = token ? `/signup/${token}` : "";
    const progressBar = total > 0 ? `
      <div class="progress-wrap" title="${filled} of ${total} food items claimed">
        <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
        <span class="progress-label">${filled}/${total} signed up</span>
        ${signupLink ? `<a href="${signupLink}" target="_blank" class="signup-link">Open signup →</a>` : ''}
      </div>` : '';
    card.innerHTML = `
      <h3>Table ${t.number} ${badge}</h3>
      <div class="captain-label">Captain: ${captainName}</div>
      <div class="headcount">${t.people.length} people</div>
      ${progressBar}
      ${t.parties.map(party => `
        <div class="party">
          <div class="party-header">
            Order #${escape(party.order_id)} · ${party.people.length} ${party.people.length === 1 ? "person" : "people"}
            ${party.is_split ? '<span class="party-split">(split)</span>' : ''}
          </div>
          <ul>
            ${party.people.map(p => `
              <li class="${p.is_captain ? 'captain-person' : ''}">
                ${escape(p.first_name)} ${escape(p.last_name)}
                <span class="muted">· ${escape(p.age_range || '')}</span>
              </li>
            `).join("")}
          </ul>
        </div>
      `).join("")}
    `;
    grid.appendChild(card);
  }
}

function escape(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// -------- Init
loadParticipants();
loadRules();
loadAssignments();
loadMenu();
loadSettings();
loadEmailTemplates();

// -------- Menu
async function loadMenu() {
  const res = await fetch("/api/menu");
  const cats = await res.json();
  const tbody = document.querySelector("#menu-table tbody");
  tbody.innerHTML = "";
  for (const c of cats) {
    const tr = document.createElement("tr");
    tr.dataset.id = c.id;
    tr.innerHTML = `
      <td contenteditable="true" data-field="name">${escape(c.name)}</td>
      <td contenteditable="true" data-field="per_table_count">${c.per_table_count}</td>
      <td contenteditable="true" data-field="notes">${escape(c.notes || "")}</td>
      <td contenteditable="true" data-field="sort_order">${c.sort_order}</td>
      <td><button class="del-btn" title="Delete">✕</button></td>
    `;
    tbody.appendChild(tr);
    tr.querySelectorAll("td[contenteditable]").forEach(cell => {
      cell.addEventListener("blur", async () => {
        const body = {};
        body[cell.dataset.field] = cell.textContent.trim();
        await fetch(`/api/menu/${c.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        setStatus("menu-status", "✓ Saved");
      });
    });
    tr.querySelector(".del-btn").addEventListener("click", async () => {
      if (!confirm(`Delete category "${c.name}"? Signups for this category will also be removed.`)) return;
      await fetch(`/api/menu/${c.id}`, { method: "DELETE" });
      loadMenu();
    });
  }
}

document.getElementById("btn-menu-add").addEventListener("click", async () => {
  const name = prompt("Category name (e.g. Salad):");
  if (!name) return;
  const count = parseInt(prompt("How many needed per table?", "1"), 10) || 1;
  await fetch("/api/menu", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, per_table_count: count }),
  });
  loadMenu();
});

// -------- Admin overview
async function loadAdminOverview() {
  const statusEl = document.getElementById("admin-status");
  statusEl.textContent = "Loading...";
  const res = await fetch("/api/admin/food-overview");
  if (!res.ok) {
    statusEl.textContent = "Failed to load";
    return;
  }
  const data = await res.json();
  renderAdminOverview(data);
  statusEl.textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

function renderAdminOverview(data) {
  const summary = data.summary || {};
  document.getElementById("admin-summary").textContent =
    `${summary.table_count || 0} tables • ${summary.total_claimed || 0}/${summary.total_target || 0} claimed • ${summary.total_missing_items || 0} category slots missing`;

  const grid = document.getElementById("admin-grid");
  grid.innerHTML = "";

  for (const t of data.tables || []) {
    const riskClass = t.missing_items > 0 ? " admin-at-risk" : " admin-good";
    const card = document.createElement("div");
    card.className = "admin-card" + riskClass;
    card.innerHTML = `
      <div class="admin-head">
        <h3>Table ${t.table_number}</h3>
        <span class="muted">${t.claimed_total}/${t.target_total} people signed up (${t.completion_pct}%)</span>
      </div>
      <div class="muted">Captain: ${escape(t.captain_name || "(none)")}</div>
      <div class="muted">Signup link: <a href="${escape(t.signup_link)}" target="_blank">Open</a></div>
      <div class="admin-cats">
        ${(t.categories || []).map(c => `
          <div class="admin-cat">
            <div class="admin-cat-head">
              <strong>${escape(c.name)}</strong>
              <span class="muted">${c.claimed}/${c.recommended}${c.remaining > 0 ? ` • ${c.remaining} missing` : ""}</span>
            </div>
            ${c.notes ? `<div class="muted">${escape(c.notes)}</div>` : ""}
            <ul>
              ${(c.claims || []).map(cl => `
                <li>
                  ${escape(cl.person_name || "")}${cl.item_description ? ` — ${escape(cl.item_description)}` : ""}
                </li>
              `).join("") || '<li class="muted">No signups yet</li>'}
            </ul>
          </div>
        `).join("")}
      </div>
    `;
    grid.appendChild(card);
  }
}

document.getElementById("btn-admin-refresh").addEventListener("click", loadAdminOverview);

// -------- Settings
async function loadSettings() {
  const res = await fetch("/api/settings");
  const s = await res.json();
  const form = document.getElementById("settings-form");
  for (const [k, v] of Object.entries(s)) {
    if (form.elements[k]) form.elements[k].value = v || "";
  }
}

document.getElementById("settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const body = {};
  for (const k of ["event_name", "event_date", "event_time", "event_location", "organizer_name", "organizer_email", "app_base_url"]) {
    body[k] = form.elements[k].value;
  }
  const res = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.ok) {
    setStatus("settings-status", "✓ Saved");
    loadEmailTemplates(); // Refresh templates since logistics might have changed
  }
});

// -------- Email templates (per mode)
const EMAIL_MODES = ["invite", "reminder", "dayof"];
const EMAIL_MODE_LABELS = { invite: "Invite", reminder: "Reminder", dayof: "Day Of" };
const EMAIL_DRAFTS_LOADED_BY_MODE = { invite: false, reminder: false, dayof: false };

async function loadEmailTemplates() {
  const res = await fetch("/api/email-templates");
  const tpls = await res.json();
  const labels = { 1: "1. Organizer → Table Captain", 2: "2. Table Captain → Guests" };
  // Group by mode.
  const byMode = { invite: [], reminder: [], dayof: [] };
  for (const t of tpls) {
    if (byMode[t.mode]) byMode[t.mode].push(t);
  }
  for (const mode of EMAIL_MODES) {
    const wrap = document.querySelector(`.email-templates[data-mode="${mode}"]`);
    if (!wrap) continue;
    wrap.innerHTML = "";
    for (const t of byMode[mode].sort((a, b) => a.id - b.id)) {
      const div = document.createElement("div");
      div.className = "email-template-editor";
      div.innerHTML = `
        <h3>${labels[t.id] || "Template " + t.id}</h3>
        <label>Subject
          <input type="text" class="tpl-subject" value="${escape(t.subject)}" />
        </label>
        <label>Body
          <textarea class="tpl-body" rows="10">${escape(t.body)}</textarea>
        </label>
        <div class="actions">
          <button class="tpl-save">Save Template</button>
          <span class="tpl-status muted"></span>
        </div>
      `;
      wrap.appendChild(div);
      div.querySelector(".tpl-save").addEventListener("click", async () => {
        const subject = div.querySelector(".tpl-subject").value;
        const body = div.querySelector(".tpl-body").value;
        const res = await fetch(`/api/email-templates/${mode}/${t.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ subject, body }),
        });
        if (res.ok) div.querySelector(".tpl-status").textContent = "✓ Saved";
        setTimeout(() => div.querySelector(".tpl-status").textContent = "", 2000);
      });
    }
  }
}

// -------- Email drafts (per-table, per mode)
document.querySelectorAll(".btn-generate-drafts").forEach(btn => {
  btn.addEventListener("click", async () => {
    await generateEmailDrafts(btn.dataset.mode);
  });
});

async function generateEmailDrafts(mode) {
  const statusEl = document.querySelector(`.drafts-status[data-mode="${mode}"]`);
  if (statusEl) statusEl.textContent = "Generating...";
  const res = await fetch(`/api/email-drafts?mode=${encodeURIComponent(mode)}`);
  const drafts = await res.json();
  renderDrafts(mode, drafts);
  EMAIL_DRAFTS_LOADED_BY_MODE[mode] = drafts.length > 0;
  if (statusEl) statusEl.textContent = `✓ ${drafts.length} ${EMAIL_MODE_LABELS[mode]} drafts generated`;
}

function renderDrafts(mode, drafts) {
  const wrap = document.querySelector(`.drafts-list[data-mode="${mode}"]`);
  if (!wrap) return;
  wrap.innerHTML = "";
  const modeLabel = EMAIL_MODE_LABELS[mode] || mode;
  for (const d of drafts) {
    const div = document.createElement("div");
    div.className = "draft-card" + (d.is_singles_table ? " singles-table" : "");
    div.innerHTML = `
      <h3>Table ${d.table_number}${d.is_singles_table ? ' <span class="badge">Singles</span>' : ''} — Captain: ${escape(d.captain_name || "(none)")}</h3>
      <p class="muted">Signup link: <a href="${escape(d.signup_link)}" target="_blank">${escape(d.signup_link)}</a></p>

      <div class="draft-block">
        <h4>① Organizer → Captain (${modeLabel}) <span class="muted">(to: ${escape(d.organizer_to_captain.to || "no captain email")})</span></h4>
        <label>Subject <input type="text" class="draft-subj-1" value="${escape(d.organizer_to_captain.subject)}" /></label>
        <label>Body <textarea class="draft-body-1" rows="10">${escape(d.organizer_to_captain.body)}</textarea></label>
        <div class="actions">
          <button class="btn-copy" data-target="1">Copy Body</button>
          <button class="btn-mailto" data-target="1">Open in Email</button>
          <button class="btn-gmail" data-target="1">Open in Gmail</button>
        </div>
      </div>

      <div class="draft-block">
        <h4>② Captain → Guests (${modeLabel}) <span class="muted">(opens to: ${escape(d.captain_to_guests.to || "no captain email")})</span></h4>
        <label>Guest email list
          <textarea class="draft-recips-2" rows="3" readonly>${escape(d.guest_email_csv || "")}</textarea>
        </label>
        <label>Subject <input type="text" class="draft-subj-2" value="${escape(d.captain_to_guests.subject)}" /></label>
        <label>Body <textarea class="draft-body-2" rows="12">${escape(d.captain_to_guests.body)}</textarea></label>
        <div class="actions">
          <button class="btn-copy-recipients">Copy Guest Emails</button>
          <button class="btn-copy" data-target="2">Copy Body</button>
          <button class="btn-mailto" data-target="2">Open in Email</button>
          <button class="btn-gmail" data-target="2">Open in Gmail</button>
        </div>
      </div>
    `;
    wrap.appendChild(div);

    div.querySelectorAll(".btn-copy").forEach(b => {
      b.addEventListener("click", () => {
        const t = b.dataset.target;
        const body = div.querySelector(`.draft-body-${t}`).value;
        navigator.clipboard.writeText(body).then(() => {
          b.textContent = "✓ Copied";
          setTimeout(() => b.textContent = "Copy Body", 1500);
        });
      });
    });
    div.querySelectorAll(".btn-copy-recipients").forEach(b => {
      b.addEventListener("click", () => {
        const recipients = div.querySelector(".draft-recips-2").value;
        navigator.clipboard.writeText(recipients).then(() => {
          b.textContent = "✓ Copied";
          setTimeout(() => b.textContent = "Copy Guest Emails", 1500);
        });
      });
    });
    div.querySelectorAll(".btn-mailto").forEach(b => {
      b.addEventListener("click", () => {
        const t = b.dataset.target;
        const to = (t === "1" ? d.organizer_to_captain.to : d.captain_to_guests.to) || "";
        const subj = div.querySelector(`.draft-subj-${t}`).value;
        const body = div.querySelector(`.draft-body-${t}`).value;
        const url = `mailto:${encodeURIComponent(to)}?subject=${encodeURIComponent(subj)}&body=${encodeURIComponent(body)}`;
        window.location.href = url;
      });
    });
    div.querySelectorAll(".btn-gmail").forEach(b => {
      b.addEventListener("click", () => {
        const t = b.dataset.target;
        const to = (t === "1" ? d.organizer_to_captain.to : d.captain_to_guests.to) || "";
        const subj = div.querySelector(`.draft-subj-${t}`).value;
        const body = div.querySelector(`.draft-body-${t}`).value;
        // Gmail web compose. Use ?authuser=0 so it works even if multiple Google
        // accounts are signed in. fs=1 = full-screen compose.
        const url = `https://mail.google.com/mail/?view=cm&fs=1`
          + `&to=${encodeURIComponent(to)}`
          + `&su=${encodeURIComponent(subj)}`
          + `&body=${encodeURIComponent(body)}`;
        window.open(url, "_blank", "noopener,noreferrer");
      });
    });
  }
}

document.querySelectorAll(".email-mode-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const mode = btn.dataset.mode;
    document.querySelectorAll(".email-mode-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    document.querySelectorAll(".email-mode-panel").forEach(p => {
      p.classList.toggle("active", p.dataset.mode === mode);
    });
    EMAIL_DRAFT_MODE = mode;
  });
});

function setStatus(id, text) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  setTimeout(() => { el.textContent = ""; }, 2000);
}
