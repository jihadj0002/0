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
      opts.headers['Content-Type'] = 'application/x-www-form-urlencoded';
      opts.body = new URLSearchParams(Object.entries(data).filter(([, v]) => v !== null && v !== undefined));
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

  /* ---------- modals & drawers ---------- */
  const modalEl = document.getElementById('crmModal');
  const modalBackdrop = document.getElementById('crmModalBackdrop');
  const drawerEl = document.getElementById('crmDrawer');
  const drawerBackdrop = document.getElementById('crmDrawerBackdrop');
  let restoreFocusEl = null;

  function lockScroll() { document.body.style.overflow = 'hidden'; }
  function unlockScroll() {
    if (modalEl.hidden && drawerEl.hidden) document.body.style.overflow = '';
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

  /* ---------- auto-dismiss toasts ---------- */
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.crm-toast').forEach((t) => {
      setTimeout(() => t.remove(), 4200);
    });
    initDatepickers(document);
  });

  window.Crm = { api, toast, openModal, closeModal, openDrawer, closeDrawer, getCookie };
})();
