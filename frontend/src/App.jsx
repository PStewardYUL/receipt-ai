import React, { useState, useEffect, useCallback, useRef } from "react";

const BASE = "/api";
const f2 = (n) => (n == null ? "—" : `$${Number(n).toFixed(2)}`);
const fPct = (n) => (n == null || n === 0 ? "" : `$${Number(n).toFixed(2)}`);

// ── Toast ─────────────────────────────────────────────────────────────────────
function useToast() {
  const [toasts, setToasts] = useState([]);
  const add = useCallback((msg, type = "info") => {
    const id = Date.now();
    setToasts((t) => [...t, { id, msg, type }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3500);
  }, []);
  return { toasts, add };
}
function Toasts({ toasts }) {
  const colors = { info: "#3b82f6", success: "#10b981", error: "#ef4444", warn: "#f59e0b" };
  return (
    <div style={{ position: "fixed", bottom: 24, right: 24, zIndex: 9999, display: "flex", flexDirection: "column", gap: 8 }}>
      {toasts.map((t) => (
        <div key={t.id} style={{ background: colors[t.type] || colors.info, color: "#fff", padding: "10px 16px", borderRadius: 8, fontSize: 14, maxWidth: 320, boxShadow: "0 4px 12px rgba(0,0,0,.2)" }}>
          {t.msg}
        </div>
      ))}
    </div>
  );
}

// ── API ───────────────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  const r = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  if (r.status === 204) return null;
  return r.json();
}

// ── Sidebar ───────────────────────────────────────────────────────────────────
const NAV = [
  { id: "dashboard", label: "Dashboard", icon: "📊" },
  { id: "receipts",  label: "Receipts",  icon: "🧾" },
  { id: "review",    label: "Review Queue", icon: "🔍" },
  { id: "aliases",   label: "Vendor Aliases", icon: "🔗" },
  { id: "categories",label: "Categories", icon: "📁" },
  { id: "processing",label: "Processing", icon: "⚙️" },
  { id: "settings",  label: "Settings",  icon: "🛠️" },
];
function Sidebar({ page, setPage, pendingCount }) {
  return (
    <nav style={{ width: 220, minHeight: "100vh", background: "#1e1b4b", display: "flex", flexDirection: "column", padding: "24px 0" }}>
      <div style={{ padding: "0 20px 24px", color: "#fff", fontWeight: 800, fontSize: 18 }}>🧾 ReceiptAI</div>
      {NAV.map((n) => (
        <button key={n.id} onClick={() => setPage(n.id)}
          style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 20px", background: page === n.id ? "#4f46e5" : "transparent", color: page === n.id ? "#fff" : "#a5b4fc", border: "none", cursor: "pointer", fontSize: 14, textAlign: "left", width: "100%" }}>
          <span>{n.icon}</span>
          <span style={{ flex: 1 }}>{n.label}</span>
          {n.id === "review" && pendingCount > 0 && (
            <span style={{ background: "#ef4444", color: "#fff", borderRadius: 10, padding: "1px 7px", fontSize: 11, fontWeight: 700 }}>{pendingCount}</span>
          )}
        </button>
      ))}
    </nav>
  );
}

// ── Inline editable cell ──────────────────────────────────────────────────────
function EditCell({ value, onSave, type = "text", style = {} }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(value ?? "");
  const ref = useRef();

  useEffect(() => { setVal(value ?? ""); }, [value]);
  useEffect(() => { if (editing && ref.current) ref.current.focus(); }, [editing]);

  const commit = () => {
    setEditing(false);
    const parsed = type === "number" ? (parseFloat(val) || 0) : val;
    if (parsed !== value) onSave(parsed);
  };

  if (!editing) return (
    <span onClick={() => setEditing(true)} title="Click to edit"
      style={{ cursor: "pointer", borderBottom: "1px dashed #d1d5db", minWidth: 40, display: "inline-block", ...style }}>
      {value == null || value === "" ? <span style={{ color: "#d1d5db" }}>—</span> : String(value)}
    </span>
  );

  return (
    <input ref={ref} value={val} type={type === "number" ? "number" : "text"}
      step={type === "number" ? "0.01" : undefined}
      onChange={(e) => setVal(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") setEditing(false); }}
      style={{ width: type === "number" ? 80 : 130, border: "1px solid #4f46e5", borderRadius: 4, padding: "2px 6px", fontSize: 13, ...style }}
    />
  );
}

// ── VendorCell with autocomplete ──────────────────────────────────────────────
function VendorCell({ value, vendors, onSave }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(value || "");
  const [filtered, setFiltered] = useState([]);
  const ref = useRef();

  useEffect(() => { setVal(value || ""); }, [value]);
  useEffect(() => { if (editing && ref.current) ref.current.focus(); }, [editing]);

  const onChange = (v) => {
    setVal(v);
    if (v.length > 0) {
      setFiltered(vendors.filter(n => n.toLowerCase().includes(v.toLowerCase())).slice(0, 6));
    } else {
      setFiltered([]);
    }
  };

  const commit = (v) => {
    const final = v ?? val;
    setEditing(false);
    setFiltered([]);
    if (final !== value) onSave(final);
  };

  if (!editing) return (
    <span onClick={() => setEditing(true)} title="Click to edit"
      style={{ cursor: "pointer", borderBottom: "1px dashed #d1d5db", display: "inline-block", maxWidth: 220, whiteSpace: "nowrap" }}>
      {value || <span style={{ color: "#d1d5db" }}>—</span>}
    </span>
  );

  return (
    <div style={{ position: "relative", display: "inline-block" }}>
      <input ref={ref} value={val}
        onChange={(e) => onChange(e.target.value)}
        onBlur={() => setTimeout(() => commit(), 200)}
        onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") { setEditing(false); setFiltered([]); } }}
        style={{ width: 200, border: "1px solid #4f46e5", borderRadius: 4, padding: "2px 6px", fontSize: 13 }}
      />
      {filtered.length > 0 && (
        <div style={{ position: "absolute", top: "100%", left: 0, background: "#fff", border: "1px solid #e5e7eb", borderRadius: 6, zIndex: 100, minWidth: 200, boxShadow: "0 4px 12px rgba(0,0,0,.1)" }}>
          {filtered.map((v) => (
            <div key={v} onMouseDown={() => commit(v)}
              style={{ padding: "6px 12px", cursor: "pointer", fontSize: 13 }}
              onMouseEnter={(e) => e.target.style.background = "#f3f4f6"}
              onMouseLeave={(e) => e.target.style.background = "transparent"}>
              {v}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Document preview modal ────────────────────────────────────────────────────
function DocPreview({ receipt, onClose }) {
  if (!receipt) return null;
  const plUrl = receipt.paperless_url;
  const thumbUrl = receipt.paperless_id
    ? `${window.PAPERLESS_URL || ""}/api/documents/${receipt.paperless_id}/thumb/`
    : null;

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ background: "#fff", borderRadius: 12, padding: 28, maxWidth: 520, width: "90%", maxHeight: "85vh", overflow: "auto", boxShadow: "0 20px 60px rgba(0,0,0,.3)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
          <strong style={{ fontSize: 16 }}>{receipt.vendor || "Unknown Vendor"}</strong>
          <button onClick={onClose} style={{ border: "none", background: "none", fontSize: 20, cursor: "pointer", color: "#6b7280" }}>✕</button>
        </div>
        {thumbUrl && (
          <img src={thumbUrl} alt="Receipt thumbnail"
            style={{ width: "100%", maxHeight: 300, objectFit: "contain", borderRadius: 8, marginBottom: 16, border: "1px solid #e5e7eb" }}
            onError={(e) => { e.target.style.display = "none"; }}
          />
        )}
        <table style={{ width: "100%", fontSize: 14, borderCollapse: "collapse" }}>
          {[
            ["Date", receipt.date], ["Vendor", receipt.vendor],
            ["Pre-Tax", f2(receipt.pre_tax)], ["GST", fPct(receipt.gst)],
            ["QST", fPct(receipt.qst)], ["PST", fPct(receipt.pst)],
            ["HST", fPct(receipt.hst)], ["Total", f2(receipt.total)],
            ["Currency", receipt.currency], ["Category", receipt.category_name],
            ["Confidence", receipt.confidence ? `${(receipt.confidence * 100).toFixed(0)}%` : "—"],
          ].map(([label, val]) => val && val !== "—" && val !== "" ? (
            <tr key={label} style={{ borderBottom: "1px solid #f3f4f6" }}>
              <td style={{ padding: "6px 0", color: "#6b7280", width: 100 }}>{label}</td>
              <td style={{ padding: "6px 0", fontWeight: label === "Total" ? 700 : 400 }}>{val}</td>
            </tr>
          ) : null)}
        </table>
        {plUrl && (
          <a href={plUrl} target="_blank" rel="noreferrer"
            style={{ display: "block", marginTop: 16, textAlign: "center", background: "#4f46e5", color: "#fff", padding: "10px 0", borderRadius: 8, textDecoration: "none", fontSize: 14, fontWeight: 600 }}>
            Open in Paperless ↗
          </a>
        )}
      </div>
    </div>
  );
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
function Dashboard({ toast }) {
  const [year, setYear] = useState(new Date().getFullYear());
  const [data, setData] = useState(null);

  useEffect(() => {
    api(`/receipts/summary?year=${year}`).then(setData).catch(() => {});
  }, [year]);

  const kpis = data ? [
    { label: "Receipts",     value: data.total_receipts, color: "#4f46e5", bg: "#ede9fe" },
    { label: "Total Spent",  value: `$${(data.total_amount||0).toFixed(2)}`, color: "#059669", bg: "#d1fae5" },
    { label: "Tax Paid",     value: `$${((data.total_gst||0)+(data.total_qst||0)+(data.total_pst||0)+(data.total_hst||0)).toFixed(2)}`, color: "#d97706", bg: "#fef3c7" },
    { label: "Vendors",      value: data.vendor_count, color: "#7c3aed", bg: "#f5f3ff" },
  ] : [];

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 28 }}>
        <h2 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>Dashboard</h2>
        <select value={year} onChange={(e) => setYear(+e.target.value)}
          style={{ border: "1px solid #e5e7eb", borderRadius: 7, padding: "6px 12px", fontSize: 14 }}>
          {[2022,2023,2024,2025,2026].map((y) => <option key={y}>{y}</option>)}
        </select>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 16, marginBottom: 28 }}>
        {kpis.map((k) => (
          <div key={k.label} style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 12, padding: 20 }}>
            <div style={{ fontSize: 12, color: "#6b7280", marginBottom: 6 }}>{k.label}</div>
            <div style={{ fontSize: 26, fontWeight: 800, color: k.color }}>{k.value}</div>
          </div>
        ))}
      </div>
      {data && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
          <div style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 12, padding: 20 }}>
            <h3 style={{ margin: "0 0 16px", fontSize: 15 }}>By Category</h3>
            {(data.by_category||[]).map((c) => (
              <div key={c.name} style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: "1px solid #f9fafb", fontSize: 14 }}>
                <span>{c.name}</span><span style={{ fontWeight: 600 }}>${c.total.toFixed(2)}</span>
              </div>
            ))}
          </div>
          <div style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 12, padding: 20 }}>
            <h3 style={{ margin: "0 0 12px", fontSize: 15 }}>Tax Breakdown</h3>
            {[["GST (5%)", data.total_gst], ["QST (9.975%)", data.total_qst], ["PST", data.total_pst], ["HST", data.total_hst]]
              .filter(([,v]) => v > 0)
              .map(([label, val]) => (
                <div key={label} style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: "1px solid #f9fafb", fontSize: 14 }}>
                  <span>{label}</span><span style={{ fontWeight: 600 }}>${(val||0).toFixed(2)}</span>
                </div>
              ))}
            <div style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", fontSize: 14, fontWeight: 700, marginTop: 4, borderTop: "2px solid #e5e7eb" }}>
              <span>Total Tax</span>
              <span>${((data.total_gst||0)+(data.total_qst||0)+(data.total_pst||0)+(data.total_hst||0)).toFixed(2)}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Receipts ──────────────────────────────────────────────────────────────────
function Receipts({ toast }) {
  const [year, setYear]           = useState(new Date().getFullYear());
  const [receipts, setReceipts]   = useState([]);
  const [categories, setCategories] = useState([]);
  const [vendors, setVendors]     = useState([]);
  const [search, setSearch]       = useState("");
  const [selected, setSelected]   = useState(new Set());
  const [preview, setPreview]     = useState(null);
  const [loading, setLoading]     = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      api(`/receipts/?year=${year}&limit=500`),
      api("/categories/"),
      api("/receipts/vendors"),
    ]).then(([r, c, v]) => {
      setReceipts(r);
      setCategories(c);
      setVendors(v);
    }).catch(() => toast("Failed to load", "error"))
      .finally(() => setLoading(false));
  }, [year]);

  useEffect(() => { load(); }, [load]);

  const update = async (id, patch) => {
    try {
      const updated = await api(`/receipts/${id}`, { method: "PATCH", body: patch });
      setReceipts((rs) => rs.map((r) => r.id === id ? updated : r));
      toast("Saved", "success");
    } catch { toast("Save failed", "error"); }
  };

  const rescanSelected = async () => {
    const ids = [...selected];
    try {
      await api("/receipts/rescan", { method: "POST", body: ids });
      toast(`Rescanning ${ids.length} receipt(s)…`, "info");
      setSelected(new Set());
      setTimeout(load, 2000);
    } catch { toast("Rescan failed", "error"); }
  };

  const filtered = receipts.filter((r) =>
    !search || (r.vendor || "").toLowerCase().includes(search.toLowerCase())
  );

  const toggleSelect = (id) => setSelected((s) => {
    const n = new Set(s);
    n.has(id) ? n.delete(id) : n.add(id);
    return n;
  });

  const allSelected = filtered.length > 0 && filtered.every((r) => selected.has(r.id));
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(filtered.map((r) => r.id)));

  const confColor = (c) => c >= 0.8 ? "#059669" : c >= 0.6 ? "#d97706" : "#ef4444";

  const totals = filtered.reduce((acc, r) => ({
    pre_tax: acc.pre_tax + (r.pre_tax || 0),
    gst:     acc.gst     + (r.gst     || 0),
    qst:     acc.qst     + (r.qst     || 0),
    pst:     acc.pst     + (r.pst     || 0),
    hst:     acc.hst     + (r.hst     || 0),
    total:   acc.total   + (r.total   || 0),
  }), { pre_tax:0, gst:0, qst:0, pst:0, hst:0, total:0 });

  const COL = { date:100, vendor:200, cat:130, pretax:85, gst:70, qst:70, pst:70, hst:70, total:90, conf:70, cur:50, act:80 };

  return (
    <div>
      <DocPreview receipt={preview} onClose={() => setPreview(null)} />
      <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 18, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 22, fontWeight: 700, flex: "0 0 auto" }}>Receipts</h2>
        <select value={year} onChange={(e) => setYear(+e.target.value)}
          style={{ border: "1px solid #e5e7eb", borderRadius: 7, padding: "6px 10px", fontSize: 14 }}>
          {[2022,2023,2024,2025,2026].map((y) => <option key={y}>{y}</option>)}
        </select>
        <input placeholder="Search vendor…" value={search} onChange={(e) => setSearch(e.target.value)}
          style={{ border: "1px solid #e5e7eb", borderRadius: 7, padding: "6px 12px", fontSize: 14, width: 180 }} />
        <button onClick={load} disabled={loading}
          style={{ background: "#f3f4f6", border: "1px solid #e5e7eb", borderRadius: 7, padding: "6px 14px", cursor: "pointer", fontSize: 13 }}>
          {loading ? "…" : "⟳ Refresh"}
        </button>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          {selected.size > 0 && (
            <button onClick={rescanSelected}
              style={{ background: "#f59e0b", color: "#fff", border: "none", borderRadius: 7, padding: "6px 14px", cursor: "pointer", fontSize: 13 }}>
              ⟳ Rescan {selected.size} selected
            </button>
          )}
          <a href={`${BASE}/receipts/export/csv?year=${year}`}
            style={{ background: "#10b981", color: "#fff", border: "none", borderRadius: 7, padding: "6px 14px", cursor: "pointer", fontSize: 13, textDecoration: "none" }}>
            CSV
          </a>
          <a href={`${BASE}/receipts/export/pdf?year=${year}`}
            style={{ background: "#4f46e5", color: "#fff", border: "none", borderRadius: 7, padding: "6px 14px", cursor: "pointer", fontSize: 13, textDecoration: "none" }}>
            PDF
          </a>
        </div>
      </div>

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, tableLayout: "fixed" }}>
          <colgroup>
            <col style={{ width: 32 }} />
            <col style={{ width: COL.date }} />
            <col style={{ width: COL.vendor }} />
            <col style={{ width: COL.cat }} />
            <col style={{ width: COL.pretax }} />
            <col style={{ width: COL.gst }} />
            <col style={{ width: COL.qst }} />
            <col style={{ width: COL.pst }} />
            <col style={{ width: COL.hst }} />
            <col style={{ width: COL.total }} />
            <col style={{ width: COL.cur }} />
            <col style={{ width: COL.conf }} />
            <col style={{ width: COL.act }} />
          </colgroup>
          <thead>
            <tr style={{ background: "#f9fafb", borderBottom: "2px solid #e5e7eb" }}>
              <th style={{ padding: "8px 6px" }}>
                <input type="checkbox" checked={allSelected} onChange={toggleAll} />
              </th>
              {["Date","Vendor","Category","Pre-Tax","GST","QST","PST","HST","Total","Cur","Conf",""].map((h) => (
                <th key={h} style={{ padding: "8px 6px", textAlign: "left", fontWeight: 600, fontSize: 11, color: "#6b7280", textTransform: "uppercase" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={r.id} style={{ borderBottom: "1px solid #f3f4f6", background: selected.has(r.id) ? "#eff6ff" : "transparent" }}>
                <td style={{ padding: "6px" }}>
                  <input type="checkbox" checked={selected.has(r.id)} onChange={() => toggleSelect(r.id)} />
                </td>
                <td style={{ padding: "6px" }}>
                  <EditCell value={r.date} onSave={(v) => update(r.id, { date: v })} />
                </td>
                <td style={{ padding: "6px" }}>
                  <VendorCell value={r.vendor} vendors={vendors} onSave={(v) => update(r.id, { vendor: v })} />
                </td>
                <td style={{ padding: "6px" }}>
                  <select value={r.category_id || ""} onChange={(e) => update(r.id, { category_id: +e.target.value || 0 })}
                    style={{ width: "100%", border: "1px solid #e5e7eb", borderRadius: 5, padding: "3px 4px", fontSize: 12 }}>
                    <option value="">— None —</option>
                    {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                </td>
                <td style={{ padding: "6px" }}><EditCell type="number" value={r.pre_tax} onSave={(v) => update(r.id, { pre_tax: v })} /></td>
                <td style={{ padding: "6px" }}><EditCell type="number" value={r.gst} onSave={(v) => update(r.id, { gst: v })} /></td>
                <td style={{ padding: "6px" }}><EditCell type="number" value={r.qst} onSave={(v) => update(r.id, { qst: v })} /></td>
                <td style={{ padding: "6px" }}><EditCell type="number" value={r.pst} onSave={(v) => update(r.id, { pst: v })} /></td>
                <td style={{ padding: "6px" }}><EditCell type="number" value={r.hst} onSave={(v) => update(r.id, { hst: v })} /></td>
                <td style={{ padding: "6px", fontWeight: 600 }}><EditCell type="number" value={r.total} onSave={(v) => update(r.id, { total: v })} /></td>
                <td style={{ padding: "6px" }}>
                  <select value={r.currency || "CAD"}
                    onChange={(e) => update(r.id, { currency: e.target.value })}
                    style={{ border: "1px solid #e5e7eb", borderRadius: 5, padding: "2px 4px", fontSize: 12, color: (r.currency || "CAD") === "USD" ? "#d97706" : "#6b7280", background: "transparent", cursor: "pointer" }}>
                    <option value="CAD">CAD</option>
                    <option value="USD">USD</option>
                  </select>
                </td>
                <td style={{ padding: "6px" }}>
                  <span style={{ color: confColor(r.confidence || 0), fontWeight: 600, fontSize: 11 }}>
                    {r.confidence ? `${(r.confidence * 100).toFixed(0)}%` : "—"}
                  </span>
                </td>
                <td style={{ padding: "6px" }}>
                  <button onClick={() => setPreview(r)} title="Preview document"
                    style={{ background: "none", border: "1px solid #e5e7eb", borderRadius: 5, padding: "3px 8px", cursor: "pointer", fontSize: 12 }}>
                    👁
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr style={{ borderTop: "2px solid #e5e7eb", background: "#f9fafb", fontWeight: 700 }}>
              <td colSpan={4} style={{ padding: "8px 6px", fontSize: 12 }}>{filtered.length} receipts</td>
              <td style={{ padding: "8px 6px" }}>${totals.pre_tax.toFixed(2)}</td>
              <td style={{ padding: "8px 6px" }}>${totals.gst.toFixed(2)}</td>
              <td style={{ padding: "8px 6px" }}>${totals.qst.toFixed(2)}</td>
              <td style={{ padding: "8px 6px" }}>${totals.pst.toFixed(2)}</td>
              <td style={{ padding: "8px 6px" }}>${totals.hst.toFixed(2)}</td>
              <td style={{ padding: "8px 6px" }}>${totals.total.toFixed(2)}</td>
              <td colSpan={3} />
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

// ── Review Queue ──────────────────────────────────────────────────────────────
function ReviewQueue({ toast }) {
  const [tab, setTab] = useState("pending");
  const [items, setItems] = useState([]);

  const load = () => api(`/review/?status=${tab}`).then(setItems).catch(() => {});
  useEffect(() => { load(); }, [tab]);

  const resolve = async (id, action) => {
    await api(`/review/${id}/resolve`, { method: "POST", body: { action } });
    toast(action === "approved" ? "Approved" : "Rejected", "success");
    load();
  };

  const reasonColor = { low_confidence: "#f59e0b", missing_total: "#ef4444", missing_date: "#f59e0b", missing_vendor: "#ef4444", manual: "#8b5cf6" };
  const reasonLabel = { low_confidence: "Low confidence", missing_total: "No total", missing_date: "No date", missing_vendor: "No vendor", manual: "Manual review" };

  const paperlessBase = window.__PAPERLESS_URL__ || "";

  return (
    <div>
      <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20 }}>Review Queue</h2>
      <div style={{ display: "flex", gap: 8, marginBottom: 20 }}>
        {["pending","approved","rejected"].map((t) => (
          <button key={t} onClick={() => setTab(t)}
            style={{ padding: "7px 18px", borderRadius: 7, border: "1px solid #e5e7eb", background: tab === t ? "#4f46e5" : "#fff", color: tab === t ? "#fff" : "#374151", cursor: "pointer", fontWeight: 600, fontSize: 13, textTransform: "capitalize" }}>
            {t}
          </button>
        ))}
      </div>
      {items.length === 0 ? (
        <div style={{ textAlign: "center", padding: 60, color: "#9ca3af" }}>No {tab} items ✓</div>
      ) : items.map((item) => {
        const isMissingVendor = !item.vendor;
        const isMissingDate   = !item.date;
        const isMissingTotal  = !item.total || item.total <= 0;
        const reasons = (item.reason || "").split("|").filter(Boolean);
        return (
          <div key={item.flag_id} style={{ background: "#fff", border: `1px solid ${isMissingVendor ? "#fca5a5" : "#e5e7eb"}`, borderRadius: 10, padding: 16, marginBottom: 12, display: "flex", alignItems: "center", gap: 16 }}>
            <div style={{ flex: 1 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <span style={{ fontWeight: 700, fontSize: 15, color: isMissingVendor ? "#9ca3af" : "#111827", fontStyle: isMissingVendor ? "italic" : "normal" }}>
                  {item.vendor || "⚠ No vendor identified"}
                </span>
                {item.paperless_id && (
                  <a href={`${paperlessBase}/documents/${item.paperless_id}`}
                    target="_blank" rel="noreferrer"
                    style={{ fontSize: 12, color: "#4f46e5", textDecoration: "none", background: "#eef2ff", borderRadius: 5, padding: "2px 8px", fontWeight: 600 }}>
                    Open in Paperless ↗
                  </a>
                )}
              </div>
              <div style={{ fontSize: 13, color: "#6b7280", marginTop: 4, display: "flex", gap: 16 }}>
                <span style={{ color: isMissingDate ? "#ef4444" : "#6b7280" }}>{item.date || "⚠ No date"}</span>
                <span style={{ color: isMissingTotal ? "#ef4444" : "#6b7280" }}>{isMissingTotal ? "⚠ No total" : f2(item.total)}</span>
                <span>conf {item.confidence ? `${(item.confidence*100).toFixed(0)}%` : "—"}</span>
                {item.category_name && <span>{item.category_name}</span>}
              </div>
              <div style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap" }}>
                {reasons.map((r) => (
                  <span key={r} style={{ background: reasonColor[r] || "#e5e7eb", color: "#fff", padding: "2px 10px", borderRadius: 10, fontSize: 11, fontWeight: 700 }}>
                    {reasonLabel[r] || r}
                  </span>
                ))}
              </div>
              {isMissingVendor && (
                <div style={{ marginTop: 8, fontSize: 12, color: "#6b7280", background: "#fafafa", borderRadius: 6, padding: "6px 10px", border: "1px solid #f3f4f6" }}>
                  💡 Open in Paperless to check the document, then edit the vendor field here or rescan.
                </div>
              )}
            </div>
            {tab === "pending" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <button onClick={() => resolve(item.flag_id, "approved")}
                  style={{ background: "#10b981", color: "#fff", border: "none", borderRadius: 7, padding: "7px 14px", cursor: "pointer", fontWeight: 600, minWidth: 90 }}>✓ Approve</button>
                <button onClick={() => resolve(item.flag_id, "rejected")}
                  style={{ background: "#ef4444", color: "#fff", border: "none", borderRadius: 7, padding: "7px 14px", cursor: "pointer", fontWeight: 600, minWidth: 90 }}>✕ Reject</button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Categories ────────────────────────────────────────────────────────────────
function Categories({ toast }) {
  const [cats, setCats] = useState([]);
  const [name, setName] = useState("");
  const [editing, setEditing] = useState({});

  useEffect(() => { api("/categories/").then(setCats).catch(() => {}); }, []);

  const add = async () => {
    if (!name.trim()) return;
    try {
      const c = await api("/categories/", { method: "POST", body: { name: name.trim() } });
      setCats((cs) => [...cs, c]);
      setName("");
      toast("Category added", "success");
    } catch { toast("Failed", "error"); }
  };

  const del = async (id) => {
    await api(`/categories/${id}`, { method: "DELETE" });
    setCats((cs) => cs.filter((c) => c.id !== id));
    toast("Deleted", "success");
  };

  const rename = async (id) => {
    const newName = editing[id];
    if (!newName?.trim()) return;
    try {
      const c = await api(`/categories/${id}`, { method: "PATCH", body: { name: newName.trim() } });
      setCats((cs) => cs.map((x) => x.id === id ? c : x));
      setEditing((e) => { const n = { ...e }; delete n[id]; return n; });
      toast("Renamed", "success");
    } catch { toast("Failed", "error"); }
  };

  return (
    <div>
      <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20 }}>Categories</h2>
      <div style={{ display: "flex", gap: 10, marginBottom: 24 }}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="New category…"
          onKeyDown={(e) => e.key === "Enter" && add()}
          style={{ border: "1px solid #e5e7eb", borderRadius: 7, padding: "8px 14px", fontSize: 14, flex: 1, maxWidth: 300 }} />
        <button onClick={add} style={{ background: "#4f46e5", color: "#fff", border: "none", borderRadius: 7, padding: "8px 20px", cursor: "pointer", fontWeight: 600 }}>Add</button>
      </div>
      {cats.map((c) => (
        <div key={c.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 0", borderBottom: "1px solid #f3f4f6" }}>
          {editing[c.id] !== undefined ? (
            <>
              <input value={editing[c.id]} onChange={(e) => setEditing({ ...editing, [c.id]: e.target.value })}
                onKeyDown={(e) => { if (e.key === "Enter") rename(c.id); if (e.key === "Escape") setEditing((x) => { const n={...x}; delete n[x.id]; return n; }); }}
                style={{ border: "1px solid #4f46e5", borderRadius: 5, padding: "5px 10px", fontSize: 14, flex: 1, maxWidth: 280 }} autoFocus />
              <button onClick={() => rename(c.id)} style={{ background: "#10b981", color: "#fff", border: "none", borderRadius: 6, padding: "5px 12px", cursor: "pointer" }}>Save</button>
            </>
          ) : (
            <>
              <span style={{ flex: 1, fontSize: 15 }}>{c.name}</span>
              <span style={{ fontSize: 12, color: "#9ca3af" }}>{c.receipt_count} receipts</span>
              <button onClick={() => setEditing({ ...editing, [c.id]: c.name })}
                style={{ background: "#f3f4f6", border: "1px solid #e5e7eb", borderRadius: 6, padding: "4px 10px", cursor: "pointer", fontSize: 12 }}>Edit</button>
              <button onClick={() => del(c.id)}
                style={{ background: "#fee2e2", border: "none", borderRadius: 6, padding: "4px 10px", cursor: "pointer", fontSize: 12, color: "#ef4444" }}>Delete</button>
            </>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Aliases ───────────────────────────────────────────────────────────────────
function Aliases({ toast }) {
  const [aliases, setAliases] = useState([]);
  const [suggestions, setSuggestions] = useState([]);
  const [raw, setRaw] = useState("");
  const [canonical, setCanonical] = useState("");

  useEffect(() => {
    Promise.all([api("/aliases/"), api("/aliases/suggestions")])
      .then(([a, s]) => { setAliases(a); setSuggestions(s); }).catch(() => {});
  }, []);

  const add = async () => {
    if (!raw.trim() || !canonical.trim()) return;
    try {
      const a = await api("/aliases/", { method: "POST", body: { raw_name: raw.trim(), canonical_name: canonical.trim() } });
      setAliases((as) => [...as, a]);
      setRaw(""); setCanonical("");
      toast("Alias created", "success");
    } catch { toast("Failed", "error"); }
  };

  const del = async (id) => {
    await api(`/aliases/${id}`, { method: "DELETE" });
    setAliases((as) => as.filter((a) => a.id !== id));
    toast("Deleted", "success");
  };

  return (
    <div>
      <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20 }}>Vendor Aliases</h2>
      <div style={{ display: "flex", gap: 12, marginBottom: 24, alignItems: "flex-end" }}>
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#6b7280", marginBottom: 4 }}>RAW NAME (as received)</div>
          <input value={raw} onChange={(e) => setRaw(e.target.value)}
            placeholder={"e.g. \"TIM HORTON'S #042\""}
            style={{ border: "1px solid #d1d5db", borderRadius: 7, padding: "9px 12px", fontSize: 13, width: 220 }} />
        </div>
        <span style={{ fontSize: 20, color: "#9ca3af", paddingBottom: 2 }}>→</span>
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#6b7280", marginBottom: 4 }}>CANONICAL NAME</div>
          <input value={canonical} onChange={(e) => setCanonical(e.target.value)}
            placeholder={"e.g. \"Tim Hortons\""}
            style={{ border: "1px solid #d1d5db", borderRadius: 7, padding: "9px 12px", fontSize: 13, width: 200 }} />
        </div>
        <button onClick={add} style={{ background: "#4f46e5", color: "#fff", border: "none", borderRadius: 7, padding: "9px 20px", cursor: "pointer", fontWeight: 600 }}>Add</button>
      </div>
      {suggestions.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <h3 style={{ fontSize: 14, color: "#6b7280", marginBottom: 10 }}>Merge Suggestions</h3>
          {suggestions.map((s, i) => (
            <div key={i} style={{ background: "#fffbeb", border: "1px solid #fde68a", borderRadius: 8, padding: 12, marginBottom: 8 }}>
              <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 13 }}>Possible duplicates:</div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {s.variants?.map((v) => (
                  <button key={v} onClick={() => setRaw(v)}
                    style={{ background: "#fff", border: "1px solid #fbbf24", borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 12 }}>
                    {v}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
        <thead>
          <tr style={{ background: "#f9fafb", borderBottom: "2px solid #e5e7eb" }}>
            {["Raw Name", "→", "Canonical Name", ""].map((h) => (
              <th key={h} style={{ padding: "8px 12px", textAlign: "left", fontWeight: 600, fontSize: 11, color: "#6b7280" }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {aliases.map((a) => (
            <tr key={a.id} style={{ borderBottom: "1px solid #f3f4f6" }}>
              <td style={{ padding: "8px 12px" }}>{a.raw_name}</td>
              <td style={{ padding: "8px 12px", color: "#9ca3af" }}>→</td>
              <td style={{ padding: "8px 12px", fontWeight: 600 }}>{a.canonical_name}</td>
              <td style={{ padding: "8px 12px" }}>
                <button onClick={() => del(a.id)}
                  style={{ background: "#fee2e2", border: "none", borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 12, color: "#ef4444" }}>Delete</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Processing ────────────────────────────────────────────────────────────────
function Processing({ toast }) {
  const [health, setHealth] = useState(null);
  const [status, setStatus] = useState(null);
  const [singleId, setSingleId] = useState("");
  const [forceReocr, setForceReocr] = useState(false);

  useEffect(() => {
    const check = () => {
      api("/processing/health").then(setHealth).catch(() => {});
      api("/processing/batch/status").then(setStatus).catch(() => {});
    };
    check();
    const t = setInterval(check, 8000);
    return () => clearInterval(t);
  }, []);

  const startBatch = async () => {
    try {
      await api("/processing/batch", { method: "POST", body: { force_reocr: forceReocr } });
      toast("Batch started", "success");
    } catch (e) {
      toast(e.message.includes("409") ? "Batch already running" : "Failed", "warn");
    }
  };

  const processSingle = async () => {
    if (!singleId.trim()) return;
    try {
      const r = await api("/processing/single", { method: "POST", body: { paperless_id: +singleId, force_reocr: forceReocr } });
      toast(r.is_receipt ? `Receipt: ${r.vendor} — ${f2(r.total)}` : "Not a receipt", r.is_receipt ? "success" : "info");
    } catch (e) { toast(`Error: ${e.message}`, "error"); }
  };

  const dot = (ok) => <span style={{ display: "inline-block", width: 10, height: 10, borderRadius: "50%", background: ok ? "#10b981" : "#ef4444", marginRight: 8 }} />;

  return (
    <div>
      <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20 }}>Processing</h2>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 24 }}>
        <div style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 10, padding: 18 }}>
          <div style={{ fontWeight: 600, marginBottom: 10 }}>Services</div>
          <div style={{ fontSize: 14 }}>{dot(health?.paperless)} Paperless-ngx {!health?.paperless && <span style={{ color: "#ef4444", fontSize: 11 }}> — check URL/token</span>}</div>
          <div style={{ fontSize: 14, marginTop: 6 }}>{dot(health?.paddleocr)} PaddleOCR (Primary)</div>
          <div style={{ fontSize: 14, marginTop: 6 }}>{dot(health?.ollama)} Ollama (Fallback)</div>
          {health?.ollama_models?.length > 0 && (
            <div style={{ fontSize: 12, color: "#6b7280", marginTop: 6 }}>LLM Models: {health.ollama_models.join(", ")}</div>
          )}
        </div>
        <div style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 10, padding: 18 }}>
          <div style={{ fontWeight: 600, marginBottom: 10 }}>Batch Status</div>
          {status && (
            <div style={{ fontSize: 13 }}>
              <div>{status.running ? "🔄 Running…" : "⏸ Idle"}</div>
              {status.stats && (
                <div style={{ marginTop: 8, color: "#6b7280" }}>
                  Last run: {status.stats.processed} docs · {status.stats.receipts} receipts · {status.stats.errors} errors
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 20 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 14, cursor: "pointer" }}>
          <input type="checkbox" checked={forceReocr} onChange={(e) => setForceReocr(e.target.checked)} />
          Force Re-OCR
        </label>
        <button onClick={startBatch}
          style={{ background: status?.running ? "#9ca3af" : "#4f46e5", color: "#fff", border: "none", borderRadius: 8, padding: "10px 24px", cursor: status?.running ? "default" : "pointer", fontWeight: 600 }}>
          {status?.running ? "Running…" : "▶ Start Batch Scan"}
        </button>
      </div>
      <div style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 10, padding: 20 }}>
        <div style={{ fontWeight: 600, marginBottom: 12 }}>Process Single Document</div>
        <div style={{ display: "flex", gap: 10 }}>
          <input value={singleId} onChange={(e) => setSingleId(e.target.value)} placeholder="Paperless document ID…"
            style={{ border: "1px solid #e5e7eb", borderRadius: 7, padding: "8px 14px", fontSize: 14, width: 200 }} />
          <button onClick={processSingle}
            style={{ background: "#4f46e5", color: "#fff", border: "none", borderRadius: 7, padding: "8px 20px", cursor: "pointer", fontWeight: 600 }}>
            Process
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Settings ──────────────────────────────────────────────────────────────────
function Settings({ toast }) {
  const [s, setS] = useState(null);
  useEffect(() => { api("/settings/").then(setS).catch(() => {}); }, []);

  const save = async () => {
    try {
      await api("/settings/", { method: "PUT", body: s });
      toast("Saved", "success");
    } catch { toast("Save failed", "error"); }
  };

  const bool = (key) => (
    <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 14, marginBottom: 10, cursor: "pointer" }}>
      <input type="checkbox" checked={!!s?.[key]} onChange={(e) => setS({ ...s, [key]: e.target.checked })} />
      {key.replace(/_/g, " ")}
    </label>
  );

  return (
    <div>
      <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20 }}>Settings</h2>
      {s && (
        <div style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 10, padding: 24, maxWidth: 480 }}>
          <div style={{ marginBottom: 16 }}>
            <label style={{ fontSize: 13, fontWeight: 600, color: "#374151", display: "block", marginBottom: 5 }}>Vision Model (Fallback OCR)</label>
            <input value={s.vision_model || ""} onChange={(e) => setS({ ...s, vision_model: e.target.value })}
              style={{ border: "1px solid #e5e7eb", borderRadius: 7, padding: "8px 12px", fontSize: 14, width: "100%" }} />
            <div style={{ fontSize: 11, color: "#6b7280", marginTop: 4 }}>Ollama vision model for fallback (e.g., llava). Primary OCR uses PaddleOCR.</div>
          </div>
          <div style={{ marginBottom: 16 }}>
            <label style={{ fontSize: 13, fontWeight: 600, color: "#374151", display: "block", marginBottom: 5 }}>Text Model (LLM Parsing)</label>
            <input value={s.text_model || ""} onChange={(e) => setS({ ...s, text_model: e.target.value })}
              style={{ border: "1px solid #e5e7eb", borderRadius: 7, padding: "8px 12px", fontSize: 14, width: "100%" }} />
            <div style={{ fontSize: 11, color: "#6b7280", marginTop: 4 }}>Ollama model for receipt text parsing (e.g., mistral, llama3)</div>
          </div>
          <div style={{ marginTop: 8 }}>
            {bool("force_reocr")}
            {bool("use_paperless_ocr_first")}
            {bool("auto_skip_vision_if_text_exists")}
          </div>
          <button onClick={save} style={{ background: "#4f46e5", color: "#fff", border: "none", borderRadius: 7, padding: "10px 28px", cursor: "pointer", fontWeight: 600, marginTop: 12 }}>Save</button>
        </div>
      )}
    </div>
  );
}

// ── App shell ─────────────────────────────────────────────────────────────────
export default function App() {
  const [page, setPage] = useState("dashboard");
  const [pendingCount, setPendingCount] = useState(0);
  const { toasts, add: toast } = useToast();

  useEffect(() => {
    const poll = () => api("/review/count").then((d) => setPendingCount(d.pending || 0)).catch(() => {});
    poll();
    const t = setInterval(poll, 30000);
    return () => clearInterval(t);
  }, []);

  const pages = { dashboard: Dashboard, receipts: Receipts, review: ReviewQueue, aliases: Aliases, categories: Categories, processing: Processing, settings: Settings };
  const Page = pages[page] || Dashboard;

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#f9fafb", fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif" }}>
      <Sidebar page={page} setPage={setPage} pendingCount={pendingCount} />
      <main style={{ flex: 1, padding: 32, overflowX: "auto" }}>
        <Page toast={toast} />
      </main>
      <Toasts toasts={toasts} />
    </div>
  );
}
