/*
 * brand-bridge.js — پل برند پویا برای PersianMeet
 * -------------------------------------------------
 * این فایل «به همراه» dist فروشنده سرو می‌شود ($DIST/brand/brand-bridge.js) و با یک
 * <script> در <head> لود می‌شود. هیچ فایلی از dist فروشنده ویرایش نمی‌شود مگر همان
 * تزریق یک‌خطی توسط ابزار نصب برند (deploy/../branding/install-brand.py).
 *
 * کارها:
 *   1) خواندن /brand/brand.json و اعمال نام/توضیح/رنگ/آیکون/لوگو روی صفحه (بدون build)
 *   2) وصله‌ی دیکشنری i18n (window.translate) قبل از اجرای Vue — نام برند همه‌جا عوض می‌شود
 *   3) جایگزینی رشته‌های برند داخل متن صفحه (MutationObserver) — مثلاً در لاگین و فوتر پنل
 *   4) منیفست PWA پویا (blob) با نام و آیکون برند
 *   5) اگر کاربر ادمینِ محصول باشد (Role==1 در localStorage و صفحه‌ی /dash)، دکمه‌ی
 *      «⚙️ تنظیمات برند» نمایش داده می‌شود؛ با آن می‌توان نام/توضیح/رنگ را تغییر داد و
 *      آیکون و لوگو را به‌صورت فایل آپلود کرد. ذخیره از طریق /brand-api/save انجام می‌شود
 *      (سرویس classroom-manager پشت nginx هم‌مبدأ).
 */
(function () {
  'use strict';
  var BRAND_JSON_URL = '/brand/brand.json';
  var SAVE_URL = '/brand-api/save';
  var ADMIN_CHECK_URL = '/brand-api/admin-check';
  var TAG = '[brand]';

  var LITERALS = ['PERSIAN MEET', 'PersianMeet', 'persianmeet', 'persiameet', 'persiameet',
    'پرشین میت', 'پرشین‌میت', 'learn100', 'Learn100', 'learn-100', 'persian meet'];

  function safeJsonParse(s) { try { return JSON.parse(s); } catch (e) { return null; } }

  function debounce(fn, wait) {
    var t = null;
    return function () {
      var args = arguments, self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, wait);
    };
  }

  function replaceLiterals(text) {
    if (!text) return text;
    var out = text;
    for (var i = 0; i < LITERALS.length; i++) {
      var lit = LITERALS[i];
      // حساسیت به حروف: انگلیسی‌ها بدون حساسیت، فارسی با تطابق دقیق
      if (/^[\x20-\x7E]+$/.test(lit)) {
        out = out.replace(new RegExp(lit.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), current.siteName);
      } else {
        out = out.split(lit).join(current.siteName);
      }
    }
    return out;
  }

  var current = {
    version: 0,
    siteName: 'PersianMeet',
    siteNameShort: 'PersianMeet',
    tagline: '',
    primaryColor: '#000000',
    hasLogo: false,
    hasIcon: false
  };

  /* ---------- 1) اعمال برند روی chrome مرورگر ---------- */
  function applyChrome(brand) {
    try { document.title = replaceLiterals(document.title) === document.title && brand.siteName
        ? brand.siteName : replaceLiterals(document.title); } catch (e) { /* noop */ }
    try { document.title = brand.siteName; } catch (e) {}
    var metas = document.querySelectorAll('meta[name="theme-color"]');
    metas.forEach(function (m) { m.setAttribute('content', brand.primaryColor || '#000000'); });
    var desc = document.querySelector('meta[name="description"]');
    if (desc && brand.tagline) desc.setAttribute('content', brand.tagline);
    var ogt = document.querySelector('meta[property="og:title"]');
    if (ogt) ogt.setAttribute('content', brand.siteName);
    // favicon / apple-touch-icon
    if (brand.hasIcon) {
      var v = '?v=' + (brand.version || '1');
      var links = document.querySelectorAll('link[rel="icon"], link[rel="shortcut icon"], link[rel="apple-touch-icon"]');
      links.forEach(function (l) {
        l.setAttribute('href', (l.getAttribute('rel') === 'apple-touch-icon' ? '/brand/icon.png' : '/brand/icon.png') + v);
        l.removeAttribute('sizes');
      });
    }
    // رنگ اصلی در CSS variables رایج PrimeVue/Tailwind (بی‌خطر اگر استفاده نشوند)
    try {
      var root = document.documentElement;
      root.style.setProperty('--pm-brand-primary', brand.primaryColor || '#000000');
      root.style.setProperty('--p-primary-color', brand.primaryColor || '#000000');
      root.style.setProperty('--primary-color', brand.primaryColor || '#000000');
    } catch (e) {}
  }

  /* ---------- 2) وصله‌ی دیکشنری i18n (window.translate) ---------- */
  function patchTranslateDict(brand) {
    try {
      var t = window.translate;
      if (!t || typeof t !== 'object') return;
      var langs = Object.keys(t);
      for (var i = 0; i < langs.length; i++) {
        var lang = t[langs[i]];
        if (!lang || typeof lang !== 'object') continue;
        walk(lang);
      }
      function walk(obj) {
        for (var k in obj) {
          if (!Object.prototype.hasOwnProperty.call(obj, k)) continue;
          var v = obj[k];
          if (typeof v === 'string') obj[k] = replaceLiterals(v);
          else if (v && typeof v === 'object') walk(v);
        }
      }
      // WelcomeToSite را با نام برند بازنویسی کن (ساختار جمله حفظ نمی‌شود؛ این متن بازاریابی است)
      ['fa', 'en', 'ar', 'hi', 'az', 'zh', 'fr', 'de', 'az2'].forEach(function (lg) {
        if (t[lg] && typeof t[lg] === 'object') {
          if (t[lg].WelcomeToSite) t[lg].WelcomeToSite = brand.siteName;
          if (brand.tagline && t[lg].welcome_subtitle) t[lg].welcome_subtitle = brand.tagline;
        }
      });
      console.info(TAG, 'i18n dictionary patched');
    } catch (e) { console.warn(TAG, 'translate patch failed', e); }
  }

  /* ---------- 3) جایگزینی متن‌های روی صفحه + نگهبان DOM ---------- */
  var TEXT_SKIP = { SCRIPT: 1, STYLE: 1, INPUT: 1, TEXTAREA: 1, SELECT: 1, CODE: 1, PRE: 1 };
  function replaceInTextNodes(root) {
    try {
      var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode: function (node) {
          var p = node.parentNode;
          if (!p || TEXT_SKIP[p.nodeName]) return NodeFilter.FILTER_REJECT;
          if (p.closest && p.closest('#pm-brand-modal, #pm-brand-fab')) return NodeFilter.FILTER_REJECT;
          return node.nodeValue && replaceLiterals(node.nodeValue) !== node.nodeValue
            ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
        }
      });
      var nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      nodes.forEach(function (n) { n.nodeValue = replaceLiterals(n.nodeValue); });
      // لوگوهای <img> که مسیرشان به برند اشاره می‌کند
      if (current.hasLogo) {
        var imgs = (root.querySelectorAll ? root.querySelectorAll('img') : []);
        imgs.forEach(function (img) {
          var src = (img.getAttribute('src') || '').toLowerCase();
          var alt = (img.getAttribute('alt') || '');
          if (/logo|brand|learn100|persianmeet/.test(src) || replaceLiterals(alt) !== alt) {
            img.setAttribute('src', '/brand/logo.png?v=' + (current.version || '1'));
            img.setAttribute('alt', current.siteName);
          }
        });
      }
    } catch (e) { /* noop */ }
  }
  var observerGuarded = false;
  function startObserver() {
    if (observerGuarded) return;
    observerGuarded = true;
    var obs = new MutationObserver(debounce(function () { replaceInTextNodes(document.body); }, 120));
    obs.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  }

  /* ---------- 4) منیفست PWA پویا ---------- */
  function applyManifest(brand) {
    try {
      var icon = brand.hasIcon ? '/brand/icon.png?v=' + (brand.version || '1') : '/pwa-512x512.png';
      var icon192 = brand.hasIcon ? '/brand/icon.png?v=' + (brand.version || '1') : '/pwa-192x192.png';
      var manifest = {
        name: brand.siteName,
        short_name: brand.siteNameShort || brand.siteName,
        description: brand.tagline || brand.siteName,
        start_url: '/',
        display: 'standalone',
        background_color: '#ffffff',
        theme_color: brand.primaryColor || '#000000',
        lang: 'fa',
        scope: '/',
        icons: [
          { src: icon192, sizes: '192x192', type: 'image/png' },
          { src: icon, sizes: '512x512', type: 'image/png' }
        ]
      };
      var blob = new Blob([JSON.stringify(manifest)], { type: 'application/manifest+json' });
      var url = URL.createObjectURL(blob);
      document.querySelectorAll('link[rel="manifest"]').forEach(function (l) { l.setAttribute('href', url); });
    } catch (e) { /* noop */ }
  }

  /* ---------- 5) دکمه و مودال «تنظیمات برند» (فقط ادمین محصول در /dash) ---------- */
  function getProductUser() {
    var u = safeJsonParse(localStorage.getItem('user'));
    if (u && typeof u === 'object') return u;
    return null;
  }
  function isProductAdmin() {
    var u = getProductUser();
    if (!u) return false;
    var role = u.Role != null ? u.Role : u.role;
    return Number(role) === 1 || role === 'admin' || role === 'ADMIN';
  }
  function onDash() { return /^\/dash(\/|$)/.test(window.location.pathname); }

  var fab = null, modal = null;
  function ensureFab() {
    if (fab) return fab;
    fab = document.createElement('button');
    fab.id = 'pm-brand-fab';
    fab.type = 'button';
    fab.innerHTML = '⚙️ <span>تنظیمات برند</span>';
    fab.style.cssText = 'position:fixed;bottom:18px;left:18px;z-index:2147483000;' +
      'background:#111827;color:#f9fafb;border:1px solid #374151;border-radius:9999px;' +
      'padding:10px 16px;font:500 13px/1.4 Vazir,Tahoma,sans-serif;cursor:pointer;' +
      'box-shadow:0 6px 24px rgba(0,0,0,.35);display:none;align-items:center;gap:8px';
    fab.addEventListener('click', openModal);
    document.body.appendChild(fab);
    return fab;
  }
  function updateFabVisibility() {
    try { ensureFab().style.display = (isProductAdmin() && onDash()) ? 'flex' : 'none'; } catch (e) {}
  }

  function buildModal() {
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'pm-brand-modal';
    modal.dir = 'rtl';
    modal.style.cssText = 'position:fixed;inset:0;z-index:2147483001;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.55);padding:16px';
    modal.innerHTML =
      '<div style="background:#fff;color:#111;max-width:520px;width:100%;max-height:92vh;overflow:auto;border-radius:16px;padding:22px 22px 18px;font-family:Vazir,Tahoma,sans-serif;box-shadow:0 20px 60px rgba(0,0,0,.4)">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">' +
          '<h2 style="font-size:18px;font-weight:700;margin:0">⚙️ تنظیمات برند پلتفرم</h2>' +
          '<button id="pm-brand-close" type="button" style="border:none;background:#f3f4f6;border-radius:8px;padding:6px 12px;cursor:pointer;font-size:14px">بستن ✕</button>' +
        '</div>' +
        '<p style="font-size:12px;color:#6b7280;margin:0 0 14px">این تنظیمات برای همه‌ی کاربران اعمال می‌شود و بلافاصله بعد از ذخیره دیده می‌شود.</p>' +
        '<label style="display:block;font-size:13px;margin-bottom:4px">نام پلتفرم *</label>' +
        '<input id="pm-brand-siteName" type="text" maxlength="64" style="width:100%;box-sizing:border-box;padding:9px 12px;border:1px solid #d1d5db;border-radius:10px;margin-bottom:12px;font-size:14px"/>' +
        '<label style="display:block;font-size:13px;margin-bottom:4px">نام کوتاه (برای PWA / آیکون روی موبایل)</label>' +
        '<input id="pm-brand-siteNameShort" type="text" maxlength="24" style="width:100%;box-sizing:border-box;padding:9px 12px;border:1px solid #d1d5db;border-radius:10px;margin-bottom:12px;font-size:14px"/>' +
        '<label style="display:block;font-size:13px;margin-bottom:4px">توضیح کوتاه / تگ‌لاین</label>' +
        '<input id="pm-brand-tagline" type="text" maxlength="140" style="width:100%;box-sizing:border-box;padding:9px 12px;border:1px solid #d1d5db;border-radius:10px;margin-bottom:12px;font-size:14px"/>' +
        '<label style="display:block;font-size:13px;margin-bottom:4px">رنگ اصلی (theme-color و اکسنت)</label>' +
        '<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">' +
          '<input id="pm-brand-color" type="color" style="width:56px;height:40px;border:1px solid #d1d5db;border-radius:8px;background:#fff;cursor:pointer"/>' +
          '<code id="pm-brand-color-hex" style="color:#374151;font-size:13px">#000000</code>' +
        '</div>' +
        '<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:14px">' +
          '<div style="flex:1;min-width:200px">' +
            '<label style="display:block;font-size:13px;margin-bottom:4px">لوگو (PNG/SVG/WEBP)</label>' +
            '<input id="pm-brand-logo" type="file" accept="image/*" style="font-size:12px;width:100%"/>' +
            '<div id="pm-brand-logo-preview" style="margin-top:8px;height:64px;display:flex;align-items:center;justify-content:center;background:#f9fafb;border:1px dashed #d1d5db;border-radius:10px;overflow:hidden"><span style="color:#9ca3af;font-size:12px">پیش‌نمایش لوگو</span></div>' +
          '</div>' +
          '<div style="flex:1;min-width:200px">' +
            '<label style="display:block;font-size:13px;margin-bottom:4px">آیکون (مربع، ترجیحاً ۵۱۲×۵۱۲)</label>' +
            '<input id="pm-brand-icon" type="file" accept="image/*" style="font-size:12px;width:100%"/>' +
            '<div id="pm-brand-icon-preview" style="margin-top:8px;height:64px;display:flex;align-items:center;justify-content:center;background:#f9fafb;border:1px dashed #d1d5db;border-radius:10px;overflow:hidden"><span style="color:#9ca3af;font-size:12px">پیش‌نمایش آیکون</span></div>' +
          '</div>' +
        '</div>' +
        '<div id="pm-brand-status" style="min-height:20px;font-size:13px;margin-bottom:10px;color:#374151"></div>' +
        '<div style="display:flex;gap:10px;justify-content:flex-end">' +
          '<button id="pm-brand-cancel" type="button" style="border:1px solid #d1d5db;background:#fff;border-radius:10px;padding:9px 18px;cursor:pointer">انصراف</button>' +
          '<button id="pm-brand-save" type="button" style="border:none;background:#111827;color:#fff;border-radius:10px;padding:9px 22px;cursor:pointer;font-weight:600">ذخیره برند</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(modal);

    modal.addEventListener('click', function (ev) { if (ev.target === modal) closeModal(); });
    modal.querySelector('#pm-brand-close').addEventListener('click', closeModal);
    modal.querySelector('#pm-brand-cancel').addEventListener('click', closeModal);
    var colorInput = modal.querySelector('#pm-brand-color');
    var hex = modal.querySelector('#pm-brand-color-hex');
    colorInput.addEventListener('input', function () { hex.textContent = colorInput.value; });
    bindPreview('#pm-brand-logo', '#pm-brand-logo-preview');
    bindPreview('#pm-brand-icon', '#pm-brand-icon-preview');
    modal.querySelector('#pm-brand-save').addEventListener('click', saveBrand);
    return modal;
  }
  function bindPreview(inputSel, previewSel) {
    var input = modal.querySelector(inputSel), preview = modal.querySelector(previewSel);
    input.addEventListener('change', function () {
      var f = input.files && input.files[0];
      if (!f) return;
      if (f.size > 2 * 1024 * 1024) { preview.innerHTML = '<span style="color:#dc2626;font-size:12px">حجم فایل بیش از ۲ مگابایت است</span>'; input.value = ''; return; }
      var reader = new FileReader();
      reader.onload = function () {
        preview.innerHTML = '<img alt="preview" src="' + reader.result + '" style="max-height:100%;max-width:100%;object-fit:contain"/>';
      };
      reader.readAsDataURL(f);
    });
  }
  function openModal() {
    buildModal();
    var u = getProductUser();
    modal.querySelector('#pm-brand-siteName').value = current.siteName || '';
    modal.querySelector('#pm-brand-siteNameShort').value = current.siteNameShort || '';
    modal.querySelector('#pm-brand-tagline').value = current.tagline || '';
    var c = current.primaryColor || '#000000';
    modal.querySelector('#pm-brand-color').value = /^#[0-9a-fA-F]{6}$/.test(c) ? c : '#000000';
    modal.querySelector('#pm-brand-color-hex').textContent = modal.querySelector('#pm-brand-color').value;
    modal.querySelector('#pm-brand-status').textContent = u && u.UserName ? ('ذخیره به نام: ' + u.UserName) : '';
    modal.style.display = 'flex';
  }
  function closeModal() { if (modal) modal.style.display = 'none'; }
  function setStatus(text, isError) {
    var el = modal && modal.querySelector('#pm-brand-status');
    if (!el) return;
    el.textContent = text;
    el.style.color = isError ? '#dc2626' : '#047857';
  }
  function saveBrand() {
    var u = getProductUser();
    var guid = u && (u.GUID || u.guid || '');
    if (!guid) { setStatus('شناسه‌ی ادمین (GUID) در localStorage پیدا نشد؛ دوباره وارد شوید.', true); return; }
    var siteName = modal.querySelector('#pm-brand-siteName').value.trim();
    if (!siteName) { setStatus('نام پلتفرم الزامی است.', true); return; }
    var fd = new FormData();
    fd.append('siteName', siteName);
    fd.append('siteNameShort', modal.querySelector('#pm-brand-siteNameShort').value.trim());
    fd.append('tagline', modal.querySelector('#pm-brand-tagline').value.trim());
    fd.append('primaryColor', modal.querySelector('#pm-brand-color').value);
    var logo = modal.querySelector('#pm-brand-logo').files[0];
    var icon = modal.querySelector('#pm-brand-icon').files[0];
    if (logo) fd.append('logo', logo);
    if (icon) fd.append('icon', icon);
    setStatus('در حال ذخیره…');
    fetch(SAVE_URL, { method: 'POST', headers: { 'X-PM-GUID': guid }, body: fd })
      .then(function (r) { return r.json().catch(function () { return { ok: false, error: 'پاسخ نامعتبر از سرور' }; }).then(function (j) { return { status: r.status, body: j }; }); })
      .then(function (res) {
        if (res.status === 200 && res.body && res.body.ok) {
          setStatus('ذخیره شد ✓ صفحه در حال بارگذاری مجدد…');
          setTimeout(function () { window.location.reload(); }, 800);
        } else if (res.status === 403 || res.status === 401) {
          setStatus('دسترسی رد شد: فقط ادمین محصول (نقش ۱) می‌تواند برند را تغییر دهد.', true);
        } else {
          setStatus('خطا: ' + ((res.body && res.body.error) || ('HTTP ' + res.status)), true);
        }
      })
      .catch(function (e) { setStatus('اتصال به /brand-api برقرار نشد: ' + e, true); });
  }

  /* ---------- راه‌اندازی ---------- */
  function boot(brand) {
    current = Object.assign(current, brand || {});
    applyChrome(current);
    patchTranslateDict(current);
    applyManifest(current);
    var run = function () { replaceInTextNodes(document.body || document.documentElement); startObserver(); updateFabVisibility(); };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run); else run();
    // SPA مسیر را بدون reload عوض می‌کند؛ فقط هر ۱.۵ ثانیه وضعیت دکمه را چک کن
    setInterval(updateFabVisibility, 1500);
    window.addEventListener('popstate', updateFabVisibility);
  }

  fetch(BRAND_JSON_URL + '?_=' + Date.now(), { cache: 'no-store' })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (brand) { boot(brand || {}); })
    .catch(function () { boot({}); });
})();
