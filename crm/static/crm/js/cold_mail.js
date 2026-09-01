/* Matrix CRM — Cold Mail functionality */
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

  /* ---------- lead selection ---------- */
  $(document).on('change', '.lead-check', function() {
    if ($('.lead-check:checked').length === $('.lead-check').length) {
      $('#selectAllCb').prop('checked', true);
    } else {
      $('#selectAllCb').prop('checked', false);
    }
  });

  $('#selectAllCb').on('change', function() {
    $('.lead-check').prop('checked', $(this).prop('checked'));
  });

  window.Crm = window.Crm || {};
  window.Crm.sendSelectedEmails = function() {
    const leadIds = $('.lead-check:checked').map(function() { return $(this).val(); }).get();
    if (!leadIds.length) {
      toast('Please select at least one lead', 'warning');
      return;
    }
    if (!confirm('Send emails to ' + leadIds.length + ' selected leads?')) return;
    
    $.ajax({
      url: '{% url "crm:cold_mail_compose" %}',
      method: 'POST',
      data: { lead_ids: leadIds },
      dataType: 'json',
      beforeSend: function() {
        $('#sendEmailsBtn').button('loading');
      },
      success: function(data) {
        $('#sendEmailsBtn').button('reset');
        if (data.status === 'started') {
          showResultsPoll(data.batch_id);
          Swal.fire({
            title: 'Emails sending',
            text: ' ' + data.sent + ' emails queued. Redirecting to results...',
            icon: 'info',
            allowOutsideClick: false,
            onBeforeOpen: () => {
              Swal.showLoading();
            }
          });
        } else if (data.status === 'results') {
          showResultsModal(data);
        }
      },
      error: function(xhr) {
        $('#sendEmailsBtn').button('reset');
        toast(xhr.responseText || 'Failed to start send', 'error');
      }
    });
  };

  /* ---------- poll for results ---------- */
  window.Crm.showResultsPoll = function(batchId) {
    const poll = setInterval(async () => {
      try {
        const data = await $.getJSON('{% url "crm:cold_mail_batch_poll" %}?batch_id=' + batchId);
        if (data.status === 'completed') {
          clearInterval(poll);
          showResultsModal(data);
          Swal.close();
        } else if (data.status === 'failed') {
          clearInterval(poll);
          toast('Batch failed: ' + (data.detail || 'Unknown error'), 'error');
          Swal.close();
        }
      } catch (e) {
        // ignore poll errors
      }
    }, 2000);
  };

  /* ---------- show results modal ---------- */
  window.Crm.showResultsModal = function(data) {
    $('#resultsSent').text(data.sent);
    $('#resultsFailed').text(data.failed);
    $('#resultsSummary').val([
      'Batch: ' + (data.batch_id || ''),
      'Total: ' + data.total + '',
      'Sent: ' + data.sent + '',
      'Failed: ' + data.failed + '',
      'Remaining: ' + data.remaining + ''
    ].join('\n'));
    $('#resultsModal').modal('show');
  };

  /* ---------- download results CSV ---------- */
  window.Crm.downloadResultsCsv = function() {
    const format = $('#exportCsv:checked') ? 'csv' : 'json';
    window.location.href = '{% url "crm:cold_mail_export" %}?format=' + format;
  };
})();