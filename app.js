// CloudLedger front-end (plain JavaScript, no build step).
// Talks to the API Gateway URL configured in config.js.

const API = (window.APP_CONFIG && window.APP_CONFIG.API_URL || "").replace(/\/$/, "");
const CAT_COLORS = {
  Food: "#f97316", Travel: "#0ea5e9", Shopping: "#a855f7", Bills: "#ef4444",
  Education: "#2563eb", Health: "#16a34a", Entertainment: "#eab308", Other: "#64748b",
};

const $ = (sel) => document.querySelector(sel);
const inr = (n) => "₹" + Number(n || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 });

const state = {
  user: localStorage.getItem("cl_user") || "demo",
  month: new Date().toISOString().slice(0, 7),
  items: [],
  summary: null,
};

// ------------------------------------------------------------------ API
async function api(path, options = {}) {
  const res = await fetch(API + path, {
    ...options,
    headers: { "content-type": "application/json", "x-user-id": state.user, ...(options.headers || {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

async function checkHealth() {
  const pill = $("#apiStatus");
  const t0 = performance.now();
  try {
    await api("/health");
    pill.textContent = `API online · ${Math.round(performance.now() - t0)} ms`;
    pill.className = "pill pill-ok";
  } catch {
    pill.textContent = "API unreachable";
    pill.className = "pill pill-bad";
  }
}

// ------------------------------------------------------------------ load + render
async function load() {
  $("#monthLabel").textContent = new Date(state.month + "-01T00:00:00")
    .toLocaleDateString("en-IN", { month: "long", year: "numeric" });
  try {
    const [list, summary] = await Promise.all([
      api(`/expenses?month=${state.month}`),
      api(`/summary?month=${state.month}`),
    ]);
    state.items = list.items;
    state.summary = summary;
    render();
  } catch (e) {
    $("#rows").innerHTML = `<tr><td colspan="6" class="muted">${escapeHtml(e.message)}</td></tr>`;
    toast(e.message, true);
  }
}

function render() {
  const s = state.summary;
  $("#statTotal").textContent = inr(s.total);
  $("#statTotalSub").textContent = s.count ? `across ${Object.keys(s.byCategory).length} categories` : "this month";
  $("#statCount").textContent = s.count;
  $("#statLargest").textContent = s.largest ? inr(s.largest.amount) : "—";
  $("#statLargestSub").textContent = s.largest ? s.largest.title : " ";

  if (s.budget) {
    $("#statLeft").textContent = inr(s.remaining);
    $("#statLeft").style.color = s.remaining < 0 ? "var(--danger)" : "";
    $("#statLeftSub").textContent = `of ${inr(s.budget)} budget`;
    $("#budgetTrack").hidden = false;
    $("#budgetPct").textContent = `${s.budgetUsedPct}%`;
    const fill = $("#budgetFill");
    fill.style.width = Math.min(100, s.budgetUsedPct) + "%";
    fill.className = "bar-fill" + (s.budgetUsedPct >= 100 ? " over" : s.budgetUsedPct >= 80 ? " warn" : "");
  } else {
    $("#statLeft").textContent = "—";
    $("#statLeft").style.color = "";
    $("#statLeftSub").textContent = "no budget set";
    $("#budgetTrack").hidden = true;
  }

  // category breakdown
  const entries = Object.entries(s.byCategory);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  $("#categoryBars").innerHTML = entries.length
    ? entries.map(([cat, v]) => `
        <div class="cat-row">
          <span>${cat}</span>
          <div class="bar"><div class="bar-fill" style="width:${(v / max) * 100}%;background:${CAT_COLORS[cat]}"></div></div>
          <span class="amt">${inr(v)}</span>
        </div>`).join("")
    : `<p class="muted">No expenses yet this month.</p>`;

  // category filter options
  const filter = $("#catFilter");
  const current = filter.value;
  filter.innerHTML = `<option value="">All categories</option>` +
    Object.keys(CAT_COLORS).map((c) => `<option ${c === current ? "selected" : ""}>${c}</option>`).join("");

  renderRows();
}

function filteredItems() {
  const q = $("#search").value.trim().toLowerCase();
  const cat = $("#catFilter").value;
  return state.items.filter((i) =>
    (!cat || i.category === cat) &&
    (!q || i.title.toLowerCase().includes(q) || (i.note || "").toLowerCase().includes(q)));
}

function renderRows() {
  const rows = filteredItems();
  $("#rows").innerHTML = rows.length
    ? rows.map((i) => `
      <tr>
        <td>${new Date(i.date + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short" })}</td>
        <td><strong>${escapeHtml(i.title)}</strong>${i.note ? `<span class="note">${escapeHtml(i.note)}</span>` : ""}</td>
        <td><span class="chip" style="background:${CAT_COLORS[i.category]}1f;color:${CAT_COLORS[i.category]}">${i.category}</span></td>
        <td class="num"><strong>${inr(i.amount)}</strong></td>
        <td>${i.receiptUrl ? `<button class="link" data-receipt="${i.id}">View</button>` : `<span class="muted">—</span>`}</td>
        <td class="num"><button class="btn-del" title="Delete" data-delete="${i.id}">🗑</button></td>
      </tr>`).join("")
    : `<tr><td colspan="6" class="muted">No expenses match.</td></tr>`;
}

// ------------------------------------------------------------------ actions
async function saveExpense(e) {
  e.preventDefault();
  const form = e.target;
  const btn = $("#saveBtn");
  const fd = new FormData(form);
  btn.disabled = true;
  btn.textContent = "Saving…";
  try {
    let receiptKey = null;
    const file = fd.get("receipt");
    if (file && file.size) {
      if (file.size > 5 * 1024 * 1024) throw new Error("Receipt must be under 5 MB");
      btn.textContent = "Uploading receipt to S3…";
      const { key, uploadUrl } = await api("/receipts/upload-url", {
        method: "POST", body: JSON.stringify({ contentType: file.type }),
      });
      const put = await fetch(uploadUrl, { method: "PUT", headers: { "Content-Type": file.type }, body: file });
      if (!put.ok) throw new Error("Receipt upload failed");
      receiptKey = key;
    }
    await api("/expenses", {
      method: "POST",
      body: JSON.stringify({
        title: fd.get("title"), amount: fd.get("amount"), category: fd.get("category"),
        date: fd.get("date"), note: fd.get("note"), receiptKey,
      }),
    });
    const keepDate = fd.get("date");
    form.reset();
    form.date.value = keepDate;
    state.month = keepDate.slice(0, 7);
    toast("Expense saved");
    await load();
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Save expense";
  }
}

async function deleteExpense(id) {
  const item = state.items.find((i) => i.id === id);
  if (!item || !confirm(`Delete "${item.title}" (${inr(item.amount)})?`)) return;
  try {
    await api(`/expenses/${encodeURIComponent(id)}`, { method: "DELETE" });
    toast("Expense deleted");
    await load();
  } catch (err) {
    toast(err.message, true);
  }
}

function showReceipt(id) {
  const item = state.items.find((i) => i.id === id);
  if (!item) return;
  $("#receiptTitle").textContent = `Receipt · ${item.title}`;
  const isPdf = (item.receiptKey || "").endsWith(".pdf");
  $("#receiptBody").innerHTML = isPdf
    ? `<a href="${item.receiptUrl}" target="_blank" rel="noopener">Open PDF receipt</a>`
    : `<img src="${item.receiptUrl}" alt="Receipt for ${escapeHtml(item.title)}" />`;
  $("#receiptDialog").showModal();
}

function exportCsv() {
  const rows = [["Date", "Title", "Category", "Amount", "Note"]]
    .concat(filteredItems().map((i) => [i.date, i.title, i.category, i.amount, i.note || ""]));
  const csv = rows.map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = `cloudledger-${state.user}-${state.month}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function shiftMonth(delta) {
  const [y, m] = state.month.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  state.month = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  load();
}

// ------------------------------------------------------------------ utils
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

let toastTimer;
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), 2800);
}

// ------------------------------------------------------------------ wire up
document.addEventListener("DOMContentLoaded", () => {
  $("#userInput").value = state.user;
  $("#expenseForm").date.value = new Date().toISOString().slice(0, 10);

  $("#expenseForm").addEventListener("submit", saveExpense);
  $("#prevMonth").addEventListener("click", () => shiftMonth(-1));
  $("#nextMonth").addEventListener("click", () => shiftMonth(1));
  $("#search").addEventListener("input", renderRows);
  $("#catFilter").addEventListener("change", renderRows);
  $("#exportBtn").addEventListener("click", exportCsv);

  $("#userForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = $("#userInput").value.trim().toLowerCase();
    if (!/^[a-z0-9_-]{2,32}$/.test(v)) return toast("Workspace: 2-32 chars, a-z 0-9 _ -", true);
    state.user = v;
    localStorage.setItem("cl_user", v);
    toast(`Switched to workspace "${v}"`);
    load();
  });

  $("#rows").addEventListener("click", (e) => {
    const del = e.target.closest("[data-delete]");
    const rec = e.target.closest("[data-receipt]");
    if (del) deleteExpense(del.dataset.delete);
    if (rec) showReceipt(rec.dataset.receipt);
  });

  $("#budgetBtn").addEventListener("click", () => {
    $("#budgetInput").value = state.summary?.budget || "";
    $("#budgetDialog").showModal();
  });
  $("#budgetDialog").addEventListener("close", async () => {
    if ($("#budgetDialog").returnValue !== "save") return;
    try {
      await api("/budget", { method: "PUT", body: JSON.stringify({ amount: $("#budgetInput").value }) });
      toast("Budget updated");
      load();
    } catch (err) {
      toast(err.message, true);
    }
  });
  document.querySelectorAll("[data-close]").forEach((b) =>
    b.addEventListener("click", () => b.closest("dialog").close()));

  checkHealth();
  load();
});
