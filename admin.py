# admin.py — 管理後台 Blueprint
import os
from functools import wraps
from flask import Blueprint, render_template_string, request, redirect, url_for, session, jsonify
from canteen_db import CanteenDB

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')
db = CanteenDB()

ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'nsysu2024')


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin.login'))
        return f(*args, **kwargs)
    return decorated


# ── HTML 模板 ─────────────────────────────────────────────────────────────────
BASE_HTML = '''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🍱 Food Bot 管理後台</title>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #0a0e0d;
  --surface: #111814;
  --border: #1e2a23;
  --teal: #1fcc8a;
  --teal-dim: #0d6e49;
  --red: #ff4d6d;
  --orange: #ff9f43;
  --text: #e8f5ee;
  --muted: #5a7a66;
  --card: #141c18;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: var(--bg); color: var(--text); font-family: 'Syne', sans-serif; min-height: 100vh; }
a { color: var(--teal); text-decoration: none; }
a:hover { text-decoration: underline; }

/* Layout */
.sidebar {
  position: fixed; left: 0; top: 0; bottom: 0; width: 220px;
  background: var(--surface); border-right: 1px solid var(--border);
  padding: 2rem 0; z-index: 100;
}
.sidebar .logo {
  padding: 0 1.5rem 2rem;
  font-size: 1.1rem; font-weight: 800; letter-spacing: -0.02em;
  border-bottom: 1px solid var(--border); margin-bottom: 1rem;
}
.sidebar .logo span { color: var(--teal); }
.nav-item {
  display: block; padding: 0.7rem 1.5rem;
  color: var(--muted); font-size: 0.85rem; font-weight: 600;
  letter-spacing: 0.05em; text-transform: uppercase;
  transition: all 0.15s;
}
.nav-item:hover, .nav-item.active {
  color: var(--teal); background: rgba(31,204,138,0.06);
  text-decoration: none;
}
.nav-section {
  padding: 1.5rem 1.5rem 0.5rem;
  font-size: 0.7rem; color: var(--muted); letter-spacing: 0.1em;
  text-transform: uppercase; font-weight: 700;
}
.main { margin-left: 220px; padding: 2.5rem; }

/* Components */
.page-title {
  font-size: 2rem; font-weight: 800; letter-spacing: -0.04em;
  margin-bottom: 0.3rem;
}
.page-sub { color: var(--muted); font-size: 0.85rem; margin-bottom: 2rem; }

.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
.stat-card {
  background: var(--card); border: 1px solid var(--border);
  padding: 1.25rem 1.5rem; border-radius: 12px;
}
.stat-num { font-size: 2rem; font-weight: 800; color: var(--teal); font-family: 'DM Mono', monospace; }
.stat-label { font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-top: 0.25rem; }

.card {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 12px; overflow: hidden; margin-bottom: 1rem;
}
.card-header {
  padding: 1rem 1.5rem; border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 1rem;
  font-weight: 700; font-size: 0.9rem;
}
.card-body { padding: 1.5rem; }

table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th { text-align: left; padding: 0.6rem 1rem; color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; border-bottom: 1px solid var(--border); }
td { padding: 0.8rem 1rem; border-bottom: 1px solid rgba(30,42,35,0.5); vertical-align: middle; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: rgba(31,204,138,0.03); }

.badge {
  display: inline-block; padding: 0.2rem 0.6rem;
  border-radius: 6px; font-size: 0.7rem; font-weight: 700;
  letter-spacing: 0.05em; text-transform: uppercase;
}
.badge-active { background: rgba(31,204,138,0.15); color: var(--teal); }
.badge-review { background: rgba(255,159,67,0.15); color: var(--orange); }
.badge-hidden { background: rgba(255,77,109,0.15); color: var(--red); }

.btn {
  display: inline-block; padding: 0.4rem 0.9rem;
  border-radius: 7px; font-size: 0.78rem; font-weight: 700;
  cursor: pointer; border: none; font-family: 'Syne', sans-serif;
  letter-spacing: 0.03em; transition: all 0.15s;
}
.btn-primary { background: var(--teal); color: #000; }
.btn-primary:hover { background: #2df5a8; }
.btn-danger { background: rgba(255,77,109,0.15); color: var(--red); border: 1px solid rgba(255,77,109,0.3); }
.btn-danger:hover { background: rgba(255,77,109,0.25); }
.btn-warn { background: rgba(255,159,67,0.15); color: var(--orange); border: 1px solid rgba(255,159,67,0.3); }
.btn-warn:hover { background: rgba(255,159,67,0.25); }
.btn-sm { padding: 0.25rem 0.65rem; font-size: 0.72rem; }
.btn-group { display: flex; gap: 0.4rem; flex-wrap: wrap; }

.search-bar {
  display: flex; gap: 0.75rem; margin-bottom: 1.5rem; align-items: center;
}
.search-bar input {
  flex: 1; background: var(--card); border: 1px solid var(--border);
  color: var(--text); padding: 0.65rem 1rem; border-radius: 8px;
  font-family: 'Syne', sans-serif; font-size: 0.85rem; outline: none;
}
.search-bar input:focus { border-color: var(--teal-dim); }
.filter-tabs { display: flex; gap: 0.5rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
.filter-tab {
  padding: 0.4rem 1rem; border-radius: 8px; font-size: 0.78rem;
  font-weight: 700; cursor: pointer; border: 1px solid var(--border);
  background: var(--card); color: var(--muted); text-decoration: none;
  transition: all 0.15s;
}
.filter-tab:hover, .filter-tab.active {
  background: rgba(31,204,138,0.1); color: var(--teal);
  border-color: var(--teal-dim); text-decoration: none;
}

.photo-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 0.75rem; }
.photo-item { position: relative; border-radius: 8px; overflow: hidden; aspect-ratio: 4/3; }
.photo-item img { width: 100%; height: 100%; object-fit: cover; }
.photo-overlay {
  position: absolute; bottom: 0; left: 0; right: 0;
  background: linear-gradient(transparent, rgba(0,0,0,0.85));
  padding: 0.5rem; display: flex; justify-content: space-between; align-items: flex-end;
}
.photo-meta { font-size: 0.65rem; color: #ccc; font-family: 'DM Mono', monospace; }
.photo-actions { display: flex; gap: 0.3rem; }
.photo-status {
  position: absolute; top: 0.4rem; left: 0.4rem;
  background: rgba(0,0,0,0.7); border-radius: 4px;
  padding: 0.15rem 0.4rem; font-size: 0.6rem; font-weight: 700;
}

.comment-item {
  padding: 0.75rem; background: var(--surface); border-radius: 8px;
  margin-bottom: 0.5rem; display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem;
}
.comment-text { font-size: 0.85rem; flex: 1; }
.comment-meta { font-size: 0.7rem; color: var(--muted); margin-top: 0.2rem; font-family: 'DM Mono', monospace; }

.alert {
  padding: 0.75rem 1rem; border-radius: 8px; margin-bottom: 1rem; font-size: 0.85rem;
}
.alert-success { background: rgba(31,204,138,0.1); border: 1px solid var(--teal-dim); color: var(--teal); }
.alert-danger { background: rgba(255,77,109,0.1); border: 1px solid rgba(255,77,109,0.3); color: var(--red); }

.empty-state { text-align: center; padding: 3rem; color: var(--muted); }
.empty-state .icon { font-size: 2.5rem; margin-bottom: 0.75rem; }

/* Login */
.login-wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; }
.login-box { width: 380px; background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 2.5rem; }
.login-title { font-size: 1.8rem; font-weight: 800; margin-bottom: 0.3rem; }
.login-sub { color: var(--muted); font-size: 0.85rem; margin-bottom: 2rem; }
.form-group { margin-bottom: 1rem; }
.form-group label { display: block; font-size: 0.75rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); margin-bottom: 0.4rem; }
.form-group input { width: 100%; background: var(--surface); border: 1px solid var(--border); color: var(--text); padding: 0.7rem 1rem; border-radius: 8px; font-family: 'Syne', sans-serif; font-size: 0.9rem; outline: none; }
.form-group input:focus { border-color: var(--teal-dim); }

/* Confirm dialog */
.confirm-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 1000; align-items: center; justify-content: center; }
.confirm-overlay.show { display: flex; }
.confirm-box { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 2rem; max-width: 380px; width: 90%; text-align: center; }
.confirm-title { font-size: 1.1rem; font-weight: 800; margin-bottom: 0.5rem; }
.confirm-msg { color: var(--muted); font-size: 0.85rem; margin-bottom: 1.5rem; }
.confirm-btns { display: flex; gap: 0.75rem; justify-content: center; }
</style>
</head>
<body>
{% block body %}{% endblock %}
<div class="confirm-overlay" id="confirmOverlay">
  <div class="confirm-box">
    <div class="confirm-title" id="confirmTitle">確認操作</div>
    <div class="confirm-msg" id="confirmMsg"></div>
    <div class="confirm-btns">
      <button class="btn btn-danger" id="confirmOk">確認</button>
      <button class="btn" style="background:var(--surface);color:var(--muted)" onclick="closeConfirm()">取消</button>
    </div>
  </div>
</div>
<script>
let confirmUrl = '';
function showConfirm(url, title, msg) {
  confirmUrl = url;
  document.getElementById('confirmTitle').textContent = title;
  document.getElementById('confirmMsg').textContent = msg;
  document.getElementById('confirmOverlay').classList.add('show');
}
function closeConfirm() { document.getElementById('confirmOverlay').classList.remove('show'); }
document.getElementById('confirmOk').onclick = () => { window.location.href = confirmUrl; };
document.getElementById('confirmOverlay').onclick = (e) => { if(e.target === e.currentTarget) closeConfirm(); };

// Search filter
function filterTable(input, tableId) {
  const q = input.value.toLowerCase();
  document.querySelectorAll(`#${tableId} tbody tr`).forEach(tr => {
    tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}
</script>
</body>
</html>'''

SIDEBAR = '''
<aside class="sidebar">
  <div class="logo">🍱 Food Bot <span>Admin</span></div>
  <span class="nav-section">主要</span>
  <a href="/admin/" class="nav-item {% if active=='home' %}active{% endif %}">📊 總覽</a>
  <a href="/admin/restaurants" class="nav-item {% if active=='restaurants' %}active{% endif %}">🏪 店家管理</a>
  <a href="/admin/reported" class="nav-item {% if active=='reported' %}active{% endif %}">🚩 待審核</a>
  <span class="nav-section">其他</span>
  <a href="/admin/logout" class="nav-item">🚪 登出</a>
</aside>
'''

LOGIN_HTML = BASE_HTML.replace('{% block body %}{% endblock %}', '''
<div class="login-wrap">
  <div class="login-box">
    <div class="login-title">🍱 管理後台</div>
    <div class="login-sub">NSYSU Food Bot Admin Panel</div>
    {% if error %}<div class="alert alert-danger">{{ error }}</div>{% endif %}
    <form method="POST">
      <div class="form-group">
        <label>密碼</label>
        <input type="password" name="password" placeholder="輸入管理員密碼" autofocus>
      </div>
      <button type="submit" class="btn btn-primary" style="width:100%;padding:0.7rem">登入</button>
    </form>
  </div>
</div>
''')


def make_page(content, active=''):
    body = f'''
<div style="display:flex">
{SIDEBAR.replace("active==\'home\'", f"active==\'{active}\'").replace("active==\'restaurants\'", f"active==\'{active}\'").replace("active==\'reported\'", f"active==\'{active}\'")}
<main class="main">{content}</main>
</div>'''
    # 簡單替換 active 判斷
    body = body.replace(f"active=='{active}'", "True").replace("active==\'home\'", "False").replace("active==\'restaurants\'", "False").replace("active==\'reported\'", "False")
    return BASE_HTML.replace('{% block body %}{% endblock %}', body)


# ── Routes ────────────────────────────────────────────────────────────────────
@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin.index'))
        error = '密碼錯誤'
    return render_template_string(LOGIN_HTML, error=error)


@admin_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('admin.login'))


@admin_bp.route('/')
@login_required
def index():
    all_r = db.admin_get_all_restaurants()
    reported = db.admin_get_reported()
    total = len(all_r)
    active_count = sum(1 for r in all_r if r['status'] == 'active')
    hidden_count = sum(1 for r in all_r if r['status'] == 'hidden')
    review_count = len(reported['restaurants']) + len(reported['photos'])

    content = f'''
<div class="page-title">總覽</div>
<div class="page-sub">NSYSU Food Bot 管理後台</div>
<div class="stats">
  <div class="stat-card"><div class="stat-num">{total}</div><div class="stat-label">總店家數</div></div>
  <div class="stat-card"><div class="stat-num" style="color:var(--teal)">{active_count}</div><div class="stat-label">正常上架</div></div>
  <div class="stat-card"><div class="stat-num" style="color:var(--orange)">{review_count}</div><div class="stat-label">待審核</div></div>
  <div class="stat-card"><div class="stat-num" style="color:var(--red)">{hidden_count}</div><div class="stat-label">已下架</div></div>
</div>
'''
    if review_count > 0:
        content += f'''
<div class="card">
  <div class="card-header">🚩 待審核項目</div>
  <div class="card-body">
    <p style="color:var(--muted);font-size:0.85rem;margin-bottom:1rem">共 {review_count} 個項目需要審核</p>
    <a href="/admin/reported" class="btn btn-warn">前往審核 →</a>
  </div>
</div>'''

    return make_page(content, 'home')


@admin_bp.route('/restaurants')
@login_required
def restaurants():
    status_filter = request.args.get('status', 'all')
    if status_filter == 'all':
        all_r = db.admin_get_all_restaurants()
    else:
        all_r = db.admin_get_all_restaurants(status=status_filter)

    flash = request.args.get('msg', '')
    flash_html = f'<div class="alert alert-success">{flash}</div>' if flash else ''

    status_map = {'active': ('badge-active', '正常'), 'pending_review': ('badge-review', '待審核'), 'hidden': ('badge-hidden', '下架')}

    rows = ''
    for r in all_r:
        badge_cls, badge_text = status_map.get(r['status'], ('', r['status']))
        rows += f'''<tr>
          <td style="font-family:'DM Mono',monospace;color:var(--muted);font-size:0.75rem">{r['id']}</td>
          <td><a href="/admin/restaurant/{r['id']}">{r['name']}</a></td>
          <td style="color:var(--muted)">{r.get('category','')}</td>
          <td><span class="badge {badge_cls}">{badge_text}</span></td>
          <td style="color:var(--red);font-family:'DM Mono',monospace">{r['report_count']}</td>
          <td style="color:var(--muted);font-size:0.75rem">{r['created_at'][:10]}</td>
          <td>
            <div class="btn-group">
              <a href="/admin/restaurant/{r['id']}" class="btn btn-sm btn-primary">詳情</a>
              {'<a href="/admin/restaurant/'+str(r['id'])+'/restore" class="btn btn-sm btn-warn">恢復</a>' if r['status'] != 'active' else ''}
              {'<a href="javascript:void(0)" onclick="showConfirm(\'/admin/restaurant/'+str(r['id'])+'/hide\',\'隱藏店家\',\'確定要暫時下架「'+r['name']+'」嗎？\')" class="btn btn-sm btn-warn">下架</a>' if r['status'] == 'active' else ''}
              <a href="javascript:void(0)" onclick="showConfirm('/admin/restaurant/{r['id']}/delete','永久刪除','確定要永久刪除「{r['name']}」嗎？此操作無法復原，將刪除所有相關照片、評論和按讚。')" class="btn btn-sm btn-danger">刪除</a>
            </div>
          </td>
        </tr>'''

    content = f'''
<div class="page-title">店家管理</div>
<div class="page-sub">共 {len(all_r)} 家店</div>
{flash_html}
<div class="filter-tabs">
  <a href="/admin/restaurants?status=all" class="filter-tab {'active' if status_filter=='all' else ''}">全部</a>
  <a href="/admin/restaurants?status=active" class="filter-tab {'active' if status_filter=='active' else ''}">✅ 正常</a>
  <a href="/admin/restaurants?status=pending_review" class="filter-tab {'active' if status_filter=='pending_review' else ''}">⚠️ 待審核</a>
  <a href="/admin/restaurants?status=hidden" class="filter-tab {'active' if status_filter=='hidden' else ''}">🚫 下架</a>
</div>
<div class="search-bar">
  <input type="text" placeholder="搜尋店家名稱..." oninput="filterTable(this,'rTable')">
</div>
<div class="card">
  <table id="rTable">
    <thead><tr><th>ID</th><th>店家名稱</th><th>分類</th><th>狀態</th><th>檢舉數</th><th>建立日期</th><th>操作</th></tr></thead>
    <tbody>{rows if rows else '<tr><td colspan="7"><div class="empty-state"><div class="icon">🍽️</div>目前沒有店家</div></td></tr>'}</tbody>
  </table>
</div>'''
    return make_page(content, 'restaurants')


@admin_bp.route('/restaurant/<int:rid>')
@login_required
def restaurant_detail(rid):
    r = db.admin_get_restaurant(rid)
    if not r:
        return redirect(url_for('admin.restaurants'))

    flash = request.args.get('msg', '')
    flash_html = f'<div class="alert alert-success">{flash}</div>' if flash else ''

    status_map = {'active': ('badge-active', '✅ 正常'), 'pending_review': ('badge-review', '⚠️ 待審核'), 'hidden': ('badge-hidden', '🚫 下架')}
    badge_cls, badge_text = status_map.get(r['status'], ('', r['status']))

    # 照片 grid
    photos_html = ''
    for i, p in enumerate(r.get('photos', []), 1):
        ps_map = {'active': '', 'pending_review': '⚠️', 'hidden': '🚫'}
        ps = ps_map.get(p.get('status', 'active'), '')
        photos_html += f'''
        <div class="photo-item">
          <img src="{p['image_url']}" alt="photo {i}">
          <div class="photo-overlay">
            <span class="photo-meta">👍{p.get('like_count',0)} 🚩{p.get('report_count',0)}</span>
            <div class="photo-actions">
              {"<a href='/admin/photo/"+str(p['id'])+"/restore' class='btn btn-sm btn-warn' title='恢復'>↩</a>" if p.get('status') != 'active' else ""}
              <a href="javascript:void(0)" onclick="showConfirm('/admin/photo/{p['id']}/delete','刪除照片','確定要永久刪除這張照片嗎？')" class="btn btn-sm btn-danger" title="刪除">✕</a>
            </div>
          </div>
          {f'<div class="photo-status">{ps}</div>' if ps else ''}
        </div>'''

    # 評論列表
    comments = db.admin_get_comments(rid)
    comments_html = ''
    for c in comments:
        comments_html += f'''
        <div class="comment-item">
          <div>
            <div class="comment-text">{c['content']}</div>
            <div class="comment-meta">ID:{c['id']} · {c['created_at'][:16]}</div>
          </div>
          <a href="javascript:void(0)" onclick="showConfirm('/admin/comment/{c['id']}/delete','刪除評論','確定要刪除這則評論嗎？')" class="btn btn-sm btn-danger">刪除</a>
        </div>'''

    content = f'''
<div style="margin-bottom:1rem"><a href="/admin/restaurants" style="color:var(--muted);font-size:0.85rem">← 返回店家列表</a></div>
{flash_html}
<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;flex-wrap:wrap;margin-bottom:1.5rem">
  <div>
    <div class="page-title">{r['name']}</div>
    <div style="margin-top:0.3rem"><span class="badge {badge_cls}">{badge_text}</span> <span style="color:var(--muted);font-size:0.8rem">ID: {r['id']}</span></div>
  </div>
  <div class="btn-group">
    {'<a href="/admin/restaurant/'+str(rid)+'/restore" class="btn btn-warn">恢復上架</a>' if r['status'] != 'active' else ''}
    {'<a href="/admin/restaurant/'+str(rid)+'/hide" class="btn btn-warn">暫時下架</a>' if r['status'] == 'active' else ''}
    <a href="javascript:void(0)" onclick="showConfirm('/admin/restaurant/{rid}/delete','永久刪除','確定要永久刪除「{r['name']}」？此操作無法復原。')" class="btn btn-danger">永久刪除</a>
  </div>
</div>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1.5rem">
  <div class="card"><div class="card-body">
    <div style="color:var(--muted);font-size:0.75rem;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.75rem">基本資訊</div>
    <div style="font-size:0.85rem;line-height:2">
      <div>🏷️ {r.get('category','')}　{r.get('price_range','')}</div>
      <div>💬 {r['review']}</div>
      <div style="color:var(--muted)">建立：{r['created_at'][:16]}</div>
    </div>
  </div></div>
  <div class="card"><div class="card-body">
    <div style="color:var(--muted);font-size:0.75rem;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.75rem">統計數據</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem">
      <div class="stat-card" style="padding:0.75rem"><div class="stat-num" style="font-size:1.3rem">{r['view_count']}</div><div class="stat-label">觀看</div></div>
      <div class="stat-card" style="padding:0.75rem"><div class="stat-num" style="font-size:1.3rem">{r['like_count']}</div><div class="stat-label">按讚</div></div>
      <div class="stat-card" style="padding:0.75rem"><div class="stat-num" style="font-size:1.3rem">{r['comment_count']}</div><div class="stat-label">評論</div></div>
      <div class="stat-card" style="padding:0.75rem"><div class="stat-num" style="font-size:1.3rem;color:var(--red)">{r['report_count']}</div><div class="stat-label">檢舉</div></div>
    </div>
  </div></div>
</div>

<div class="card" style="margin-bottom:1rem">
  <div class="card-header">📷 照片（{r['photo_count']} 張）</div>
  <div class="card-body">
    {f'<div class="photo-grid">{photos_html}</div>' if photos_html else '<div class="empty-state"><div class="icon">📷</div>尚無照片</div>'}
  </div>
</div>

<div class="card">
  <div class="card-header">💬 評論（{r['comment_count']} 則）</div>
  <div class="card-body">
    {comments_html if comments_html else '<div class="empty-state"><div class="icon">💬</div>尚無評論</div>'}
  </div>
</div>'''
    return make_page(content, 'restaurants')


@admin_bp.route('/reported')
@login_required
def reported():
    data = db.admin_get_reported()
    flash = request.args.get('msg', '')
    flash_html = f'<div class="alert alert-success">{flash}</div>' if flash else ''

    r_rows = ''
    for r in data['restaurants']:
        st = '⚠️ 待審核' if r['status'] == 'pending_review' else '🚫 下架'
        r_rows += f'''<tr>
          <td style="font-family:'DM Mono',monospace;color:var(--muted);font-size:0.75rem">{r['id']}</td>
          <td><a href="/admin/restaurant/{r['id']}">{r['name']}</a></td>
          <td><span class="badge badge-review">{st}</span></td>
          <td style="color:var(--red);font-family:'DM Mono',monospace">{r['report_count']}</td>
          <td>
            <div class="btn-group">
              <a href="/admin/restaurant/{r['id']}/restore" class="btn btn-sm btn-warn">恢復</a>
              <a href="/admin/restaurant/{r['id']}/hide" class="btn btn-sm btn-warn">下架</a>
              <a href="javascript:void(0)" onclick="showConfirm('/admin/restaurant/{r['id']}/delete','永久刪除','確定刪除「{r['name']}」？')" class="btn btn-sm btn-danger">刪除</a>
            </div>
          </td>
        </tr>'''

    p_rows = ''
    for p in data['photos']:
        st = '⚠️ 待審核' if p['status'] == 'pending_review' else '🚫 下架'
        p_rows += f'''<tr>
          <td style="font-family:'DM Mono',monospace;color:var(--muted);font-size:0.75rem">{p['id']}</td>
          <td><a href="/admin/restaurant/{p['restaurant_id']}">{p['restaurant_name']}</a></td>
          <td><img src="{p['image_url']}" style="width:60px;height:45px;object-fit:cover;border-radius:4px"></td>
          <td><span class="badge badge-review">{st}</span></td>
          <td style="color:var(--red);font-family:'DM Mono',monospace">{p['report_count']}</td>
          <td>
            <div class="btn-group">
              <a href="/admin/photo/{p['id']}/restore" class="btn btn-sm btn-warn">恢復</a>
              <a href="javascript:void(0)" onclick="showConfirm('/admin/photo/{p['id']}/delete','刪除照片','確定要永久刪除此照片？')" class="btn btn-sm btn-danger">刪除</a>
            </div>
          </td>
        </tr>'''

    content = f'''
<div class="page-title">待審核</div>
<div class="page-sub">共 {len(data['restaurants'])+len(data['photos'])} 個項目需要審核</div>
{flash_html}

<div class="card" style="margin-bottom:1rem">
  <div class="card-header">🏪 待審核店家（{len(data['restaurants'])} 家）</div>
  <table>
    <thead><tr><th>ID</th><th>店家名稱</th><th>狀態</th><th>檢舉數</th><th>操作</th></tr></thead>
    <tbody>{r_rows if r_rows else '<tr><td colspan="5"><div class="empty-state" style="padding:1.5rem"><div class="icon">✅</div>沒有待審核店家</div></td></tr>'}</tbody>
  </table>
</div>

<div class="card">
  <div class="card-header">📷 待審核照片（{len(data['photos'])} 張）</div>
  <table>
    <thead><tr><th>ID</th><th>所屬店家</th><th>照片</th><th>狀態</th><th>檢舉數</th><th>操作</th></tr></thead>
    <tbody>{p_rows if p_rows else '<tr><td colspan="6"><div class="empty-state" style="padding:1.5rem"><div class="icon">✅</div>沒有待審核照片</div></td></tr>'}</tbody>
  </table>
</div>'''
    return make_page(content, 'reported')


# ── Action Routes ─────────────────────────────────────────────────────────────
@admin_bp.route('/restaurant/<int:rid>/hide')
@login_required
def restaurant_hide(rid):
    db.admin_hide_restaurant(rid)
    return redirect(url_for('admin.restaurants', msg='已下架'))

@admin_bp.route('/restaurant/<int:rid>/restore')
@login_required
def restaurant_restore(rid):
    db.admin_restore_restaurant(rid)
    return redirect(url_for('admin.restaurant_detail', rid=rid, msg='已恢復上架'))

@admin_bp.route('/restaurant/<int:rid>/delete')
@login_required
def restaurant_delete(rid):
    db.admin_delete_restaurant(rid)
    return redirect(url_for('admin.restaurants', msg='已永久刪除'))

@admin_bp.route('/photo/<int:pid>/restore')
@login_required
def photo_restore(pid):
    p = db.get_photo_by_id(pid)
    db.admin_restore_photo(pid)
    rid = p['restaurant_id'] if p else None
    if rid:
        return redirect(url_for('admin.restaurant_detail', rid=rid, msg='照片已恢復'))
    return redirect(url_for('admin.reported', msg='照片已恢復'))

@admin_bp.route('/photo/<int:pid>/delete')
@login_required
def photo_delete(pid):
    p = db.get_photo_by_id(pid)
    rid = p['restaurant_id'] if p else None
    db.admin_delete_photo(pid)
    if rid:
        return redirect(url_for('admin.restaurant_detail', rid=rid, msg='照片已刪除'))
    return redirect(url_for('admin.reported', msg='照片已刪除'))

@admin_bp.route('/comment/<int:cid>/delete')
@login_required
def comment_delete(cid):
    # 找評論所屬店家
    with db._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT restaurant_id FROM comments WHERE id=%s", (cid,))
            row = cur.fetchone()
    rid = row['restaurant_id'] if row else None
    db.admin_delete_comment(cid)
    if rid:
        return redirect(url_for('admin.restaurant_detail', rid=rid, msg='評論已刪除'))
    return redirect(url_for('admin.restaurants'))
