/* RESA Pronostics 2026 — Vanilla JS */
'use strict';

/* ---- Prediction score entry ---- */
function initPredictionScores() {
  document.querySelectorAll('.prediction-card[data-match-id]').forEach(function(card) {
    var matchId = card.dataset.matchId;
    var token = document.body.dataset.token;
    var score1 = card.querySelector('.score-pick[data-side="1"]');
    var score2 = card.querySelector('.score-pick[data-side="2"]');
    // Carte verrouillée, terminée (sans inputs) ou pronos pas encore ouverts:
    // rien d'interactif, le rendu serveur fait foi.
    if (!score1 || score1.disabled) return;
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
      if (s1 > s2) {
        return {
          value: 'team1',
          short: '1',
          label: card.dataset.team1,
          outcome: 'Victoire ' + card.dataset.team1
        };
      }
      if (s2 > s1) {
        return {
          value: 'team2',
          short: '2',
          label: card.dataset.team2,
          outcome: 'Victoire ' + card.dataset.team2
        };
      }
      return { value: 'draw', short: 'X', label: 'Match nul', outcome: 'Match nul' };
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
        outcome.textContent = prediction.outcome;
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

/* ---- Tooltips that stay inside the viewport ---- */
function initFloatingTooltips() {
  var triggers = document.querySelectorAll('.help-tip[data-tip], .tip[data-tip]');
  if (!triggers.length) return;

  document.body.classList.add('tooltip-floating');

  var bubble = document.createElement('div');
  bubble.className = 'floating-tooltip';
  bubble.setAttribute('role', 'tooltip');
  document.body.appendChild(bubble);

  var active = null;

  function placeTooltip(trigger) {
    var text = trigger.getAttribute('data-tip');
    if (!text) return;
    active = trigger;
    bubble.textContent = text;
    bubble.style.maxWidth = Math.min(300, window.innerWidth - 24) + 'px';
    bubble.style.left = '12px';
    bubble.style.top = '12px';
    bubble.classList.add('show');

    var triggerRect = trigger.getBoundingClientRect();
    var bubbleRect = bubble.getBoundingClientRect();
    var left = triggerRect.left + triggerRect.width / 2 - bubbleRect.width / 2;
    left = Math.max(12, Math.min(left, window.innerWidth - bubbleRect.width - 12));

    var top = triggerRect.top - bubbleRect.height - 10;
    if (top < 12) top = triggerRect.bottom + 10;
    if (top + bubbleRect.height > window.innerHeight - 12) {
      top = Math.max(12, window.innerHeight - bubbleRect.height - 12);
    }

    bubble.style.left = left + 'px';
    bubble.style.top = top + 'px';
  }

  function hideTooltip(trigger) {
    if (trigger && active && trigger !== active) return;
    active = null;
    bubble.classList.remove('show');
  }

  triggers.forEach(function(trigger) {
    trigger.addEventListener('mouseenter', function() { placeTooltip(trigger); });
    trigger.addEventListener('focus', function() { placeTooltip(trigger); });
    trigger.addEventListener('mouseleave', function() { hideTooltip(trigger); });
    trigger.addEventListener('blur', function() { hideTooltip(trigger); });
    trigger.addEventListener('click', function(e) {
      e.stopPropagation();
      placeTooltip(trigger);
    });
  });

  document.addEventListener('click', function() { hideTooltip(); });
  window.addEventListener('resize', function() {
    if (active) placeTooltip(active);
  });
  window.addEventListener('scroll', function() {
    if (active) placeTooltip(active);
  }, { passive: true });
}

/* ---- Prediction anchor scroll offset ---- */
function initPredictionAnchorScroll() {
  function stickyOffset() {
    var offset = 14;
    var pageHead = document.querySelector('.page-head');
    var topNav = document.getElementById('top-nav');
    if (pageHead) offset += pageHead.getBoundingClientRect().height;
    if (topNav && getComputedStyle(topNav).display !== 'none') {
      offset += topNav.getBoundingClientRect().height;
    }
    return offset;
  }

  function scrollToMatch(matchId, smooth) {
    var target = document.getElementById(matchId);
    if (!target) return false;
    var y = target.getBoundingClientRect().top + window.pageYOffset - stickyOffset();
    window.scrollTo({ top: Math.max(0, y), behavior: smooth ? 'smooth' : 'auto' });
    target.classList.add('anchor-highlight');
    setTimeout(function() { target.classList.remove('anchor-highlight'); }, 1600);
    return true;
  }

  document.querySelectorAll('.next-incomplete[href^="#match-"]').forEach(function(link) {
    link.addEventListener('click', function(e) {
      var id = link.getAttribute('href').slice(1);
      if (!scrollToMatch(id, true)) return;
      history.replaceState(null, '', '#' + id);
      e.preventDefault();
    });
  });

  if (window.location.hash && window.location.hash.indexOf('#match-') === 0) {
    setTimeout(function() {
      scrollToMatch(window.location.hash.slice(1), false);
    }, 80);
  }
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

/* ---- Countdown timers ---- */
function initCountdown() {
  function pad(n) { return n < 10 ? '0' + n : String(n); }
  document.querySelectorAll('[data-countdown]').forEach(function(el) {
    var target = parseInt(el.dataset.countdown);
    function update() {
      var diff = target - Math.floor(Date.now() / 1000);
      if (diff <= 0) { el.textContent = 'Commencé !'; return; }
      var d = Math.floor(diff / 86400);
      var h = Math.floor((diff % 86400) / 3600);
      var m = Math.floor((diff % 3600) / 60);
      var s = diff % 60;
      if (d > 0) {
        el.textContent = d + 'j ' + h + 'h ' + pad(m) + 'min';
        setTimeout(update, 30000);
      } else {
        el.textContent = (h > 0 ? h + 'h ' : '') + pad(m) + 'min ' + pad(s) + 's';
        setTimeout(update, 1000);
      }
    }
    update();
  });
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
    var championMirror = form.querySelector('[data-champion-mirror]');

    function syncChampionMirror() {
      // Slot "Finaliste 1" shows the champion so it's obvious he's also a finalist.
      if (!championMirror) return;
      championMirror.value = winner.value || '';
      championMirror.classList.toggle('is-empty', !winner.value);
    }

    function syncDisabled() {
      // The selected champion can't be picked as the other finalist, and vice versa.
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
        syncChampionMirror();
      });
    });

    form.addEventListener('submit', function(e) {
      if (conflict()) {
        showError(true);
        if (errorBox) errorBox.scrollIntoView({ block: 'center', behavior: 'smooth' });
        e.preventDefault();
      }
    });

    syncDisabled();
    syncChampionMirror();
  });
}

/* ---- Top scorer combobox (1200+ players) ---- */
function initScorerCombos() {
  var dataEl = document.getElementById('scorer-data');
  var combos = document.querySelectorAll('[data-scorer-combo]');
  if (!dataEl || !combos.length) return;
  var players;
  try { players = JSON.parse(dataEl.textContent); } catch (e) { return; }

  function fold(s) {
    return s.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  }
  players.forEach(function(p) { p.search = fold(p.name + ' ' + p.team); });

  combos.forEach(function(combo) {
    var input = combo.querySelector('[data-combo-input]');
    var hidden = combo.querySelector('[data-combo-value]');
    var list = combo.querySelector('[data-combo-list]');
    if (!input || !hidden || !list || input.disabled) return;
    var activeIndex = -1;

    function close() {
      list.style.display = 'none';
      list.innerHTML = '';
      activeIndex = -1;
    }

    function commit(value) {
      hidden.value = value;
      input.value = value;
      close();
      input.dispatchEvent(new Event('combo-change'));
    }

    function setActive(index) {
      var items = list.querySelectorAll('li[data-value]');
      if (!items.length) return;
      activeIndex = (index + items.length) % items.length;
      Array.prototype.forEach.call(items, function(li, i) {
        li.classList.toggle('on', i === activeIndex);
      });
      items[activeIndex].scrollIntoView({ block: 'nearest' });
    }

    function playerItem(p, withTeam) {
      var li = document.createElement('li');
      li.dataset.value = p.value;
      li.innerHTML = '<b></b><span class="meta"></span>';
      li.querySelector('b').textContent = p.name;
      li.querySelector('.meta').textContent =
        withTeam && p.position ? p.position + ' · ' + p.team
        : (p.position || p.team);
      // mousedown fires before the input's blur, so the click isn't lost
      li.addEventListener('mousedown', function(e) {
        e.preventDefault();
        commit(p.value);
      });
      return li;
    }

    function renderBrowse() {
      // Full list, grouped by nation (players come pre-sorted: team, position, name).
      var fragment = document.createDocumentFragment();
      var currentTeam = null;
      var selectedItem = null;
      players.forEach(function(p) {
        if (p.team !== currentTeam) {
          currentTeam = p.team;
          var header = document.createElement('li');
          header.className = 'group-header';
          header.textContent = p.team;
          header.addEventListener('mousedown', function(e) { e.preventDefault(); });
          fragment.appendChild(header);
        }
        var li = playerItem(p, false);
        if (p.value === hidden.value) {
          li.classList.add('on');
          selectedItem = li;
        }
        fragment.appendChild(li);
      });
      list.innerHTML = '';
      list.appendChild(fragment);
      list.style.display = 'block';
      if (selectedItem) selectedItem.scrollIntoView({ block: 'center' });
      else list.scrollTop = 0;
    }

    function renderSearch(q) {
      list.innerHTML = '';
      var matches = players.filter(function(p) { return p.search.indexOf(q) !== -1; });
      if (!matches.length) {
        list.innerHTML = '<li class="hint">Aucun joueur trouvé.</li>';
        list.style.display = 'block';
        return;
      }
      matches.slice(0, 30).forEach(function(p) {
        list.appendChild(playerItem(p, true));
      });
      if (matches.length > 30) {
        var more = document.createElement('li');
        more.className = 'hint';
        more.textContent = (matches.length - 30) + ' autres joueurs — affine ta recherche…';
        list.appendChild(more);
      }
      list.style.display = 'block';
    }

    function render() {
      activeIndex = -1;
      var raw = input.value.trim();
      // A confirmed selection in the input isn't a search: browse instead.
      if (!raw || raw === hidden.value) renderBrowse();
      else renderSearch(fold(raw));
    }

    input.addEventListener('input', render);
    input.addEventListener('focus', function() {
      // Select the canonical text so typing starts a fresh search,
      // and open the browsable list right away.
      if (input.value === hidden.value && input.value) input.select();
      render();
    });
    input.addEventListener('mousedown', function() {
      // Reopen on click even when already focused (select-like behavior).
      if (list.style.display === 'none') render();
    });
    input.addEventListener('keydown', function(e) {
      if (e.key === 'ArrowDown') { setActive(activeIndex + 1); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { setActive(activeIndex - 1); e.preventDefault(); }
      else if (e.key === 'Enter') {
        var items = list.querySelectorAll('li[data-value]');
        var searching = input.value.trim() && input.value !== hidden.value;
        if (list.style.display !== 'none' && items.length
            && (activeIndex !== -1 || searching)) {
          commit(items[activeIndex === -1 ? 0 : activeIndex].dataset.value);
          e.preventDefault();
        }
      } else if (e.key === 'Escape') {
        close();
        input.value = hidden.value;
      }
    });
    input.addEventListener('blur', function() {
      close();
      // Keep only confirmed picks: empty clears, anything else reverts.
      if (input.value.trim() === '') hidden.value = '';
      else input.value = hidden.value;
    });
  });
}

/* ---- Bonus answer validation ---- */
function initBonusForms() {
  document.querySelectorAll('.bonus-answer-form').forEach(function(form) {
    var errorBox = form.querySelector('.form-error');
    function setError(message) {
      if (!errorBox) return;
      errorBox.textContent = message || '';
      errorBox.classList.toggle('show', !!message);
    }
    form.addEventListener('submit', function(e) {
      var radios = form.querySelectorAll('input[type="radio"][name="answer"]');
      if (radios.length) {
        var checked = Array.prototype.some.call(radios, function(radio) {
          return radio.checked;
        });
        if (!checked) {
          e.preventDefault();
          setError('Choisis une réponse avant de valider.');
          return;
        }
      }
      var text = form.querySelector('input[type="text"][name="answer"]');
      if (text && !text.value.trim()) {
        e.preventDefault();
        setError('Indique ta réponse avant de valider.');
        return;
      }
      setError('');
    });
    form.querySelectorAll('input[name="answer"]').forEach(function(input) {
      input.addEventListener('input', function() { setError(''); });
      input.addEventListener('change', function() { setError(''); });
    });
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

/* ---- Admin: toggle paiement sans recharger ---- */
function initPaidToggles() {
  document.querySelectorAll('[data-toggle-paid]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      btn.disabled = true;
      fetch('/admin/participants/' + btn.dataset.togglePaid + '/toggle-paid', { method: 'POST' })
        .then(function(r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(function(data) {
          var paid = !!data.has_paid;
          btn.textContent = paid ? '✓ payé' : '— en att.';
          btn.classList.toggle('ok', paid);
          btn.classList.toggle('gr', !paid);
          // Garde le tri par colonne cohérent au prochain clic d'en-tête.
          var cell = btn.closest('td');
          if (cell) cell.setAttribute('data-sort-value', paid ? '1' : '0');
        })
        .catch(function() {
          alert('Mise à jour du paiement impossible, réessaie.');
        })
        .then(function() { btn.disabled = false; });
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
    var qualifier = form.querySelector('select[name="qualifier_winner"]');
    if (isKnockout && s1 === s2 && qualifier && !qualifier.value) {
      alert("Choisis l'équipe qualifiée pour ce match de phase finale.");
      e.preventDefault();
      return;
    }
    if (s1 === 0 && s2 === 0 && isKnockout) {
      if (!confirm('Score 0-0 sur un match éliminatoire. Confirmer ?')) {
        e.preventDefault();
      }
      }
    });
  });
}

/* ---- Admin: generic table search + sort ---- */
function initAdminTables() {
  document.querySelectorAll('table[data-admin-table]').forEach(function(table) {
    var tbody = table.tBodies[0];
    if (!tbody) return;

    var rows = Array.prototype.filter.call(tbody.rows, function(row) {
      return !row.hasAttribute('data-empty-row');
    });
    var emptyRow = tbody.querySelector('[data-empty-row]');
    if (!emptyRow) {
      emptyRow = document.createElement('tr');
      emptyRow.setAttribute('data-empty-row', '');
      emptyRow.style.display = 'none';
      var emptyCell = document.createElement('td');
      emptyCell.colSpan = table.tHead && table.tHead.rows[0] ? table.tHead.rows[0].cells.length : 1;
      emptyCell.style.textAlign = 'center';
      emptyCell.style.padding = '24px';
      emptyCell.style.color = 'var(--n400)';
      emptyCell.textContent = 'Aucune ligne ne correspond.';
      emptyRow.appendChild(emptyCell);
      tbody.appendChild(emptyRow);
    }

    var searchInput = null;
    var countEl = null;
    if (table.dataset.adminSearch) {
      searchInput = document.getElementById(table.dataset.adminSearch);
    }

    if (table.dataset.adminNoFilter !== '1') {
      var tools = searchInput ? (searchInput.closest('.toolbar2') || searchInput.parentElement) : null;
      if (!searchInput) {
        tools = document.createElement('div');
        tools.className = 'admin-table-tools';
        searchInput = document.createElement('input');
        searchInput.type = 'text';
        searchInput.className = 'input-search';
        searchInput.placeholder = table.dataset.adminSearchPlaceholder || '⌕ Filtrer...';
        tools.appendChild(searchInput);
        table.parentNode.insertBefore(tools, table);
      }
      countEl = document.createElement('span');
      countEl.className = 'admin-table-count';
      tools.appendChild(countEl);
    }

    function sortValue(cell, type) {
      if (!cell) return '';
      var raw = cell.getAttribute('data-sort-value');
      if (raw === null) raw = cell.textContent.trim();
      if (type === 'number') {
        var number = parseFloat(String(raw).replace(',', '.'));
        return Number.isFinite(number) ? number : -Infinity;
      }
      if (type === 'date') {
        var time = Date.parse(raw);
        return Number.isFinite(time) ? time : 0;
      }
      return String(raw).toLowerCase();
    }

    function applyFilter() {
      var q = searchInput ? searchInput.value.trim().toLowerCase() : '';
      var visible = 0;
      rows.forEach(function(row) {
        var text = (row.dataset.search || row.textContent).toLowerCase();
        var match = !q || text.indexOf(q) !== -1;
        row.style.display = match ? '' : 'none';
        if (match) visible += 1;
      });
      emptyRow.style.display = rows.length === 0 || visible === 0 ? '' : 'none';
      if (countEl) {
        countEl.textContent = q ? (visible + '/' + rows.length + ' ligne(s)') : (rows.length + ' ligne(s)');
      }
    }

    table.querySelectorAll('thead th[data-sort]').forEach(function(th) {
      th.addEventListener('click', function() {
        var headerRow = th.parentNode;
        var index = Array.prototype.indexOf.call(headerRow.children, th);
        var type = th.dataset.sort || 'text';
        var direction = th.classList.contains('sort-asc') ? 'desc' : 'asc';
        table.querySelectorAll('thead th[data-sort]').forEach(function(other) {
          other.classList.remove('sort-asc', 'sort-desc');
        });
        th.classList.add(direction === 'asc' ? 'sort-asc' : 'sort-desc');
        rows.sort(function(a, b) {
          var av = sortValue(a.cells[index], type);
          var bv = sortValue(b.cells[index], type);
          if (av < bv) return direction === 'asc' ? -1 : 1;
          if (av > bv) return direction === 'asc' ? 1 : -1;
          return 0;
        });
        rows.forEach(function(row) { tbody.appendChild(row); });
        tbody.appendChild(emptyRow);
        applyFilter();
      });
    });

    if (searchInput) searchInput.addEventListener('input', applyFilter);
    applyFilter();
  });
}

/* ---- Admin: push test target mode ---- */
function initAdminPushTarget() {
  var form = document.querySelector('[data-push-test-form]');
  if (!form) return;
  var allTarget = form.querySelector('[data-push-target-all]');
  var recipients = form.querySelectorAll('[data-push-recipient]');
  if (!allTarget || !recipients.length) return;

  recipients.forEach(function(cb) {
    cb.dataset.baseDisabled = cb.disabled ? '1' : '0';
  });

  function updateRecipients() {
    recipients.forEach(function(cb) {
      cb.disabled = allTarget.checked || cb.dataset.baseDisabled === '1';
    });
  }

  allTarget.addEventListener('change', updateRecipients);
  updateRecipients();
}

/* ---- Admin: auto-refresh dashboard ---- */
function initAutoRefresh(seconds) {
  if (!document.querySelector('.admin-dashboard')) return;
  function busy() {
    // Ne pas recharger pendant une saisie ou un formulaire déplié.
    var el = document.activeElement;
    if (el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) return true;
    return !!document.querySelector('details[open]');
  }
  function tick() {
    if (busy()) {
      setTimeout(tick, seconds * 1000);
      return;
    }
    window.location.reload();
  }
  setTimeout(tick, seconds * 1000);
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

/* ---- Notifications push (plan hybride: push si possible, sinon email) ---- */
function initPush() {
  var cards = document.querySelectorAll('[data-push-card]');
  if (!cards.length) return;
  var token = document.body.dataset.token;
  if (!token || !('serviceWorker' in navigator)) return;

  function urlBase64ToUint8Array(base64String) {
    var padding = '='.repeat((4 - base64String.length % 4) % 4);
    var base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    var raw = window.atob(base64);
    var output = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; ++i) output[i] = raw.charCodeAt(i);
    return output;
  }

  var standalone = window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone === true;
  var isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  var supported = 'PushManager' in window && 'Notification' in window;

  fetch('/api/push/config?token=' + encodeURIComponent(token))
    .then(function(r) { return r.json(); })
    .then(function(cfg) {
      if (!cfg.enabled) return;
      navigator.serviceWorker.register('/sw.js').then(function(reg) {
        cards.forEach(function(card) { setupCard(card, reg, cfg); });
      });
    })
    .catch(function() {});

  function setupCard(card, reg, cfg) {
    var statusEl = card.querySelector('[data-push-status]');
    var toggleBtn = card.querySelector('[data-push-toggle]');
    var iosHelp = card.querySelector('[data-push-ios-help]');
    var dismissBtn = card.querySelector('[data-push-dismiss]');
    var isPromo = card.hasAttribute('data-push-promo');

    if (isPromo && localStorage.getItem('pushPromoDismissed') === '1') return;
    if (dismissBtn) {
      dismissBtn.addEventListener('click', function() {
        localStorage.setItem('pushPromoDismissed', '1');
        card.style.display = 'none';
      });
    }

    if (!supported) {
      // iPhone hors app installée: guider vers l'installation.
      if (isIOS && !standalone) {
        card.style.display = '';
        if (iosHelp) iosHelp.style.display = '';
      }
      return;
    }

    function render(sub) {
      card.style.display = '';
      if (isPromo && sub) { card.style.display = 'none'; return; }
      if (toggleBtn) {
        toggleBtn.style.display = '';
        toggleBtn.textContent = sub ? 'Désactiver les notifications' : (isPromo ? 'Activer' : 'Activer les notifications');
      }
      if (statusEl && !isPromo) {
        statusEl.textContent = sub
          ? 'Notifications activées sur cet appareil ✓ — tu ne reçois plus les emails de rappel ici.'
          : 'Reçois les rappels (matchs, bonus, récap) directement sur ton téléphone — sinon tu les reçois par email.';
      }
    }

    reg.pushManager.getSubscription().then(function(sub) {
      render(sub);
      if (!toggleBtn) return;
      toggleBtn.addEventListener('click', function() {
        reg.pushManager.getSubscription().then(function(current) {
          if (current) {
            current.unsubscribe().then(function() {
              fetch('/api/push/unsubscribe?token=' + encodeURIComponent(token), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ endpoint: current.endpoint })
              });
              render(null);
            });
            return;
          }
          Notification.requestPermission().then(function(permission) {
            if (permission !== 'granted') return;
            reg.pushManager.subscribe({
              userVisibleOnly: true,
              applicationServerKey: urlBase64ToUint8Array(cfg.publicKey)
            }).then(function(sub) {
              fetch('/api/push/subscribe?token=' + encodeURIComponent(token), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(sub.toJSON())
              }).then(function() { render(sub); });
            }).catch(function() {});
          });
        });
      });
    });
  }
}

/* ---- Match detail: suivi live ---- */
function initMatchLive() {
  var board = document.querySelector('.scoreboard[data-live-state]');
  if (!board) return;
  var state = board.dataset.liveState;
  var token = document.body.dataset.token;

  // Temps écoulé depuis le coup d'envoi, rafraîchi chaque minute.
  var elapsedEl = board.querySelector('[data-elapsed-since]');
  if (elapsedEl) {
    var kickoff = new Date(elapsedEl.dataset.elapsedSince);
    function renderElapsed() {
      var mins = Math.floor((Date.now() - kickoff.getTime()) / 60000);
      if (mins < 0) return;
      elapsedEl.textContent = mins < 60
        ? '· coup d’envoi il y a ' + mins + ' min'
        : '· coup d’envoi il y a ' + Math.floor(mins / 60) + ' h ' + (mins % 60) + ' min';
      setTimeout(renderElapsed, 60000);
    }
    renderElapsed();
  }

  // Tant que le résultat n'est pas encodé, on vérifie régulièrement :
  // dès qu'il tombe, on recharge pour afficher points et classement du match.
  if ((state === 'live' || state === 'awaiting') && token) {
    var matchId = board.dataset.matchId;
    setInterval(function() {
      fetch('/api/match/' + matchId + '/status?token=' + encodeURIComponent(token))
        .then(function(r) { return r.json(); })
        .then(function(data) {
          if (data.state === 'done') window.location.reload();
        })
        .catch(function() {});
    }, 60000);
  }
}

/* ---- Compteur animé pour les points gagnés ---- */
function initCountUp() {
  var els = document.querySelectorAll('[data-countup]');
  if (!els.length) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  function animate(el) {
    var target = parseInt(el.dataset.countup, 10);
    if (!target || target <= 0) return;
    var start = null;
    var duration = 700;
    function step(ts) {
      if (start === null) start = ts;
      var progress = Math.min((ts - start) / duration, 1);
      // ease-out cubic
      var eased = 1 - Math.pow(1 - progress, 3);
      el.textContent = Math.round(eased * target);
      if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  if (!('IntersectionObserver' in window)) return;
  var seen = new WeakSet();
  var observer = new IntersectionObserver(function(entries) {
    entries.forEach(function(entry) {
      if (!entry.isIntersecting || seen.has(entry.target)) return;
      seen.add(entry.target);
      observer.unobserve(entry.target);
      animate(entry.target);
    });
  }, { threshold: 0.5 });
  els.forEach(function(el) { observer.observe(el); });
}

/* ---- Offset des en-têtes sticky sous le bandeau de page ---- */
function initStickyTop() {
  var pageHead = document.querySelector('.page-head');
  if (!pageHead || !document.querySelector('.pgroup-head')) return;
  function apply() {
    document.documentElement.style.setProperty(
      '--sticky-top', pageHead.getBoundingClientRect().height + 'px');
  }
  apply();
  window.addEventListener('resize', apply);
}

/* ---- Init all on DOM ready ---- */
document.addEventListener('DOMContentLoaded', function() {
  initLocalTimes();
  initFloatingTooltips();
  initPredictionAnchorScroll();
  initPredictionScores();
  initMiniInputs();
  initCountdown();
  initOutsiderChips();
  initFlash();
  initTopMatchToggles();
  initPaidToggles();
  initResultForms();
  initAdminTables();
  initAdminPushTarget();
  initAutoRefresh(60);
  initCsvImport();
  initPhaseFilter();
  initStepper('goals-stepper', 50, 300);
  initWinnerFinalistGuard();
  initScorerCombos();
  initBonusForms();
  initPush();
  initMatchLive();
  initCountUp();
  initStickyTop();
});
