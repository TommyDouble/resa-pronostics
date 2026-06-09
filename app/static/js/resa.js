/* RESA Pronostics 2026 — Vanilla JS */
'use strict';

/* ---- Prediction score entry ---- */
function initPredictionScores() {
  document.querySelectorAll('.prediction-card[data-match-id]').forEach(function(card) {
    var matchId = card.dataset.matchId;
    var token = document.body.dataset.token;
    var score1 = card.querySelector('.score-pick[data-side="1"]');
    var score2 = card.querySelector('.score-pick[data-side="2"]');
    var outcome = card.querySelector('[data-outcome]');
    var qualifierRow = card.querySelector('.qualifier-row');
    var qualifierBtns = card.querySelectorAll('.qualifier-btn');
    var errorBox = card.querySelector('.prediction-error');
    var saveIndicator = card.querySelector('.save-badge');
    var saveTimer = null;

    function scoreValue(inp) {
      if (!inp || inp.value === '') return null;
      var parsed = parseInt(inp.value, 10);
      return isNaN(parsed) ? null : parsed;
    }

    function clampScore(inp) {
      if (!inp || inp.value === '') return;
      var value = parseInt(inp.value, 10);
      if (isNaN(value) || value < 0) inp.value = 0;
      if (value > 30) inp.value = 30;
    }

    function derivedPrediction(s1, s2) {
      if (s1 > s2) return { value: 'team1', short: '1', label: card.dataset.team1 };
      if (s2 > s1) return { value: 'team2', short: '2', label: card.dataset.team2 };
      return { value: 'draw', short: 'X', label: 'Nul' };
    }

    function selectedQualifier() {
      var selected = card.querySelector('.qualifier-btn.on');
      return selected ? selected.dataset.value : null;
    }

    function setError(message) {
      if (!errorBox) return;
      errorBox.textContent = message || '';
      errorBox.classList.toggle('show', !!message);
    }

    function isKnockoutDraw(s1, s2) {
      return card.dataset.knockout === '1' && s1 === s2;
    }

    function updateOutcome() {
      var s1 = scoreValue(score1);
      var s2 = scoreValue(score2);
      if (s1 === null || s2 === null) {
        if (outcome) {
          outcome.textContent = 'Score requis';
          outcome.classList.add('empty');
        }
        if (qualifierRow) qualifierRow.classList.remove('open');
        return null;
      }
      var prediction = derivedPrediction(s1, s2);
      if (outcome) {
        outcome.textContent = 'Issue ' + prediction.short;
        outcome.classList.remove('empty');
      }
      if (qualifierRow) {
        qualifierRow.classList.toggle('open', isKnockoutDraw(s1, s2));
      }
      return prediction;
    }

    function markSaved(data) {
      card.classList.add('complete');
      var dot = card.querySelector('.mtop .dot');
      if (dot) {
        dot.classList.remove('gr', 'r');
        dot.classList.add('g');
      }
      var prediction = updateOutcome();
      var pronoText = card.querySelector('.prono-text');
      var s1 = scoreValue(score1);
      var s2 = scoreValue(score2);
      if (pronoText && prediction && s1 !== null && s2 !== null) {
        pronoText.textContent = 'Prono ' + s1 + '-' + s2 + ' · ' + prediction.label;
        pronoText.classList.remove('empty');
      }
      showSaveBadge(saveIndicator);
      showSaveBadge(document.getElementById('global-save-badge'));
      setError('');
    }

    function saveScorePrediction() {
      var s1 = scoreValue(score1);
      var s2 = scoreValue(score2);
      if (s1 === null || s2 === null) return;
      var body = {
        match_id: parseInt(matchId, 10),
        exact_score_team1: s1,
        exact_score_team2: s2
      };
      if (isKnockoutDraw(s1, s2)) {
        var qualifier = selectedQualifier();
        if (!qualifier) {
          setError('Choisis l’équipe qualifiée.');
          return;
        }
        body.qualifier_prediction = qualifier;
      }

      fetch('/api/predictions?token=' + encodeURIComponent(token), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      }).then(function(response) {
        return response.json().then(function(data) {
          if (!response.ok) throw data;
          return data;
        });
      }).then(function(data) {
        if (data.success) markSaved(data);
      }).catch(function(err) {
        setError((err && err.detail) || 'Enregistrement impossible, réessaie.');
      });
    }

    function queueSave() {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(saveScorePrediction, 500);
    }

    [score1, score2].forEach(function(inp) {
      if (!inp || inp.disabled) return;
      inp.addEventListener('input', function() {
        clampScore(inp);
        setError('');
        updateOutcome();
        if (scoreValue(score1) !== null && scoreValue(score2) !== null) queueSave();
      });
      inp.addEventListener('change', saveScorePrediction);
      inp.addEventListener('keydown', function(e) {
        if (e.key === 'ArrowUp') {
          inp.value = Math.min(30, (parseInt(inp.value, 10) || 0) + 1);
          updateOutcome();
          saveScorePrediction();
          e.preventDefault();
        }
        if (e.key === 'ArrowDown') {
          inp.value = Math.max(0, (parseInt(inp.value, 10) || 0) - 1);
          updateOutcome();
          saveScorePrediction();
          e.preventDefault();
        }
      });
    });

    qualifierBtns.forEach(function(btn) {
      if (btn.disabled) return;
      btn.addEventListener('click', function() {
        qualifierBtns.forEach(function(other) { other.classList.remove('on'); });
        btn.classList.add('on');
        setError('');
        saveScorePrediction();
      });
    });

    updateOutcome();
  });
}

function showSaveBadge(badge) {
  if (!badge) return;
  badge.style.display = 'inline-flex';
  badge.classList.add('show');
  setTimeout(function() {
    badge.classList.remove('show');
    if (badge.id === 'global-save-badge') badge.style.display = 'none';
  }, 2000);
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

/* ---- Local time display ---- */
function initLocalTimes() {
  var locale = navigator.language || 'fr-BE';
  var timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || '';

  function dateFromIso(iso) {
    var d = new Date(iso);
    return isNaN(d.getTime()) ? null : d;
  }

  function fmt(d, mode) {
    if (mode === 'time') {
      return new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit' }).format(d);
    }
    if (mode === 'date') {
      return new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit', year: 'numeric' }).format(d);
    }
    return new Intl.DateTimeFormat(locale, {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    }).format(d);
  }

  document.querySelectorAll('[data-local-utc]').forEach(function(el) {
    var d = dateFromIso(el.dataset.localUtc);
    if (!d) return;
    el.textContent = fmt(d, el.dataset.localFormat || 'datetime');
    if (timeZone) el.title = timeZone;
  });

  document.querySelectorAll('input[data-local-input-utc]').forEach(function(inp) {
    var d = dateFromIso(inp.dataset.localInputUtc);
    if (!d) return;
    var pad = function(n) { return String(n).padStart(2, '0'); };
    inp.value = d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
      'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  });

  document.querySelectorAll('[data-local-timezone]').forEach(function(el) {
    el.textContent = timeZone ? 'heure locale (' + timeZone + ')' : 'heure locale';
  });

  document.querySelectorAll('form').forEach(function(form) {
    if (!form.querySelector('input[type="datetime-local"]')) return;
    if (form.querySelector('input[name="timezone_name"]')) return;
    var hidden = document.createElement('input');
    hidden.type = 'hidden';
    hidden.name = 'timezone_name';
    hidden.value = timeZone;
    form.appendChild(hidden);
  });
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

/* ---- Winner ≠ finalist guard (participant + admin forms) ---- */
function initWinnerFinalistGuard() {
  document.querySelectorAll('form').forEach(function(form) {
    var winner = form.querySelector('select[name="winner"]');
    var finalist = form.querySelector('select[name="finalist"]');
    if (!winner || !finalist) return;
    var errorBox = form.querySelector('#winner-finalist-error');

    function syncDisabled() {
      // The selected winner can't be picked as finalist, and vice versa.
      Array.prototype.forEach.call(finalist.options, function(opt) {
        opt.disabled = !!opt.value && opt.value === winner.value;
      });
      Array.prototype.forEach.call(winner.options, function(opt) {
        opt.disabled = !!opt.value && opt.value === finalist.value;
      });
    }

    function conflict() {
      return winner.value && finalist.value && winner.value === finalist.value;
    }

    function showError(show) {
      if (errorBox) errorBox.style.display = show ? 'block' : 'none';
      finalist.style.borderColor = show ? 'var(--error, #DC2626)' : '';
    }

    [winner, finalist].forEach(function(sel) {
      sel.addEventListener('change', function() {
        if (conflict()) {
          // Clear the other field rather than keeping an invalid pair.
          (sel === winner ? finalist : winner).value = '';
        }
        showError(false);
        syncDisabled();
      });
    });

    form.addEventListener('submit', function(e) {
      if (conflict()) {
        showError(true);
        alert('Le vainqueur et le finaliste ne peuvent pas être identiques.');
        e.preventDefault();
      }
    });

    syncDisabled();
  });
}

/* ---- Top scorer select filter (1200+ players) ---- */
function initScorerFilter() {
  var filter = document.getElementById('scorer-filter');
  var select = document.getElementById('top-scorer-select');
  if (!filter || !select) return;
  var groups = Array.prototype.slice.call(select.querySelectorAll('optgroup'));

  filter.addEventListener('input', function() {
    var q = filter.value.toLowerCase().trim();
    groups.forEach(function(group) {
      var visible = 0;
      Array.prototype.forEach.call(group.querySelectorAll('option'), function(opt) {
        var match = !q || (opt.dataset.search || '').indexOf(q) !== -1;
        opt.hidden = !match;
        if (match) visible++;
      });
      group.hidden = visible === 0;
    });
    if (q) {
      // Expand the list while filtering so matches are visible at a glance.
      var visibleCount = select.querySelectorAll('option:not([hidden])').length;
      select.size = Math.min(Math.max(visibleCount + 1, 2), 10);
    } else {
      select.size = 1;
    }
  });

  select.addEventListener('change', function() {
    if (select.value) {
      filter.value = '';
      groups.forEach(function(group) {
        group.hidden = false;
        Array.prototype.forEach.call(group.querySelectorAll('option'), function(opt) { opt.hidden = false; });
      });
      select.size = 1;
    }
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
  initLocalTimes();
  initPredictionScores();
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
  initWinnerFinalistGuard();
  initScorerFilter();
});
