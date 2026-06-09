/* RESA Pronostics 2026 — Vanilla JS */
'use strict';

/* ---- Prediction pills ---- */
function initPredictionPills() {
  document.querySelectorAll('.match-card[data-match-id]').forEach(function(card) {
    var matchId = card.dataset.matchId;
    var token = document.body.dataset.token;
    var pills = card.querySelectorAll('.pills .p:not(.lock)');
    var exactRow = card.querySelector('.exact-row');
    var saveIndicator = card.querySelector('.save-badge');

    pills.forEach(function(pill) {
      pill.addEventListener('click', function() {
        var value = pill.dataset.value;
        // update UI immediately
        pills.forEach(function(p) { p.classList.remove('on'); });
        pill.classList.add('on');
        // expand score exact
        if (exactRow) exactRow.classList.add('open');
        // save to server
        savePrediction(matchId, token, value, card, saveIndicator);
      });
    });

    // Score exact auto-save on blur
    var exactInputs = card.querySelectorAll('.mini-input');
    exactInputs.forEach(function(inp) {
      inp.addEventListener('change', function() {
        var activePill = card.querySelector('.pills .p.on');
        if (!activePill) return;
        var s1 = card.querySelector('.mini-input[data-side="1"]');
        var s2 = card.querySelector('.mini-input[data-side="2"]');
        if (!s1 || !s2 || s1.value === '' || s2.value === '') return;
        savePrediction(matchId, token, activePill.dataset.value, card, saveIndicator,
          s1 ? parseInt(s1.value) : null, s2 ? parseInt(s2.value) : null);
      });
    });
  });
}

function savePrediction(matchId, token, prediction, card, badge, score1, score2) {
  var body = { match_id: parseInt(matchId), prediction: prediction };
  if (score1 !== null && score1 !== undefined && score2 !== null && score2 !== undefined &&
      !isNaN(score1) && !isNaN(score2) && scoreOutcome(score1, score2) !== prediction) {
    alert('Le score exact ne correspond pas au pronostic choisi.');
    return;
  }
  if (score1 !== null && score1 !== undefined && !isNaN(score1)) body.exact_score_team1 = score1;
  if (score2 !== null && score2 !== undefined && !isNaN(score2)) body.exact_score_team2 = score2;

  fetch('/api/predictions?token=' + encodeURIComponent(token), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  }).then(function(r) { return r.json(); }).then(function(data) {
    if (data.success) showSaveBadge(badge);
    if (!data.success && data.detail) alert(data.detail);
  }).catch(function() {
    // silently fail — user can retry
  });
}

function scoreOutcome(score1, score2) {
  if (score1 > score2) return 'team1';
  if (score2 > score1) return 'team2';
  return 'draw';
}

function showSaveBadge(badge) {
  if (!badge) return;
  badge.classList.add('show');
  setTimeout(function() { badge.classList.remove('show'); }, 2000);
}

/* ---- Score exact stepper inputs ---- */
function initMiniInputs() {
  document.querySelectorAll('.mini-input').forEach(function(inp) {
    inp.addEventListener('input', function() {
      var v = parseInt(inp.value);
      if (isNaN(v) || v < 0) inp.value = 0;
      if (v > 30) inp.value = 30;
    });
    inp.addEventListener('keydown', function(e) {
      if (e.key === 'ArrowUp') { inp.value = Math.min(30, (parseInt(inp.value) || 0) + 1); inp.dispatchEvent(new Event('change')); e.preventDefault(); }
      if (e.key === 'ArrowDown') { inp.value = Math.max(0, (parseInt(inp.value) || 0) - 1); inp.dispatchEvent(new Event('change')); e.preventDefault(); }
    });
  });
}

/* ---- Goals stepper (pre-tournament) ---- */
function initStepper(id, min, max) {
  var wrap = document.getElementById(id);
  if (!wrap) return;
  var inp = wrap.querySelector('input, .v');
  var btnM = wrap.querySelector('[data-action="minus"]');
  var btnP = wrap.querySelector('[data-action="plus"]');
  function getVal() { return parseInt(inp.value || inp.textContent) || min; }
  function setVal(v) {
    v = Math.min(max, Math.max(min, v));
    if (inp.tagName === 'INPUT') inp.value = v; else inp.textContent = v;
  }
  if (btnM) btnM.addEventListener('click', function() { setVal(getVal() - 1); });
  if (btnP) btnP.addEventListener('click', function() { setVal(getVal() + 1); });
}

/* ---- Countdown timer ---- */
function initCountdown() {
  var el = document.querySelector('[data-countdown]');
  if (!el) return;
  var target = parseInt(el.dataset.countdown);
  function update() {
    var diff = target - Math.floor(Date.now() / 1000);
    if (diff <= 0) { el.textContent = 'Commencé !'; return; }
    var h = Math.floor(diff / 3600);
    var m = Math.floor((diff % 3600) / 60);
    var s = diff % 60;
    el.textContent = (h > 0 ? h + 'h ' : '') + pad(m) + 'min ' + pad(s) + 's';
    setTimeout(update, 1000);
  }
  function pad(n) { return n < 10 ? '0' + n : String(n); }
  update();
}

/* ---- Pre-tournament outsider chips ---- */
function initOutsiderChips() {
  var inp = document.getElementById('revelation-input');
  document.querySelectorAll('.outsider-chip').forEach(function(chip) {
    chip.addEventListener('click', function() {
      document.querySelectorAll('.outsider-chip').forEach(function(c) { c.classList.remove('on'); });
      chip.classList.add('on');
      if (inp) inp.value = chip.dataset.value;
    });
  });
}

/* ---- Autocomplete (top scorer) ---- */
function initAutocomplete(inputId, listId, items) {
  var inp = document.getElementById(inputId);
  var list = document.getElementById(listId);
  if (!inp || !list) return;
  inp.addEventListener('input', function() {
    var q = inp.value.toLowerCase().trim();
    list.innerHTML = '';
    if (q.length < 2) { list.style.display = 'none'; return; }
    var matches = items.filter(function(i) { return i.toLowerCase().includes(q); }).slice(0, 8);
    if (!matches.length) { list.style.display = 'none'; return; }
    matches.forEach(function(m) {
      var li = document.createElement('li');
      li.textContent = m;
      li.addEventListener('click', function() { inp.value = m; list.style.display = 'none'; });
      list.appendChild(li);
    });
    list.style.display = 'block';
  });
  document.addEventListener('click', function(e) {
    if (!inp.contains(e.target)) list.style.display = 'none';
  });
}

/* ---- Flash message auto-dismiss ---- */
function initFlash() {
  document.querySelectorAll('.flash-msg').forEach(function(msg) {
    setTimeout(function() {
      msg.style.transition = 'opacity 300ms';
      msg.style.opacity = '0';
      setTimeout(function() { msg.remove(); }, 300);
    }, 3000);
  });
}

/* ---- Admin: toggle Top Match ---- */
function initTopMatchToggles() {
  document.querySelectorAll('[data-toggle-top]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var matchId = btn.dataset.toggleTop;
      fetch('/admin/matches/' + matchId + '/toggle-top', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(data) {
          if (data.is_top_match) {
            btn.textContent = '★ ×2';
            btn.classList.remove('gr'); btn.classList.add('acc');
          } else {
            btn.textContent = '○ ×1';
            btn.classList.remove('acc'); btn.classList.add('gr');
          }
        });
    });
  });
}

/* ---- Admin: score validation warning ---- */
function initResultForms() {
  document.querySelectorAll('.result-form').forEach(function(form) {
    form.addEventListener('submit', function(e) {
      var s1 = parseInt(form.querySelector('[name="score_team1"]').value);
      var s2 = parseInt(form.querySelector('[name="score_team2"]').value);
      var phase = form.dataset.phase;
      var isKnockout = phase && phase !== 'group';
      if (s1 === 0 && s2 === 0 && isKnockout) {
        if (!confirm('Score 0-0 sur un match éliminatoire. Confirmer ?')) {
          e.preventDefault();
        }
      }
    });
  });
}

/* ---- Admin: participant search filter ---- */
function initParticipantSearch() {
  var inp = document.getElementById('participant-search');
  if (!inp) return;
  inp.addEventListener('input', function() {
    var q = inp.value.toLowerCase();
    document.querySelectorAll('.participant-row').forEach(function(row) {
      var text = row.textContent.toLowerCase();
      row.style.display = text.includes(q) ? '' : 'none';
    });
  });
}

/* ---- Admin: auto-refresh dashboard ---- */
function initAutoRefresh(seconds) {
  if (!document.querySelector('.admin-dashboard')) return;
  setTimeout(function() { window.location.reload(); }, seconds * 1000);
}

/* ---- CSV import validation ---- */
function initCsvImport() {
  var form = document.getElementById('csv-import-form');
  if (!form) return;
  var fileInp = form.querySelector('[type="file"]');
  var preview = document.getElementById('csv-preview');
  if (!fileInp || !preview) return;
  fileInp.addEventListener('change', function() {
    var file = fileInp.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function(e) {
      var lines = e.target.result.split('\n').filter(Boolean);
      preview.textContent = lines.length + ' ligne(s) détectée(s)';
    };
    reader.readAsText(file);
  });
}

/* ---- Phase filter (predictions page) ---- */
function initPhaseFilter() {
  var btns = document.querySelectorAll('.phase-filter-btn');
  if (!btns.length) return;
  btns.forEach(function(btn) {
    btn.addEventListener('click', function() {
      btns.forEach(function(b) { b.classList.remove('on'); });
      btn.classList.add('on');
      var phase = btn.dataset.phase;
      document.querySelectorAll('.phase-section').forEach(function(sec) {
        sec.style.display = (!phase || sec.dataset.phase === phase || phase === 'all') ? '' : 'none';
      });
    });
  });
}

/* ---- Init all on DOM ready ---- */
document.addEventListener('DOMContentLoaded', function() {
  initPredictionPills();
  initMiniInputs();
  initCountdown();
  initOutsiderChips();
  initFlash();
  initTopMatchToggles();
  initResultForms();
  initParticipantSearch();
  initAutoRefresh(60);
  initCsvImport();
  initPhaseFilter();
  initStepper('goals-stepper', 50, 300);

  // Scorers autocomplete — injected by template
  if (typeof SCORERS !== 'undefined') {
    initAutocomplete('top-scorer-input', 'scorer-list', SCORERS);
  }
});
