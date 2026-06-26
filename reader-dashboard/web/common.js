// shared helpers
async function loadLibrary() {
  const r = await fetch('library.json?_=' + Date.now());
  if (!r.ok) throw new Error('library.json not found');
  return r.json();
}

function fmtMinutes(m) {
  if (!m) return '—';
  const h = Math.floor(m / 60), mm = Math.round(m % 60);
  return h ? `${h}h ${mm}m` : `${mm}m`;
}
function fmtNum(n) {
  return (n == null) ? '—' : Number(n).toLocaleString('en-US');
}
function fmtDate(v) {
  if (!v) return '—';
  // epoch seconds/ms (cc.db) or ISO string (krds)
  let d;
  if (typeof v === 'number' || /^\d+$/.test(v)) {
    let n = Number(v);
    if (n < 1e12) n *= 1000;   // cc.db stores epoch SECONDS; krds gives ms/ISO
    d = new Date(n);
  } else d = new Date(v);
  if (isNaN(d)) return v;
  return d.toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' });
}
function readStateLabel(s) {
  if (s === 2) return ['done', 'Read'];
  if (s === 1) return ['reading', 'Reading'];
  return ['unread', 'Unread'];
}
// read_status from build() (fmcache-aware): 'read' | 'reading' | 'unread'
function readStatusLabel(b) {
  const st = b.read_status || (b.readState === 2 ? 'read' : b.readState === 1 ? 'reading' : 'unread');
  if (st === 'read') return ['done', b.fm_read_state === 'READ' ? 'Read (manual)' : 'Read'];
  if (st === 'reading') return ['reading', 'Reading'];
  return ['unread', 'Unread'];
}
function readRank(b) {
  const st = b.read_status; return st === 'read' ? 2 : st === 'reading' ? 1 : 0;
}
function qparam(k) {
  return new URLSearchParams(location.search).get(k);
}
function pct(b) {
  const p = (b.stats && b.stats.percent_read) || b.percentFinished || 0;
  return Math.round(p);
}

// ---- page mapping (apnx oPNToPosition: index = printed page) ----
function apnx(b) { return (b.azw3r || {})['apnx.key'] || null; }
function totalPages(b) { const a = apnx(b); return (a && a.oPNToPosition) ? a.oPNToPosition.length - 1 : null; }
function pageOf(b, position) {
  const a = apnx(b);
  if (!a || !a.oPNToPosition) return null;
  const arr = a.oPNToPosition, p = Number(position);
  if (isNaN(p)) return null;
  let lo = 0, hi = arr.length - 1, ans = 0;          // largest i with arr[i] <= p
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (arr[mid] <= p) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
  return ans;
}
// current page from the last-read position (lpr -> furthest-read fpr fallback)
function currentPage(b) {
  const f = b.azw3f || {}, src = f.lpr || f.fpr;
  return src ? pageOf(b, src.position) : null;
}
async function refresh(btn) {
  const old = btn.textContent;
  btn.textContent = 'Syncing…'; btn.disabled = true;
  try {
    const r = await fetch('/api/refresh', { method: 'POST' });
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || 'failed');
    location.reload();
  } catch (e) {
    btn.textContent = 'Error: ' + e.message;
    setTimeout(() => { btn.textContent = old; btn.disabled = false; }, 4000);
  }
}
