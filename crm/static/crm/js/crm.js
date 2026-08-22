/* Matrix CRM — shared JS utilities (vanilla, fetch-based) */
(function () {
  'use strict';

  const csrf = () => {
    const el = document.querySelector('[name=csrfmiddlewaretoken]');
    return el ? el.value : '';
  };

  const getCookie = (name) => {
    const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
    return match ? decodeURIComponent(match[1]) : '';
  };

  const toast = (msg, type = 'info') => {
    let stack = document.querySelector('.toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.className = 'toast-stack';
      document.body.appendChild(stack);
    }
    const t = document.createElement('div');
    t.className = 'crm-toast toast-' + type;
    t.textContent = msg;
    stack.appendChild(t);
    setTimeout(() => t.remove(), 4200);
  };

  async function api(url, method = 'GET', data = null) {
    const opts = { method, headers: {} };
    if (data !== null) {
      opts.headers['X-Requested-With'] = 'XMLHttpRequest';
      if (data instanceof FormData) {
        opts.body = data;
      } else {
        opts.headers['Content-Type'] = 'application/x-www-form-urlencoded';
        opts.body = new URLSearchParams(Object.entries(data).filter(([, v]) => v !== null && v !== undefined));
      }
    }
    const csrfToken = getCookie('csrftoken');
    if (csrfToken) opts.headers['X-CSRFToken'] = csrfToken;
    const res = await fetch(url, opts);
    const ct = res.headers.get('content-type') || '';
    const body = ct.includes('json') ? await res.json() : await res.text();
    if (!res.ok) {
      throw new Error((body && (body.error || body.detail)) || ('HTTP ' + res.status));
    }
    return body;
  }

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* fall through */ }
    }
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand('copy');
    } catch (e) {
      ok = false;
    } finally {
      ta.remove();
    }
    return ok;
  }

  /* ---------- modals & drawers ---------- */
  const modalEl = document.getElementById('crmModal');
  const modalBackdrop = document.getElementById('crmModalBackdrop');
  const drawerEl = document.getElementById('crmDrawer');
  const drawerBackdrop = document.getElementById('crmDrawerBackdrop');
  let restoreFocusEl = null;
  let scrollYPos = 0;

  /* Lock scrolling on BOTH <html> and <body> plus a position:fixed fallback —
     body{overflow:hidden} alone does NOT stop iOS Safari from scrolling, which
     made fixed modal/drawer elements appear off-screen on mobile. */
  function lockScroll() {
    if (document.body.dataset.crmLocked) return;
    document.body.dataset.crmLocked = '1';
    scrollYPos = window.scrollY;
    document.documentElement.style.overflow = 'hidden';
    document.body.style.overflow = 'hidden';
    document.body.style.overscrollBehavior = 'none';
    document.body.style.position = 'fixed';
    document.body.style.left = '0';
    document.body.style.right = '0';
    document.body.style.top = '-' + scrollYPos + 'px';
  }
  function unlockScroll() {
    if (!document.body.dataset.crmLocked) return;
    delete document.body.dataset.crmLocked;
    const y = scrollYPos || 0;
    document.documentElement.style.overflow = '';
    document.body.style.overflow = '';
    document.body.style.overscrollBehavior = '';
    document.body.style.position = '';
    document.body.style.left = '';
    document.body.style.right = '';
    document.body.style.top = '';
    window.scrollTo(0, y);
  }
  function focusFirst(el) {
    const f = el.querySelector('input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])');
    if (f) { try { f.focus(); } catch (e) {} }
  }

  function openModal(title, html, opts) {
    if (!modalEl) return;
    restoreFocusEl = document.activeElement;
    document.getElementById('crmModalTitle').textContent = title;
    document.getElementById('crmModalBody').innerHTML = html;
    modalEl.hidden = false;
    modalBackdrop.hidden = false;
    lockScroll();
    initDatepickers(modalEl);
    if (!opts || !opts.noFocus) focusFirst(modalEl);
  }
  function closeModal() {
    if (!modalEl) return;
    modalEl.hidden = true;
    modalBackdrop.hidden = true;
    unlockScroll();
    if (restoreFocusEl) { try { restoreFocusEl.focus(); } catch (e) {} restoreFocusEl = null; }
  }
  function openDrawer(title, html) {
    if (!drawerEl) return;
    restoreFocusEl = document.activeElement;
    document.getElementById('crmDrawerTitle').textContent = title;
    document.getElementById('crmDrawerBody').innerHTML = html;
    drawerEl.hidden = false;
    drawerBackdrop.hidden = false;
    lockScroll();
    initDatepickers(drawerEl);
  }
  function closeDrawer() {
    if (!drawerEl) return;
    drawerEl.hidden = true;
    drawerBackdrop.hidden = true;
    unlockScroll();
    if (restoreFocusEl) { try { restoreFocusEl.focus(); } catch (e) {} restoreFocusEl = null; }
  }
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!modalEl.hidden) closeModal();
    else if (!drawerEl.hidden) closeDrawer();
  });
  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-close-modal]')) closeModal();
    if (e.target.closest('[data-close-drawer]')) closeDrawer();
    if (e.target === modalBackdrop) closeModal();
    if (e.target === drawerBackdrop) closeDrawer();
    if (e.target.closest('#bellBtn')) {
      document.getElementById('bellPanel')?.classList.toggle('open');
      document.getElementById('profilePanel')?.classList.remove('open');
    } else if (e.target.closest('#profileBtn')) {
      document.getElementById('profilePanel')?.classList.toggle('open');
      document.getElementById('bellPanel')?.classList.remove('open');
    } else if (!e.target.closest('.bell-panel') && !e.target.closest('.profile-panel')) {
      document.getElementById('bellPanel')?.classList.remove('open');
      document.getElementById('profilePanel')?.classList.remove('open');
    }
  });

  /* ---------- sidebar (mobile) ---------- */
  const sidebar = document.getElementById('crmSidebar');
  const overlay = document.getElementById('sidebarOverlay');
  document.addEventListener('click', (e) => {
    if (e.target.closest('#burger')) {
      sidebar?.classList.toggle('open');
      overlay?.classList.toggle('open');
    }
    if (e.target === overlay) {
      sidebar?.classList.remove('open');
      overlay?.classList.remove('open');
    }
  });

  /* ---------- global search ---------- */
  const searchInput = document.getElementById('globalSearch');
  const searchResults = document.getElementById('searchResults');
  if (searchInput && searchResults) {
    let timer = null;
    searchInput.addEventListener('input', () => {
      clearTimeout(timer);
      const q = searchInput.value.trim();
      if (q.length < 2) { searchResults.classList.remove('open'); return; }
      timer = setTimeout(async () => {
        try {
          const data = await api('/crm/ajax/search?q=' + encodeURIComponent(q));
          searchResults.innerHTML = data.results.length
            ? data.results.map((r) =>
                '<a class="sr-item" href="' + r.url + '"><b>' + r.name + '</b><small>' + r.type + ' — ' + r.sub + '</small></a>'
              ).join('')
            : '<div class="bell-empty">No results</div>';
          searchResults.classList.add('open');
        } catch { /* ignore */ }
      }, 250);
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.topbar-search')) searchResults.classList.remove('open');
    });
  }

  /* ---------- notifications bell ---------- */
  const bellPanel = document.getElementById('bellPanel');
  if (bellPanel) {
    document.addEventListener('click', (e) => {
      if (e.target.closest('#bellBtn')) {
        if (bellPanel.classList.contains('open')) return;
        fetch('/crm/ajax/notifications')
          .then((r) => r.json())
          .then((data) => {
            const list = document.getElementById('bellList');
            list.innerHTML = data.notifications.length
              ? data.notifications.map((n) =>
                  '<a class="bell-item ' + (n.read ? '' : 'unread') + '" href="' + n.url + '">' +
                  n.message + '<small>' + n.time + '</small></a>'
                ).join('')
              : '<div class="bell-empty">No notifications</div>';
            fetch('/crm/ajax/notifications/mark-read', { method: 'POST', headers: { 'X-CSRFToken': getCookie('csrftoken') } });
            const dot = document.getElementById('bellCount');
            if (dot) dot.remove();
          });
      }
    });
  }

  function initDatepickers(root) {
    if (!window.flatpickr) return;
    root.querySelectorAll('[data-datepicker]').forEach((el) => {
      if (el._flatpickr) return;
      flatpickr(el, { enableTime: true, dateFormat: 'Y-m-d H:i' });
    });
  }

  /* ---------- lead drawer actions (shared by leads + pipeline) ---------- */
  async function quickUpdate(leadId, field, value) {
    return api('/crm/ajax/leads/' + leadId + '/update', 'POST', { field, value });
  }
  function refreshLeadRow(leadId, data) {
    const row = document.querySelector('tr[data-lead="' + leadId + '"]');
    if (!row) return;
    const stageCell = row.querySelector('[data-stage-cell]');
    if (stageCell && data.stage_name) {
      stageCell.innerHTML = '<span class="pill pill-blue"></span>';
      stageCell.querySelector('.pill').textContent = data.stage_name;
      const bucketCell = row.querySelector('[data-bucket-cell]');
      if (bucketCell) {
        const pill = bucketCell.querySelector('.pill');
        if (data.won) { pill.className = 'pill pill-won'; pill.textContent = 'Won'; }
        else if (data.lost) { pill.className = 'pill pill-lost'; pill.textContent = 'Lost'; }
      }
    }
    const assignedCell = row.querySelector('[data-assigned-cell]');
    if (assignedCell && 'assignee' in data) {
      assignedCell.textContent = data.assignee || 'Unassigned';
    }
  }
  document.addEventListener('submit', async (e) => {
    const form = e.target.closest('.stage-form');
    if (!form) return;
    e.preventDefault();
    const leadId = form.dataset.leadId;
    const btn = form.querySelector('button[type=submit]');
    if (btn) btn.disabled = true;
    try {
      const res = await api('/crm/ajax/leads/' + leadId + '/update', 'POST', new FormData(form));
      Crm.toast(res.stage_name ? 'Lead updated: ' + res.stage_name : 'Lead updated', 'success');
      refreshLeadRow(leadId, res);
      window.Crm._openLeadPopup && window.Crm._openLeadPopup(leadId);
    } catch (err) { Crm.toast(err.message, 'error'); }
    if (btn) btn.disabled = false;
  });
  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('#assignMeBtn');
    if (!btn) return;
    btn.disabled = true;
    try {
      await quickUpdate(btn.dataset.leadId, 'assigned_to', 'me');
      Crm.toast('Lead assigned to you', 'success');
      window.Crm._openLeadPopup && window.Crm._openLeadPopup(btn.dataset.leadId);
    } catch (err) { Crm.toast(err.message, 'error'); btn.disabled = false; }
  });

  /* ---------- call log form submission (in lead drawer) ---------- */
  document.addEventListener('submit', async (e) => {
    const form = e.target.closest('.call-log-form');
    if (!form) return;
    e.preventDefault();
    const leadId = form.dataset.leadId;
    if (!leadId) { console.error('Call log form missing leadId', form); toast('Form error: missing lead ID', 'error'); return; }
    const btn = form.querySelector('button[type=submit]');
    if (btn) btn.disabled = true;
    try {
      const fd = new FormData(form);
      fd.append('lead', leadId);
      const res = await api('/crm/ajax/calls/log', 'POST', fd);
      toast('Call logged', 'success');
      form.reset();
      window.Crm._openLeadPopup && window.Crm._openLeadPopup(leadId);
    } catch (err) { toast(err.message, 'error'); }
    if (btn) btn.disabled = false;
  });

  /* ---------- convert lead -> customer ---------- */
  function convertModal(leadId) {
    openModal('Convert to Customer', `
      <form id="convertForm" class="form-grid">
        <div class="form-group full"><label>Package / Plan</label><input name="package" placeholder="e.g. Pro" value="free"></div>
        <div class="form-group"><label>Monthly Value (৳)</label><input name="monthly_value"></div>
        <div class="form-group"><label>Renewal Date</label><input name="renewal" data-datepicker></div>
        <div class="form-group full"><button class="btn btn-primary" type="submit">Convert</button></div>
      </form>`);
    initDatepickers(document);
    document.getElementById('convertForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      try {
        const res = await api('/crm/ajax/leads/' + leadId + '/convert', 'POST',
          Object.fromEntries(new FormData(e.target)));
        toast('Customer created!', 'success');
        setTimeout(() => location.href = res.url, 400);
      } catch (err) { toast(err.message, 'error'); }
    });
  }

  /* ---------- import leads from image (owner) ---------- */
  function fileToDataUrl(file, maxDim) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error('Could not read file'));
      reader.onload = () => {
        const img = new Image();
        img.onerror = () => reject(new Error('Not a valid image'));
        img.onload = () => {
          const dim = maxDim || 1280;
          const scale = Math.min(1, dim / Math.max(img.width, img.height));
          const canvas = document.createElement('canvas');
          canvas.width = Math.round(img.width * scale);
          canvas.height = Math.round(img.height * scale);
          canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
          resolve(canvas.toDataURL('image/jpeg', 0.85));
        };
        img.src = reader.result;
      };
      reader.readAsDataURL(file);
    });
  }

  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function imageImport() {
    openModal('Import Leads from Image', `
      <div class="import-drop" id="importDrop">
        <div class="import-drop-icon">🖼️</div>
        <div class="import-drop-text">Click to choose an image,<br>or paste it here (Ctrl+V)</div>
        <div class="form-help">Business card / lead detail sheet — AI reads the details for you.</div>
        <input type="file" id="importFile" accept="image/*" hidden>
      </div>
      <img id="importPreview" class="import-preview" alt="Preview" hidden>
      <button id="importAnalyzeBtn" class="btn btn-primary btn-block" disabled hidden>⚡ Analyze Image</button>
      <div id="importStatus" class="import-status" hidden></div>
      <div id="importReviews"></div>
      <button id="importCreateBtn" class="btn btn-primary btn-block" hidden>✅ Create Leads</button>`);

    const drop = document.getElementById('importDrop');
    const fileInput = document.getElementById('importFile');
    const preview = document.getElementById('importPreview');
    const analyzeBtn = document.getElementById('importAnalyzeBtn');
    const statusEl = document.getElementById('importStatus');
    const reviewsEl = document.getElementById('importReviews');
    const createBtn = document.getElementById('importCreateBtn');
    let pendingDataUrl = null;

    const setStatus = (msg, kind) => {
      statusEl.hidden = !msg;
      statusEl.textContent = msg || '';
      statusEl.className = 'import-status' + (kind ? ' import-status-' + kind : '');
    };

    const handleFile = async (file) => {
      if (!file || !file.type || !file.type.startsWith('image/')) {
        Crm.toast('Please choose an image file', 'error');
        return;
      }
      setStatus('Preparing image…');
      try {
        pendingDataUrl = await fileToDataUrl(file);
        preview.src = pendingDataUrl;
        preview.hidden = false;
        analyzeBtn.hidden = false;
        analyzeBtn.disabled = false;
        setStatus('');
      } catch (err) {
        setStatus('');
        Crm.toast(err.message, 'error');
      }
    };

    drop.addEventListener('click', () => fileInput.click());
    drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('drag'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('drag'));
    drop.addEventListener('drop', (e) => {
      e.preventDefault();
      drop.classList.remove('drag');
      handleFile(e.dataTransfer.files && e.dataTransfer.files[0]);
    });
    fileInput.addEventListener('change', () => handleFile(fileInput.files[0]));

    analyzeBtn.addEventListener('click', async () => {
      if (!pendingDataUrl) return;
      analyzeBtn.disabled = true;
      setStatus('Analyzing image with AI…');
      try {
        const res = await api('/crm/ajax/leads/analyze-image', 'POST', { image: pendingDataUrl });
        reviewsEl.innerHTML = res.leads.map((l, i) => {
          const tags = (l.tags || []).concat(l.tier ? [l.tier] : []).join(', ');
          return `
          <div class="import-row" data-i="${i}">
            <div class="import-row-head">
              <label class="import-check"><input type="checkbox" class="import-include" checked> Create</label>
              <button type="button" class="import-remove" title="Remove">✕</button>
            </div>
            <div class="form-grid">
              <div class="form-group full"><label>Name *</label><input class="f-name" value="${escHtml(l.name)}"></div>
              <div class="form-group"><label>Phone</label><input class="f-phone" value="${escHtml(l.phone)}"></div>
              <div class="form-group"><label>Email</label><input class="f-email" type="email" value="${escHtml(l.email)}"></div>
              <div class="form-group full"><label>Address</label><input class="f-address" value="${escHtml(l.address)}"></div>
              <div class="form-group"><label>Website</label><input class="f-website" value="${escHtml(l.website)}"></div>
              <div class="form-group"><label>Industry</label><input class="f-industry" value="${escHtml(l.industry)}"></div>
              <div class="form-group full"><label>Tags (comma separated)</label><input class="f-tags" placeholder="e.g. tier-1, hot, wholesale" value="${escHtml(tags)}"></div>
              <div class="form-group full"><label>Notes</label><textarea class="f-notes" rows="2" placeholder="Other visible details (VAT, hours, owner…)">${escHtml(l.notes)}</textarea></div>
            </div>
          </div>`;
        }).join('');
        createBtn.hidden = false;
        createBtn.disabled = res.leads.length === 0;
        setStatus('Review the extracted details, correct anything, then create the leads.', 'note');
      } catch (err) {
        Crm.toast(err.message, 'error');
      }
      analyzeBtn.disabled = false;
    });

    document.addEventListener('click', (e) => {
      const rm = e.target.closest('.import-remove');
      if (!rm || !reviewsEl.contains(rm)) return;
      rm.closest('.import-row').remove();
      if (!reviewsEl.querySelector('.import-row')) {
        createBtn.hidden = true;
        setStatus('');
      }
    });

    document.addEventListener('paste', (e) => {
      if (modalEl.hidden || !drop.isConnected) return;
      const items = (e.clipboardData || {}).items || [];
      for (const item of items) {
        if (item.kind === 'file' && item.type.startsWith('image/')) {
          e.preventDefault();
          handleFile(item.getAsFile());
          return;
        }
      }
    });

    createBtn.addEventListener('click', async () => {
      const leads = [];
      reviewsEl.querySelectorAll('.import-row').forEach((row) => {
        if (!row.querySelector('.import-include').checked) return;
        const name = row.querySelector('.f-name').value.trim();
        if (!name) return;
        leads.push({
          name,
          phone: row.querySelector('.f-phone').value.trim(),
          email: row.querySelector('.f-email').value.trim(),
          address: row.querySelector('.f-address').value.trim(),
          website: row.querySelector('.f-website').value.trim(),
          industry: row.querySelector('.f-industry').value.trim(),
          tags: row.querySelector('.f-tags').value.trim(),
          notes: row.querySelector('.f-notes').value.trim(),
        });
      });
      if (!leads.length) {
        Crm.toast('No valid leads to create — check names', 'error');
        return;
      }
      createBtn.disabled = true;
      setStatus('Creating leads…');
      try {
        const res = await api('/crm/ajax/leads/create-from-import', 'POST', { leads: JSON.stringify(leads) });
        let msg = res.created + ' lead' + (res.created === 1 ? '' : 's') + ' created';
        if (res.duplicates) msg += ', ' + res.duplicates + ' duplicate' + (res.duplicates === 1 ? '' : 's') + ' skipped';
        Crm.toast(msg, 'success');
        closeModal();
        setTimeout(() => location.reload(), 600);
      } catch (err) {
        Crm.toast(err.message, 'error');
        createBtn.disabled = false;
      }
    });
  }

  /* ---------- add to home screen (PWA) ---------- */
  let deferredPrompt = null;
  const installBtn = document.getElementById('installAppBtn');

  function showInstallHelp() {
    const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
    openModal('Add to Home Screen', `
      <div class="install-help">
        <div class="import-drop-icon">📲</div>
        <p>Install Matrix CRM on your device so it opens like a normal app.</p>
        ${isIOS ? `
          <div class="install-step"><b>1.</b> Tap the <b>Share</b> button <span class="pill pill-gray">⎋</span> in Safari's toolbar.</div>
          <div class="install-step"><b>2.</b> Scroll down and tap <b>"Add to Home Screen"</b>.</div>
          <div class="install-step"><b>3.</b> Tap <b>Add</b> in the top-right corner.</div>`
        : `
          <div class="install-step"><b>1.</b> Open the browser menu <span class="pill pill-gray">⋮</span> (top-right).</div>
          <div class="install-step"><b>2.</b> Tap <b>"Add to Home screen"</b> or <b>"Install app"</b>.</div>
          <div class="install-step"><b>3.</b> Confirm in the popup that appears.</div>`}
      </div>`);
  }

  if (installBtn) {
    window.addEventListener('beforeinstallprompt', (e) => {
      e.preventDefault();
      deferredPrompt = e;
      installBtn.hidden = false;
    });
    window.addEventListener('appinstalled', () => {
      deferredPrompt = null;
      installBtn.hidden = true;
    });
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches
      || window.navigator.standalone === true;
    if (!isStandalone && !('beforeinstallprompt' in window) && /iphone|ipad|ipod/i.test(navigator.userAgent)) {
      installBtn.hidden = false;
    }
    installBtn.addEventListener('click', async () => {
      if (deferredPrompt) {
        deferredPrompt.prompt();
        const choice = await deferredPrompt.userChoice.catch(() => ({}));
        if (choice.outcome !== 'accepted') deferredPrompt = null;
        return;
      }
      showInstallHelp();
    });
  }

  /* ---------- auto-dismiss toasts ---------- */
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.crm-toast').forEach((t) => {
      setTimeout(() => t.remove(), 4200);
    });
    initDatepickers(document);
  });

  window.Crm = { api, toast, openModal, closeModal, openDrawer, closeDrawer, getCookie, convertModal, copyText, imageImport };
})();
