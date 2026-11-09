# -*- coding: utf-8 -*-
"""رندر HTML — تم فارسی راست‌چین، بدون هیچ فونت/CSS بیرونی."""
from html import escape

_CSS = """
:root{--bg:#0b1220;--panel:#111a2e;--panel2:#0f1830;--line:#1f2b4d;--text:#e8ecf8;--muted:#8fa1c7;--accent:#22d3ee;--accent2:#0ea5e9;--ok:#34d399;--warn:#fbbf24;--danger:#f87171}
*{box-sizing:border-box}
body{margin:0;font-family:Tahoma,'IRANSans','Segoe UI',sans-serif;background:radial-gradient(1200px 600px at 80% -10%,#16224a 0,var(--bg) 55%);color:var(--text);min-height:100vh}
header{display:flex;align-items:center;justify-content:space-between;padding:.9rem 1.4rem;background:rgba(13,20,38,.85);border-bottom:1px solid var(--line);backdrop-filter:blur(6px);position:sticky;top:0;z-index:10}
.brand{font-weight:800;font-size:1.05rem;color:var(--accent);display:flex;gap:.5rem;align-items:center}
.brand small{color:var(--muted);font-weight:400;font-size:.75rem}
nav a{color:var(--text);text-decoration:none;margin-inline-start:1rem;font-size:.9rem}
nav a:hover{color:var(--accent)}
main{max-width:1100px;margin:1.4rem auto;padding:0 1rem}
.card{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);border-radius:16px;padding:1.1rem 1.2rem;margin-bottom:1.1rem;box-shadow:0 10px 30px rgba(0,0,0,.35)}
h1{font-size:1.35rem;margin:.2rem 0 1rem}h2{font-size:1.05rem;margin:.2rem 0 .8rem;color:var(--accent)}
table{width:100%;border-collapse:collapse;font-size:.88rem}
th,td{padding:.55rem .6rem;border-bottom:1px solid var(--line);text-align:right}
th{color:var(--muted);font-weight:600}
tr:nth-child(even) td{background:rgba(255,255,255,.02)}
.btn{display:inline-block;border:1px solid var(--line);background:#182344;color:var(--text);padding:.5rem .95rem;border-radius:10px;text-decoration:none;cursor:pointer;font-size:.88rem;transition:.15s;font-family:inherit}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-primary{background:linear-gradient(135deg,var(--accent2),var(--accent));border:none;color:#04222b;font-weight:700}
.btn-danger{background:linear-gradient(135deg,#f43f5e,#fb7185);border:none;color:#2b0505;font-weight:700}
.btn-ok{background:linear-gradient(135deg,#10b981,#34d399);border:none;color:#032b1d;font-weight:700}
.input,select{width:100%;background:#0d1730;border:1px solid var(--line);color:var(--text);border-radius:10px;padding:.55rem .65rem;font-family:inherit;font-size:.9rem}
.input:focus{outline:2px solid var(--accent2)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:1rem}
.pill{display:inline-block;padding:.15rem .6rem;border-radius:99px;font-size:.72rem;border:1px solid var(--line);color:var(--muted)}
.pill.ok{color:var(--ok);border-color:var(--ok)}.pill.warn{color:var(--warn);border-color:var(--warn)}.pill.danger{color:var(--danger);border-color:var(--danger)}
.msg{padding:.6rem .8rem;border-radius:10px;margin-bottom:1rem;font-size:.88rem}
.msg-ok{background:rgba(52,211,153,.12);border:1px solid var(--ok);color:var(--ok)}
.msg-err{background:rgba(248,113,113,.12);border:1px solid var(--danger);color:var(--danger)}
.muted{color:var(--muted);font-size:.82rem}
code.link{background:#0d1730;border:1px solid var(--line);border-radius:8px;padding:.3rem .5rem;font-size:.78rem;word-break:break-all;direction:ltr;display:block;text-align:left}
.row{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}
.copy-btn{cursor:pointer}
footer{text-align:center;color:var(--muted);font-size:.72rem;padding:1.5rem}
@media print{header,nav,.no-print{display:none!important}body{background:#fff;color:#000}.card{border:none;box-shadow:none}}
"""

NAV = """<nav>
  <a href="/">داشبورد</a>
  {admin_links}
  <a href="/logout">خروج ({full_name})</a>
</nav>"""


def page(title: str, body: str, user: dict | None = None) -> bytes:
    admin_links = '<a href="/admin/import">ایمپورت CSV</a><a href="/admin/teachers">دبیران</a><a href="/admin/students">دانش‌آموزان</a><a href="/admin/classes">کلاس‌ها</a>' if user and user.get("role") == "admin" else ""
    nav = NAV.format(admin_links=admin_links, full_name=escape(user["full_name"])) if user else ""
    html = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; style-src 'unsafe-inline'; img-src 'self' data:; script-src 'unsafe-inline'">
<title>{escape(title)} — سامانه‌ی مدیریت کلاس</title>
<style>{_CSS}</style>
</head>
<body>
<header>
  <div class="brand">🎓 سامانه‌ی مدیریت کلاس <small>PersianMeet</small></div>
  {nav}
</header>
<main>
{body}
</main>
<footer>bacirat-function · مدیریت کلاس PersianMeet — میزبانی اختصاصی</footer>
<script>
document.querySelectorAll('.copy-btn').forEach(b=>b.addEventListener('click',()=>{{
  const t=b.parentElement.querySelector('code'); if(!t) return;
  navigator.clipboard.writeText(t.textContent.trim()).then(()=>{{b.textContent='✓ کپی شد'; setTimeout(()=>b.textContent='📋 کپی',1500);}});
}}));
</script>
</body></html>"""
    return html.encode("utf-8")


def msg_box(kind: str, text: str) -> str:
    cls = "msg-ok" if kind == "ok" else "msg-err"
    return f'<div class="msg {cls}">{escape(text)}</div>'
