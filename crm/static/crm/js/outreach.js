/* Matrix CRM — Email Outreach */
(function () {
  'use strict';

  const csrf = () => {
    const el = document.querySelector('[name=csrfmiddlewaretoken]');
    return el ? el.value : '';
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

  /* ---------- helpers ---------- */
  function qs(sel, ctx) { return (ctx || document).querySelector(sel); }
  function qsa(sel, ctx) { return (ctx || document).querySelectorAll(sel); }
  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  async function api(url, method, data) {
    const opts = { method: method || 'GET', headers: {} };
    if (data != null) {
      opts.headers['X-Requested-With'] = 'XMLHttpRequest';
      if (data instanceof FormData) {
        opts.body = data;
      } else {
        opts.headers['Content-Type'] = 'application/x-www-form-urlencoded';
        opts.body = new URLSearchParams(
          Object.entries(data).filter(([, v]) => v !== null && v !== undefined)
        ).toString();
      }
    }
    const token = csrf();
    if (token) opts.headers['X-CSRFToken'] = token;
    const res = await fetch(url, opts);
    const ct = res.headers.get('content-type') || '';
    const body = ct.includes('json') ? await res.json() : await res.text();
    if (!res.ok) throw new Error((body && (body.error || body.detail)) || ('HTTP ' + res.status));
    return body;
  }

  /* ===================================================================
     1 — Lead selection (audience step)
     =================================================================== */
  const Outreach = {
    initLeadSelection: function () {
      const container = document.getElementById('leadSelectContainer');
      if (!container) return;
      const checkboxes = qsa('.lead-check', container);
      const selectAll = document.getElementById('leadSelectAll');

      function updateSummary() {
        const checked = qsa('.lead-check:checked', container);
        const count = checked.length;
        const validEmails = Array.from(checked).filter(function (cb) {
          var meta = cb.closest('.lead-select-item').querySelector('.meta');
          return meta && meta.textContent.indexOf('@') > -1;
        }).length;
        var selectedEl = document.getElementById('audienceSelectedCount');
        var validEl = document.getElementById('audienceValidCount');
        var excludedEl = document.getElementById('audienceExcludedCount');
        var totalEl = document.getElementById('audienceTotalCount');
        if (selectedEl) selectedEl.textContent = count;
        if (validEl) validEl.textContent = validEmails;
        if (excludedEl) excludedEl.textContent = count - validEmails;
        if (totalEl) totalEl.textContent = validEmails;
        if (selectAll)
          selectAll.checked = checkboxes.length > 0 && count === checkboxes.length;
      }

      checkboxes.forEach(function (cb) {
        cb.addEventListener('change', function () {
          updateSummary();
          Outreach.saveSelectedLeads();
        });
      });

      if (selectAll) {
        selectAll.addEventListener('change', function () {
          checkboxes.forEach(function (cb) { cb.checked = selectAll.checked; });
          updateSummary();
          Outreach.saveSelectedLeads();
        });
      }

      // Restore from draft if leads are stored
      var stored = container.getAttribute('data-selected-leads');
      if (stored) {
        try {
          var ids = JSON.parse(stored);
          checkboxes.forEach(function (cb) {
            if (ids.indexOf(cb.value) > -1) cb.checked = true;
          });
          updateSummary();
        } catch (e) {}
      }

      updateSummary();
    },

    saveSelectedLeads: function () {
      const container = document.getElementById('leadSelectContainer');
      if (!container) return;
      const ids = Array.from(qsa('.lead-check:checked', container))
        .map(function (cb) { return cb.value; });
      api('/crm/outreach/campaigns/create/', 'POST', {
        action: 'save_audience',
        lead_ids: JSON.stringify(ids)
      }).then(function () {
        // silently saved
      }).catch(function (err) {
        toast(err.message, 'error');
      });
}
  };

  /* ===================================================================
     2 — Template preview
     =================================================================== */
  Outreach.previewTemplate = function (uid) {
    const previewEl = document.getElementById('templatePreview');
    if (!previewEl) return;
    previewEl.innerHTML = '<div class="text-muted">Loading preview…</div>';
    api('/crm/outreach/templates/' + uid + '/preview/')
      .then(function (data) {
        previewEl.innerHTML =
          '<div class="template-preview-subject"><strong>Subject:</strong> ' +
          escHtml(data.subject) + '</div>' +
          '<div class="template-preview-body">' + data.body_html + '</div>';
      })
      .catch(function (err) {
        previewEl.innerHTML = '<div class="text-danger">Preview failed: ' + escHtml(err.message) + '</div>';
        toast(err.message, 'error');
      });
  };

  Outreach.initTemplateSelector = function () {
    const sel = document.getElementById('templateSelect');
    if (!sel) return;
    sel.addEventListener('change', function () {
      var uid = sel.value;
      var previewEl = document.getElementById('templatePreview');
      if (!uid) {
        if (previewEl) previewEl.innerHTML = '';
        return;
      }
      Outreach.previewTemplate(uid);
    });
  };

  /* ===================================================================
     3 — Campaign wizard navigation
     =================================================================== */
  Outreach.goToStep = function (stepIndex) {
    var steps = qsa('.wizard-step');
    var panes = qsa('.wizard-pane');
    var indicator = document.getElementById('wizardStepIndicator');
    if (!steps.length || !panes.length) return;

    steps.forEach(function (s, i) {
      s.classList.toggle('completed', i < stepIndex);
      s.classList.toggle('current', i === stepIndex);
    });

    panes.forEach(function (p, i) {
      p.classList.toggle('active', i === stepIndex);
    });

    if (indicator) indicator.value = stepIndex;

    if (stepIndex === 5) Outreach.populateReview();
  };

  Outreach.populateReview = function () {
    var form = document.getElementById('campaignWizardForm');
    if (!form) return;
    var fd = new FormData(form);

    var nameEl = document.getElementById('reviewName');
    var audienceEl = document.getElementById('reviewAudience');
    var templateEl = document.getElementById('reviewTemplate');
    var accountEl = document.getElementById('reviewAccount');
    var scheduleEl = document.getElementById('reviewSchedule');
    var limitEl = document.getElementById('reviewDailyLimit');

    if (nameEl) nameEl.textContent = fd.get('campaign_name') || '—';

    var checkedLeads = qsa('.lead-check:checked');
    var validCount = Array.from(checkedLeads).filter(function (cb) {
      var meta = cb.closest('.lead-select-item').querySelector('.meta');
      return meta && meta.textContent.indexOf('@') > -1;
    }).length;
    if (audienceEl) audienceEl.textContent = validCount + ' recipients (' + checkedLeads.length + ' selected)';

    var templateSel = document.getElementById('templateSelect');
    if (templateSel && templateSel.value) {
      var card = qs('.template-card.selected');
      var tname = card ? card.querySelector('.template-name').textContent : 'Template selected';
      if (templateEl) templateEl.textContent = tname;
    } else {
      if (templateEl) templateEl.textContent = '—';
    }

    var acctSel = document.getElementById('id_sending_account');
    if (acctSel && acctSel.value) {
      var text = acctSel.options[acctSel.selectedIndex].text;
      if (accountEl) accountEl.textContent = text;
    } else {
      if (accountEl) accountEl.textContent = '—';
    }

    var schedType = fd.get('schedule_type');
    if (schedType === 'schedule') {
      var date = fd.get('schedule_date') || '';
      var time = fd.get('schedule_time') || '';
      if (scheduleEl) scheduleEl.textContent = (date ? date : '') + (time ? ' ' + time : 'Scheduled');
    } else {
      if (scheduleEl) scheduleEl.textContent = 'Immediate';
    }

    if (limitEl) limitEl.textContent = fd.get('daily_limit') || '50';

    // Pre-flight checks
    var hasRecipients = validCount > 0;
    var hasTemplate = templateSel && !!templateSel.value;
    var hasAccount = acctSel && !!acctSel.value;

    function toggleCheck(id, ok, msg) {
      var el = document.getElementById(id);
      if (!el) return;
      el.className = ok ? 'valid' : 'invalid';
      el.querySelector('.check-icon').className = 'check-icon ' + (ok ? 'valid' : 'invalid');
      el.querySelector('.check-icon').textContent = ok ? '✓' : '✗';
    }
    toggleCheck('checkRecipients', hasRecipients, 'Valid recipients selected');
    toggleCheck('checkTemplate', hasTemplate, 'Template selected');
    toggleCheck('checkAccount', hasAccount, 'Account connected');
    toggleCheck('checkSchedule', true, 'Schedule configured');
  };

  Outreach.saveStep = function (stepIndex) {
    var form = document.getElementById('campaignWizardForm');
    if (!form) return Promise.resolve();
    var fd = new FormData(form);
    fd.append('action', 'save_step');
    fd.append('step', stepIndex);
    var btn = qs('[data-wizard-action="next"]');
    if (btn) btn.disabled = true;
    return api('/crm/outreach/campaigns/create/', 'POST', fd)
      .then(function () {
        toast('Step saved', 'success');
        return true;
      })
      .catch(function (err) {
        toast(err.message, 'error');
        return false;
      })
      .finally(function () {
        if (btn) btn.disabled = false;
      });
  };

  Outreach.validateStep = function (stepIndex) {
    var pane = qsa('.wizard-pane')[stepIndex];
    if (!pane) return true;
    var required = qsa('[required]', pane);
    var valid = true;
    required.forEach(function (el) {
      if (!el.value.trim()) {
        el.classList.add('is-invalid');
        valid = false;
      } else {
        el.classList.remove('is-invalid');
      }
    });
    if (!valid) toast('Please fill in all required fields', 'warning');
    return valid;
  };

  Outreach.nextStep = function () {
    var indicator = document.getElementById('wizardStepIndicator');
    var current = indicator ? parseInt(indicator.value, 10) : 0;
    var total = qsa('.wizard-pane').length;
    if (current >= total - 1) return;
    if (!Outreach.validateStep(current)) return;
    Outreach.saveStep(current).then(function (ok) {
      if (ok) Outreach.goToStep(current + 1);
    });
  };

  Outreach.backStep = function () {
    var indicator = document.getElementById('wizardStepIndicator');
    var current = indicator ? parseInt(indicator.value, 10) : 0;
    if (current <= 0) return;
    Outreach.goToStep(current - 1);
  };

  Outreach.submitCampaign = function () {
    var form = document.getElementById('campaignWizardForm');
    if (!form) return;
    var btn = qs('[data-wizard-action="submit"]');
    if (btn) btn.disabled = true;
    var fd = new FormData(form);
    fd.append('action', 'submit');
    api('/crm/outreach/campaigns/create/', 'POST', fd)
      .then(function (data) {
        toast(data.message || 'Campaign sent!', 'success');
        if (data.redirect) { window.location.href = data.redirect; }
        else { window.location.reload(); }
      })
      .catch(function (err) {
        toast(err.message, 'error');
        if (btn) btn.disabled = false;
      });
  };

  Outreach.initWizard = function () {
    Outreach.goToStep(0);
    Outreach.initTemplateSelector();
  };

  /* ===================================================================
     4 — Campaign list filters
     =================================================================== */
  Outreach.initCampaignFilters = function () {
    var statusFilter = document.getElementById('campaignStatusFilter');
    var searchInput = document.getElementById('campaignSearch');
    var tableBody = document.getElementById('campaignTableBody');
    var timer = null;

    function reload() {
      if (!tableBody) return;
      var status = statusFilter ? statusFilter.value : '';
      var q = searchInput ? searchInput.value.trim() : '';
      tableBody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">Loading…</td></tr>';
      api('/crm/outreach/campaigns/?_ajax=1&status=' + encodeURIComponent(status) +
        '&q=' + encodeURIComponent(q))
        .then(function (html) {
          tableBody.innerHTML = html;
        })
        .catch(function (err) {
          tableBody.innerHTML = '<tr><td colspan="6" class="text-center text-danger">' +
            escHtml(err.message) + '</td></tr>';
        });
    }

    if (statusFilter) {
      statusFilter.addEventListener('change', reload);
    }

    if (searchInput) {
      searchInput.addEventListener('input', function () {
        clearTimeout(timer);
        timer = setTimeout(reload, 350);
      });
    }
  };

  /* ===================================================================
     5 — Batch polling (send results)
     =================================================================== */
  Outreach.pollBatch = function (batchUid) {
    var progressBar = document.getElementById('batchProgressBar');
    var sentCount = document.getElementById('batchSentCount');
    var totalCount = document.getElementById('batchTotalCount');
    var failedCount = document.getElementById('batchFailedCount');
    var statusText = document.getElementById('batchStatusText');

    function updateUI(data) {
      var total = data.total || 0;
      var sent = data.sent || 0;
      var failed = data.failed || 0;
      var processed = sent + failed;
      var pct = total > 0 ? Math.round((processed / total) * 100) : 0;

      if (progressBar) {
        progressBar.style.width = pct + '%';
        progressBar.setAttribute('aria-valuenow', pct);
        progressBar.textContent = pct + '%';
      }
      if (sentCount) sentCount.textContent = sent;
      if (totalCount) totalCount.textContent = total;
      if (failedCount) failedCount.textContent = failed;
      if (statusText) statusText.textContent = 'Sent: ' + sent + ', Failed: ' + failed + ' / ' + total;
    }

    var poll = setInterval(function () {
      api('/crm/outreach/batch/' + batchUid + '/poll/')
        .then(function (data) {
          updateUI(data);
          if (data.status === 'completed') {
            clearInterval(poll);
            Outreach.showBatchResults(data);
          } else if (data.status === 'failed') {
            clearInterval(poll);
            toast('Batch failed: ' + (data.detail || 'Unknown error'), 'error');
          }
        })
        .catch(function () {});
    }, 3000);
  };

  Outreach.showBatchResults = function (data) {
    var modal = document.getElementById('batchResultsModal');
    if (!modal) return;
    qs('[data-result-sent]', modal).textContent = data.sent || 0;
    qs('[data-result-failed]', modal).textContent = data.failed || 0;
    qs('[data-result-total]', modal).textContent = data.total || 0;
    qs('[data-result-detail]', modal).textContent = [
      'Batch: ' + (data.batch_id || ''),
      'Total: ' + (data.total || 0),
      'Sent: ' + (data.sent || 0),
      'Failed: ' + (data.failed || 0),
      'Remaining: ' + (data.remaining || 0)
    ].join('\n');
    modal.hidden = false;
  };

  Outreach.sendCampaign = function (campaignUid) {
    if (!confirm('Send this campaign now?')) return;
    var btn = document.getElementById('sendCampaignBtn');
    if (btn) btn.disabled = true;
    api('/crm/outreach/campaigns/' + campaignUid + '/send/', 'POST')
      .then(function (data) {
        toast('Campaign queued for sending', 'success');
        window.location.reload();
      })
      .catch(function (err) {
        toast(err.message, 'error');
        if (btn) btn.disabled = false;
      });
  };

  Outreach.pauseCampaign = function (campaignUid) {
    if (!confirm('Pause this campaign?')) return;
    api('/crm/outreach/campaigns/' + campaignUid + '/pause/', 'POST')
      .then(function (data) {
        toast('Campaign paused', 'success');
        window.location.reload();
      })
      .catch(function (err) {
        toast(err.message, 'error');
      });
  };

  Outreach.archiveCampaign = function (campaignUid) {
    if (!confirm('Archive this campaign?')) return;
    api('/crm/outreach/campaigns/' + campaignUid + '/archive/', 'POST')
      .then(function (data) {
        toast('Campaign archived', 'success');
        window.location.reload();
      })
      .catch(function (err) {
        toast(err.message, 'error');
      });
  };

  /* ===================================================================
     6 — Close results modal
     =================================================================== */
  Outreach.closeBatchResults = function () {
    var modal = document.getElementById('batchResultsModal');
    if (modal) modal.hidden = true;
  };

  /* ===================================================================
     7 — Campaign create wizard helpers
     =================================================================== */
  Outreach.selectTemplate = function (uid) {
    var input = document.getElementById('templateSelect');
    if (input) input.value = uid;
    qsa('.template-card').forEach(function (c) { c.classList.remove('selected'); });
    var card = qs('.template-card[data-uid="' + uid + '"]');
    if (card) card.classList.add('selected');
    Outreach.previewTemplate(uid);
    toast('Template selected', 'success');
  };

  Outreach.toggleAiOptions = function () {
    var aiOpts = document.getElementById('aiPersonalizationOptions');
    if (!aiOpts) return;
    var toggle = document.getElementById('aiPersonalizationToggle');
    aiOpts.style.display = toggle && toggle.checked ? 'block' : 'none';
  };

  Outreach.applyFilters = function () {
    var form = document.getElementById('campaignWizardForm');
    if (!form) return;
    var fd = new FormData(form);
    fd.append('action', 'filter_leads');
    var btn = qs('[data-filter-btn]');
    if (btn) btn.disabled = true;
    var container = document.getElementById('leadSelectContainer');
    if (container) container.innerHTML = '<div class="text-muted" style="padding:16px;text-align:center">Loading…</div>';
    api('/crm/outreach/campaigns/create/', 'POST', fd)
      .then(function (data) {
        if (!container) return;
        if (!data.leads || !data.leads.length) {
          container.innerHTML = '<div class="empty-state"><div class="es-title">No leads found</div><div class="es-sub">Try different filters.</div></div>';
          return;
        }
        var html = '';
        if (data.total > 0) {
          html += '<label class="checkbox-option" style="margin-bottom:8px"><input type="checkbox" id="leadSelectAll"> Select All (' + data.total + ')</label>';
        }
        data.leads.forEach(function (l) {
          html += '<div class="lead-select-item">' +
            '<input type="checkbox" class="lead-check" name="selected_leads" value="' + l.id + '">' +
            '<div class="lead-info">' +
            '<div class="name">' + escHtml(l.name) + '</div>' +
            '<div class="meta">' + escHtml(l.email) + (l.company ? ' &middot; ' + escHtml(l.company) : '') + '</div>' +
            '</div></div>';
        });
        container.innerHTML = html;
        Outreach.initLeadSelection();
        toast(data.total + ' leads found', 'success');
      })
      .catch(function (err) {
        toast(err.message, 'error');
        if (container) container.innerHTML = '<div class="text-danger">' + escHtml(err.message) + '</div>';
      })
      .finally(function () {
        if (btn) btn.disabled = false;
      });
  };

  Outreach.saveDraft = function () {
    var form = document.getElementById('campaignWizardForm');
    if (!form) return;
    var fd = new FormData(form);
    fd.append('action', 'save_draft');
    var btn = document.querySelector('[data-wizard-action="draft"]');
    if (btn) btn.disabled = true;
    api('/crm/outreach/campaigns/create/', 'POST', fd)
      .then(function (data) {
        toast('Draft saved', 'success');
        if (data.redirect) window.location.href = data.redirect;
      })
      .catch(function (err) {
        toast(err.message, 'error');
        if (btn) btn.disabled = false;
      });
  };

  Outreach.initManualSearch = function () {
    var input = document.getElementById('id_manual_search');
    if (!input) return;
    var timer = null;
    input.addEventListener('keyup', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var searchInput = document.getElementById('id_search');
        if (searchInput) searchInput.value = input.value;
        Outreach.applyFilters();
      }, 400);
    });
  };

  Outreach.initAudienceRadios = function () {
    var radios = qsa('input[name="audience_source"]');
    radios.forEach(function (r) {
      r.addEventListener('change', function () {
        var filterSection = document.getElementById('audienceFilterSection');
        var manualSection = document.getElementById('audienceManualSection');
        if (!filterSection || !manualSection) return;
        filterSection.style.display = this.value === 'filter' || this.value === 'segment' ? 'block' : 'none';
        manualSection.style.display = this.value === 'manual' ? 'block' : 'none';
      });
    });
    var checked = qs('input[name="audience_source"]:checked');
    if (checked) {
      var evt = new Event('change');
      checked.dispatchEvent(evt);
    }
  };

  Outreach.initTemplateCategoryFilters = function () {
    var btns = qsa('[data-category]');
    btns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var cat = this.getAttribute('data-category');
        btns.forEach(function (b) {
          b.className = b.className.replace('btn-primary', '') + ' btn-ghost';
        });
        this.className = this.className.replace('btn-ghost', '') + ' btn-primary';
        qsa('.template-card').forEach(function (card) {
          card.style.display = (cat === 'all' || card.getAttribute('data-category') === cat) ? '' : 'none';
        });
      });
    });
  };

  /* ===================================================================
     Init on DOMContentLoaded
     =================================================================== */
  document.addEventListener('DOMContentLoaded', function () {
    if (document.getElementById('leadSelectContainer')) {
      Outreach.initLeadSelection();
      Outreach.initManualSearch();
    }
    if (document.getElementById('campaignWizardForm')) {
      Outreach.initWizard();
      Outreach.initAudienceRadios();
      Outreach.initTemplateCategoryFilters();
    }
    if (document.getElementById('campaignStatusFilter') || document.getElementById('campaignSearch')) {
      Outreach.initCampaignFilters();
    }
  });

  /* Expose on window.Outreach for inline onclick */
  window.Outreach = Outreach;
})();