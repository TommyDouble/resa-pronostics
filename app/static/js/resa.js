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
    var qualifierBtns = card.querySelectorAll('.qualifier-btn');
    var errorBox = card.querySelector('.prediction-error');
    var saveIndicator = card.querySelector('.save-badge');
    var saveTimer = null;
    var isKnockout = card.dataset.knockout === '1';

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

    // Active/désactive les inputs score selon la sélection du qualifié (knockout).
    function syncScoreState() {
      if (!isKnockout) return;
      var locked = !selectedQualifier();
      score1.disabled = locked;
      score2.disabled = locked;
    }

    // Retourne true si le score est décisif et incohérent avec le qualifié choisi.
    function isIncoherent(s1, s2, qualifier) {
      if (!isKnockout || s1 === s2 || !qualifier) return false;
      return qualifier !== (s1 > s2 ? 'team1' : 'team2');
    }

    function updateOutcome() {
      var s1 = scoreValue(score1);
      var s2 = scoreValue(score2);
      var qualifier = selectedQualifier();

      if (isKnockout && !qualifier) {
        if (outcome) {
          outcome.textContent = 'Choisis le qualifié';
          outcome.classList.add('empty');
        }
        return null;
      }

      if (s1 === null || s2 === null) {
        if (outcome) {
          outcome.textContent = isKnockout ? 'Score à 90 min ?' : 'Score requis';
          outcome.classList.add('empty');
        }
        return null;
      }

      // Knockout : incohérence score ↔ qualifié
      if (isKnockout && isIncoherent(s1, s2, qualifier)) {
        if (outcome) {
          outcome.textContent = 'À corriger';
          outcome.classList.add('empty');
        }
        var scoreWinner = s1 > s2 ? card.dataset.team1 : card.dataset.team2;
        var qualName = qualifier === 'team1' ? card.dataset.team1 : card.dataset.team2;
        setError(scoreWinner + ' mène à 90 min mais tu as désigné ' + qualName + ' comme équipe qualifiée. Corrige le score ou le qualifié.');
        return null;
      }

      var prediction = derivedPrediction(s1, s2);
      if (outcome) {
        var outcomeText;
        if (isKnockout) {
          var teamName = qualifier === 'team1' ? card.dataset.team1 : card.dataset.team2;
          outcomeText = s1 === s2
            ? 'Nul · Qualifié : ' + teamName + ' (prol./t.a.b.)'
            : 'Qualifié : ' + teamName + ' · ' + s1 + '-' + s2;
        } else {
          outcomeText = prediction.outcome;
        }
        outcome.textContent = outcomeText;
        outcome.classList.remove('empty');
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
      var qualifier = selectedQualifier();

      if (isKnockout && !qualifier) {
        setError("Choisis l'équipe qualifiée.");
        return;
      }
      if (isKnockout && isIncoherent(s1, s2, qualifier)) {
        // Message déjà affiché par updateOutcome.
        return;
      }

      var body = {
        match_id: parseInt(matchId, 10),
        exact_score_team1: s1,
        exact_score_team2: s2
      };
      if (isKnockout) {
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
        syncScoreState();
        updateOutcome();
        if (scoreValue(score1) !== null && scoreValue(score2) !== null) {
          saveScorePrediction();
        }
      });
    });

    syncScoreState();
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
  var triggers = document.querySelectorAll(
    '.help-tip[data-tip], .tip[data-tip], .help-tip[data-tooltip-content], .tip[data-tooltip-content]'
  );
  if (!triggers.length) return;

  document.body.classList.add('tooltip-floating');

  var bubble = document.createElement('div');
  bubble.id = 'floating-tooltip';
  bubble.className = 'floating-tooltip';
  bubble.setAttribute('role', 'tooltip');
  document.body.appendChild(bubble);

  var active = null;

  function renderTooltip(trigger) {
    var sourceId = trigger.getAttribute('data-tooltip-content');
    var source = sourceId ? document.getElementById(sourceId) : null;
    if (source && source.content) {
      bubble.replaceChildren(source.content.cloneNode(true));
      bubble.classList.add('rich');
      return 'rich';
    }

    var text = trigger.getAttribute('data-tip');
    if (!text) return '';
    bubble.textContent = text;
    bubble.classList.remove('rich');
    return 'plain';
  }

  function placeTooltip(trigger) {
    var kind = renderTooltip(trigger);
    if (!kind) return;
    active = trigger;
    bubble.style.maxWidth = Math.min(kind === 'rich' ? 340 : 300, window.innerWidth - 24) + 'px';
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
    trigger.setAttribute('aria-describedby', bubble.id);
    trigger.addEventListener('mouseenter', function() { placeTooltip(trigger); });
    trigger.addEventListener('focus', function() { placeTooltip(trigger); });
    trigger.addEventListener('mouseleave', function() { hideTooltip(trigger); });
    trigger.addEventListener('blur', function() { hideTooltip(trigger); });
    trigger.addEventListener('click', function(e) {
      e.preventDefault();
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
function resaNowSeconds() {
  var root = document.querySelector('[data-client-now-ts]');
  var base = root ? parseInt(root.dataset.clientNowTs, 10) : 0;
  if (!base) return Math.floor(Date.now() / 1000);
  if (typeof window.__resaClockOffset !== 'number') {
    window.__resaClockOffset = base - Math.floor(Date.now() / 1000);
  }
  return Math.floor(Date.now() / 1000) + window.__resaClockOffset;
}

function resaNowDate() {
  return new Date(resaNowSeconds() * 1000);
}

function initCountdown() {
  function pad(n) { return n < 10 ? '0' + n : String(n); }
  document.querySelectorAll('[data-countdown]').forEach(function(el) {
    var target = parseInt(el.dataset.countdown);
    function update() {
      var diff = target - resaNowSeconds();
      if (diff <= 0) { el.textContent = 'Coup d’envoi !'; return; }
      var d = Math.floor(diff / 86400);
      var h = Math.floor((diff % 86400) / 3600);
      var m = Math.floor((diff % 3600) / 60);
      var s = diff % 60;
      if (d > 0) {
        el.textContent = d + 'j ' + h + 'h ' + pad(m) + 'min';
        setTimeout(update, 30000);
      } else if (h > 0) {
        el.textContent = h + 'h ' + pad(m) + 'min';
        setTimeout(update, (s + 1) * 1000);
      } else {
        el.textContent = pad(m) + 'min ' + pad(s) + 's';
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
    if (mode === 'day') {
      // Séparateurs de journées : relatif quand c'est proche, sinon en toutes lettres.
      var startOfDay = function(x) { return new Date(x.getFullYear(), x.getMonth(), x.getDate()); };
      var diff = Math.round((startOfDay(d) - startOfDay(resaNowDate())) / 86400000);
      if (diff === 0) return "Aujourd'hui";
      if (diff === -1) return 'Hier';
      if (diff === 1) return 'Demain';
      var label = new Intl.DateTimeFormat(locale, { weekday: 'long', day: 'numeric', month: 'long' }).format(d);
      return label.charAt(0).toUpperCase() + label.slice(1);
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
      var number = form.querySelector('input[type="number"][name="answer"]');
      if (number && !number.value.trim()) {
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

/* ---- Admin: bonus question builder ---- */
function initAdminBonusQuestionForms() {
  document.querySelectorAll('[data-admin-bonus-form]').forEach(function(form) {
    var typeSelect = form.querySelector('select[name="answer_type"]');
    var presetSelect = form.querySelector('select[name="closest_preset_key"]');
    var preview = form.querySelector('[data-bonus-preview]');

    function currentType() {
      return typeSelect ? typeSelect.value : (form.dataset.bonusInitialType || 'choice');
    }

    function activeField(name) {
      return form.querySelector('[name="' + name + '"]:not(:disabled)') ||
        form.querySelector('[name="' + name + '"]');
    }

    function parseOptions(value) {
      return (value || '')
        .split(/[\n,]/)
        .map(function(option) { return option.trim(); })
        .filter(Boolean);
    }

    function setText(selector, value) {
      if (!preview) return;
      var el = preview.querySelector(selector);
      if (el) el.textContent = value;
    }

    function formatLocalDeadline(value) {
      if (!value) return 'à définir';
      var date = new Date(value);
      if (isNaN(date.getTime())) return 'à définir';
      var pad = function(n) { return String(n).padStart(2, '0'); };
      return pad(date.getDate()) + '/' + pad(date.getMonth() + 1) + '/' +
        date.getFullYear() + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes());
    }

    function previewPointsLabel(type) {
      if (type === 'number') {
        var preset = presetSelect ? presetSelect.value : 'fun_balanced';
        if (preset === 'custom') {
          var rank1 = activeField('closest_rank1_points');
          var customPoints = rank1 ? parseInt(rank1.value, 10) : 6;
          return (isNaN(customPoints) ? 6 : Math.max(customPoints, 0)) + ' pts';
        }
        return '6 pts';
      }
      var pointsField = activeField('points_value');
      var points = pointsField ? parseInt(pointsField.value, 10) : 6;
      return (isNaN(points) ? 6 : Math.max(points, 0)) + ' pts';
    }

    function updatePreviewOptions() {
      if (!preview) return;
      var list = preview.querySelector('[data-preview-choice-list]');
      if (!list) return;
      var optionsField = form.querySelector('textarea[name="options_text"]');
      var options = parseOptions(optionsField ? optionsField.value : '');
      if (!options.length) options = ['Option 1', 'Option 2'];
      list.innerHTML = '';
      options.slice(0, 8).forEach(function(option) {
        var label = document.createElement('label');
        label.style.cssText = 'display:flex;align-items:center;gap:8px;cursor:default;';
        var input = document.createElement('input');
        input.type = 'radio';
        input.disabled = true;
        var span = document.createElement('span');
        span.textContent = option;
        label.appendChild(input);
        label.appendChild(span);
        list.appendChild(label);
      });
    }

    function updatePreview() {
      if (!preview) return;
      var type = currentType();
      var question = form.querySelector('input[name="question_text"]');
      var phase = form.querySelector('select[name="phase"]');
      var deadline = form.querySelector('input[name="deadline"]');
      var status = preview.querySelector('[data-preview-status]');
      var answerArea = preview.querySelector('[data-preview-answer-area]');
      var locked = preview.querySelector('[data-preview-locked]');
      var choice = preview.querySelector('[data-preview-choice]');
      var number = preview.querySelector('[data-preview-number]');
      var deadlineValue = deadline ? deadline.value : '';
      var deadlineDate = deadlineValue ? new Date(deadlineValue) : null;
      var isOpen = !deadlineDate || isNaN(deadlineDate.getTime()) || deadlineDate.getTime() > Date.now();

      setText('[data-preview-title]', (question && question.value.trim()) || 'Intitulé de la question');
      setText('[data-preview-phase]', phase && phase.selectedOptions.length ? phase.selectedOptions[0].textContent : 'Pré-tournoi');
      setText('[data-preview-points]', previewPointsLabel(type));
      setText('[data-preview-deadline]', formatLocalDeadline(deadlineValue));

      if (status) {
        status.textContent = isOpen ? 'À répondre' : 'Non répondue';
        status.classList.remove('warn', 'lock', 'ok', 'gr');
        status.classList.add(isOpen ? 'warn' : 'lock');
      }
      if (answerArea) answerArea.style.display = isOpen ? '' : 'none';
      if (locked) locked.style.display = isOpen ? 'none' : '';
      if (choice) choice.style.display = type === 'choice' ? '' : 'none';
      if (number) number.style.display = type === 'number' ? '' : 'none';
      updatePreviewOptions();
    }

    function setSection(selector, visible) {
      form.querySelectorAll(selector).forEach(function(section) {
        section.style.display = visible ? '' : 'none';
        section.querySelectorAll('input, select, textarea, button').forEach(function(control) {
          control.disabled = !visible;
        });
      });
    }

    function update() {
      var isNumber = currentType() === 'number';
      var isCustom = isNumber && presetSelect && presetSelect.value === 'custom';
      setSection('[data-bonus-choice-only]', !isNumber);
      setSection('[data-bonus-number-only]', isNumber);
      setSection('[data-bonus-custom-only]', isCustom);
      updatePreview();
    }

    if (typeSelect) typeSelect.addEventListener('change', update);
    if (presetSelect) presetSelect.addEventListener('change', update);
    form.querySelectorAll('input, select, textarea').forEach(function(control) {
      control.addEventListener('input', updatePreview);
      control.addEventListener('change', updatePreview);
    });
    update();
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

/* ---- Admin: toggle favori (départage des ex æquo) sans recharger ---- */
function initFavoriteToggles() {
  document.querySelectorAll('[data-toggle-favorite]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      btn.disabled = true;
      fetch('/admin/participants/' + btn.dataset.toggleFavorite + '/toggle-favorite', { method: 'POST' })
        .then(function(r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(function(data) {
          var on = !!data.is_favorite;
          btn.textContent = on ? '★' : '☆';
          btn.classList.toggle('on', on);
          var cell = btn.closest('td');
          if (cell) cell.setAttribute('data-sort-value', on ? '1' : '0');
        })
        .catch(function() {
          alert('Mise à jour du favori impossible, réessaie.');
        })
        .then(function() { btn.disabled = false; });
    });
  });
}

/* ---- Admin: score validation warning ---- */
function initResultForms() {
  document.querySelectorAll('.result-form').forEach(function(form) {
    var score1 = form.querySelector('[name="score_team1"]');
    var score2 = form.querySelector('[name="score_team2"]');
    var final1 = form.querySelector('[name="final_score_team1"]');
    var final2 = form.querySelector('[name="final_score_team2"]');
    var finalFields = form.querySelector('[data-ko-final-fields]');
    var qualifier = form.querySelector('select[name="qualifier_winner"]');
    var phase = form.dataset.phase;
    var isKnockout = phase && phase !== 'group';

    function scoreValue(input) {
      if (!input || input.value === '') return null;
      var value = parseInt(input.value, 10);
      return isNaN(value) ? null : value;
    }

    function setQualifierFromFinal() {
      if (!isKnockout || !qualifier) return;
      var f1 = scoreValue(final1);
      var f2 = scoreValue(final2);
      if (f1 === null || f2 === null || f1 === f2) {
        qualifier.disabled = false;
        return;
      }
      qualifier.value = f1 > f2 ? 'team1' : 'team2';
      qualifier.disabled = true;
    }

    function updateFinalFields() {
      if (!finalFields) return;
      var s1 = scoreValue(score1);
      var s2 = scoreValue(score2);
      var open = isKnockout && s1 !== null && s2 !== null && s1 === s2;
      finalFields.classList.toggle('is-open', open);
      if (!open) {
        if (final1) final1.value = '';
        if (final2) final2.value = '';
        if (qualifier) qualifier.disabled = false;
      }
      setQualifierFromFinal();
    }

    [score1, score2, final1, final2].forEach(function(input) {
      if (!input) return;
      input.addEventListener('input', updateFinalFields);
      input.addEventListener('change', updateFinalFields);
    });
    updateFinalFields();

    form.addEventListener('submit', function(e) {
      if (qualifier && qualifier.disabled) qualifier.disabled = false;
      var s1 = scoreValue(score1);
      var s2 = scoreValue(score2);
      var f1 = scoreValue(final1);
      var f2 = scoreValue(final2);
      if (isKnockout && s1 === s2) {
        if ((f1 !== null && f1 < s1) || (f2 !== null && f2 < s2)) {
          alert("Le score final ne peut pas être inférieur au score à 90 minutes.");
          e.preventDefault();
          return;
        }
        if ((f1 === null || f2 === null || f1 === f2) && qualifier && !qualifier.value) {
          alert("Choisis l'équipe qualifiée pour ce match de phase finale.");
          e.preventDefault();
          return;
        }
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

/* ---- Admin: rafraîchissement discret du tableau de bord ----
   Recharge le HTML de la page en arrière-plan et remplace les sections
   marquées data-dash-swap, sans reload ni perte de contexte. */
function initDashboardRefresh(seconds) {
  if (!document.querySelector('[data-dash-swap]')) return;
  function busy() {
    // Ne pas toucher au DOM pendant une saisie ou un panneau déplié.
    var el = document.activeElement;
    if (el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) return true;
    return !!document.querySelector('details[open]');
  }
  function tick() {
    if (busy()) {
      setTimeout(tick, seconds * 1000);
      return;
    }
    fetch(window.location.href, { headers: { 'Accept': 'text/html' } })
      .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      })
      .then(function(html) {
        var doc = new DOMParser().parseFromString(html, 'text/html');
        document.querySelectorAll('[data-dash-swap]').forEach(function(section) {
          var fresh = doc.querySelector('[data-dash-swap="' + section.dataset.dashSwap + '"]');
          if (fresh) section.innerHTML = fresh.innerHTML;
        });
        // Les nœuds remplacés perdent leurs handlers : on les rebranche.
        initLocalTimes();
        initResultForms();
      })
      .catch(function() {})
      .then(function() { setTimeout(tick, seconds * 1000); });
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

/* ---- Cartes compactes des matchs des jours passés ---- */
function initCompactCards() {
  document.querySelectorAll('[data-compact-toggle]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var card = btn.closest('.prediction-card');
      if (!card) return;
      var open = card.classList.toggle('open');
      card.querySelectorAll('[data-compact-toggle]').forEach(function(toggle) {
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
    });
  });
}

/* ---- Carrousel de trophées (encart supérieur du classement général) ---- */
function initTrophyCarousel() {
  var el = document.querySelector('[data-trophy-carousel]');
  if (!el) return;
  var track = el.querySelector('.tc-track');
  var slides = Array.from(track.querySelectorAll('.tc-slide'));
  var dots = Array.from(el.querySelectorAll('.tc-dot'));

  el.querySelectorAll('[data-tc-overflow]').forEach(function(btn) {
    btn.dataset.originalText = btn.textContent.trim();
    btn.textContent = btn.dataset.originalText;
    btn.setAttribute('aria-expanded', 'false');
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var card = btn.closest('.tc-card') || btn.closest('.tc-slide');
      var overflow = card ? card.querySelector('.tc-overflow') : null;
      if (overflow) {
        overflow.hidden = !overflow.hidden;
        var expanded = !overflow.hidden;
        btn.textContent = expanded ? 'Voir moins' : btn.dataset.originalText;
        btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        if (slides.length > 1) pauseAndResume();
      }
    });
  });

  if (slides.length < 2) return;

  var current = 0;
  var timer = null;
  var resumeTimer = null;
  var INTERVAL = 5000;
  var RESUME_DELAY = 8000;
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function syncDots(idx) {
    dots.forEach(function(d, j) {
      var active = j === idx;
      d.classList.toggle('active', active);
      d.setAttribute('aria-selected', active ? 'true' : 'false');
    });
  }

  function goTo(idx) {
    current = idx;
    track.scrollTo({ left: slides[idx].offsetLeft, behavior: reduced ? 'auto' : 'smooth' });
    syncDots(idx);
  }

  function next() { goTo((current + 1) % slides.length); }

  function startAuto() {
    if (reduced) return;
    stopAuto();
    timer = setInterval(next, INTERVAL);
  }

  function stopAuto() {
    if (timer) { clearInterval(timer); timer = null; }
    if (resumeTimer) { clearTimeout(resumeTimer); resumeTimer = null; }
  }

  function pauseAndResume() {
    stopAuto();
    resumeTimer = setTimeout(startAuto, RESUME_DELAY);
  }

  var observer = new IntersectionObserver(function(entries) {
    entries.forEach(function(e) {
      if (e.isIntersecting) {
        var idx = slides.indexOf(e.target);
        if (idx >= 0) { current = idx; syncDots(idx); }
      }
    });
  }, { root: track, threshold: 0.5 });
  slides.forEach(function(s) { observer.observe(s); });

  dots.forEach(function(d, i) {
    d.addEventListener('click', function() { goTo(i); pauseAndResume(); });
  });

  track.addEventListener('pointerdown', function() { stopAuto(); });
  track.addEventListener('pointerup', function() {
    resumeTimer = setTimeout(startAuto, RESUME_DELAY);
  });

  startAuto();
}

/* ---- Classement des départements : un seul détail ouvert à la fois ---- */
function initDepartmentRanking() {
  var details = Array.from(document.querySelectorAll('[data-department-detail]'));
  if (!details.length) return;

  function updateSummaryLabel(detail) {
    var summary = detail.querySelector('summary');
    if (!summary) return;
    var label = summary.getAttribute('aria-label') || '';
    summary.setAttribute(
      'aria-label',
      label.replace(/ouvrir|fermer/, detail.open ? 'fermer' : 'ouvrir')
    );
  }

  function syncUrl() {
    if (!window.history || !window.history.replaceState) return;
    var url = new URL(window.location.href);
    var openDetail = details.find(function(item) { return item.open; });
    if (!openDetail) {
      url.searchParams.delete('department');
      url.searchParams.delete('members');
    } else {
      url.searchParams.set('department', openDetail.dataset.departmentName || '');
      if (openDetail.dataset.membersExpanded === '1') {
        url.searchParams.set('members', 'all');
      } else {
        url.searchParams.delete('members');
      }
    }
    window.history.replaceState({}, '', url.pathname + url.search);
  }

  details.forEach(function(detail) {
    updateSummaryLabel(detail);
    var summary = detail.querySelector('summary');
    if (summary) {
      summary.addEventListener('keydown', function(event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        detail.open = !detail.open;
      });
    }
    detail.addEventListener('toggle', function() {
      if (detail.open) {
        details.forEach(function(other) {
          if (other !== detail && other.open) other.open = false;
        });
      }
      updateSummaryLabel(detail);
      syncUrl();
    });

    var showAll = detail.querySelector('[data-department-show-all]');
    if (showAll) {
      showAll.addEventListener('click', function() {
        detail.querySelectorAll('[data-department-member-hidden]').forEach(function(row) {
          row.hidden = false;
          row.removeAttribute('data-department-member-hidden');
        });
        detail.querySelectorAll('.department-member[href]').forEach(function(link) {
          var profileUrl = new URL(link.href, window.location.origin);
          profileUrl.searchParams.set('return_members', 'all');
          link.href = profileUrl.pathname + profileUrl.search;
        });
        detail.dataset.membersExpanded = '1';
        showAll.hidden = true;
        syncUrl();
      });
    }
  });

  var initialOpen = details.find(function(item) { return item.open; });
  if (initialOpen) {
    window.requestAnimationFrame(function() {
      initialOpen.querySelector('summary').scrollIntoView({ block: 'center', inline: 'nearest' });
    });
  }
}

/* Le filtre actif d'une rangée scrollable doit toujours rester visible. */
function initRankingFilters() {
  var filters = document.querySelector('[data-ranking-filters]');
  if (!filters) return;
  var active = filters.querySelector('[data-ranking-filter-active]');
  if (!active) return;
  var left = active.offsetLeft;
  var right = left + active.offsetWidth;
  if (left < filters.scrollLeft || right > filters.scrollLeft + filters.clientWidth) {
    active.scrollIntoView({ block: 'nearest', inline: 'center' });
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

/* ---- Story des nouveautés ---- */
function initStoryPlayer() {
  var root = document.querySelector('[data-story]');
  if (!root) return;
  var features = Array.prototype.slice.call(root.querySelectorAll('[data-story-feature]'));
  if (!features.length) return;
  var screensOf = features.map(function(f) {
    return Array.prototype.slice.call(f.querySelectorAll('[data-story-screen]'));
  });
  var token = document.body.dataset.token;
  var maxId = parseInt(root.dataset.storyMaxid, 10) || 0;
  var fi = 0;   // index de la fonctionnalité courante
  var si = 0;   // index de l'écran courant dans la fonctionnalité
  var seenSent = false;

  // Barre du haut : segments de progression + (titre · compteur 1/N) + fermer.
  var topbar = document.createElement('div');
  topbar.className = 'story-topbar';
  var prog = document.createElement('div');
  prog.className = 'story-progress';
  var titleRow = document.createElement('div');
  titleRow.className = 'story-titlerow';
  var titleEl = document.createElement('span');
  titleEl.className = 'story-title-top';
  var counterEl = document.createElement('span');
  counterEl.className = 'story-counter';
  titleRow.appendChild(titleEl);
  titleRow.appendChild(counterEl);
  topbar.appendChild(prog);
  topbar.appendChild(titleRow);
  root.insertBefore(topbar, root.firstChild);

  var closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'story-close';
  closeBtn.setAttribute('aria-label', 'Fermer les nouveautés');
  closeBtn.textContent = '✕';
  root.insertBefore(closeBtn, root.firstChild);

  var navPrev = document.createElement('button');
  navPrev.type = 'button';
  navPrev.className = 'story-nav prev';
  navPrev.setAttribute('aria-label', 'Précédent');
  var navNext = document.createElement('button');
  navNext.type = 'button';
  navNext.className = 'story-nav next';
  navNext.setAttribute('aria-label', 'Suivant');
  root.appendChild(navPrev);
  root.appendChild(navNext);

  function render() {
    features.forEach(function(f, i) { f.classList.toggle('is-active', i === fi); });
    var screens = screensOf[fi];
    screens.forEach(function(s, i) { s.classList.toggle('is-active', i === si); });
    // Segments = nombre d'écrans de la fonctionnalité courante (réinitialisés).
    if (prog.children.length !== screens.length) {
      prog.innerHTML = '';
      for (var k = 0; k < screens.length; k++) {
        var seg = document.createElement('span');
        seg.className = 'story-seg';
        prog.appendChild(seg);
      }
    }
    for (var i = 0; i < prog.children.length; i++) {
      prog.children[i].classList.toggle('done', i < si);
      prog.children[i].classList.toggle('current', i === si);
    }
    titleEl.textContent = features[fi].dataset.title || '';
    counterEl.textContent = (si + 1) + '/' + screens.length;
  }
  function open() {
    fi = 0; si = 0;
    root.classList.add('open');
    document.body.classList.add('story-locked');
    render();
  }
  function markSeen() {
    if (seenSent || !token || !maxId) return;
    seenSent = true;
    fetch('/api/news/seen?token=' + encodeURIComponent(token), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: maxId })
    }).catch(function() {});
  }
  function close() {
    root.classList.remove('open');
    document.body.classList.remove('story-locked');
    markSeen();  // vu OU passé : dans les deux cas on ne le remontre pas.
  }
  function next() {
    if (si < screensOf[fi].length - 1) { si++; }
    else if (fi < features.length - 1) { fi++; si = 0; }
    else { close(); return; }
    render();
  }
  function prev() {
    if (si > 0) { si--; }
    else if (fi > 0) { fi--; si = screensOf[fi].length - 1; }
    else { return; }
    render();
  }

  navNext.addEventListener('click', next);
  navPrev.addEventListener('click', prev);
  closeBtn.addEventListener('click', close);
  document.addEventListener('keydown', function(e) {
    if (!root.classList.contains('open')) return;
    if (e.key === 'ArrowRight') next();
    else if (e.key === 'ArrowLeft') prev();
    else if (e.key === 'Escape') close();
  });

  // Auto-ouverture seulement si aucune action prioritaire ne l'exige.
  if (root.dataset.storyAutoopen === '1') {
    open();
  } else {
    var chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'story-chip';
    chip.textContent = '✨ Nouveautés';
    chip.addEventListener('click', open);
    var anchor = document.querySelector('.page-content') || document.body;
    anchor.insertBefore(chip, anchor.firstChild);
  }
  render();
}

/* ---- Confetti (canvas, sans dépendance) ---- */
function resaConfetti(opts) {
  opts = opts || {};
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var count = opts.count || 90;
  var canvas = document.createElement('canvas');
  canvas.className = 'resa-confetti';
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
  document.body.appendChild(canvas);
  var ctx = canvas.getContext('2d');
  var colors = ['#D3450D', '#F59E0B', '#2E7D32', '#A33308', '#FBBF24'];
  var parts = [];
  for (var i = 0; i < count; i++) {
    parts.push({
      x: canvas.width / 2 + (Math.random() - 0.5) * 140,
      y: canvas.height / 3,
      vx: (Math.random() - 0.5) * 11,
      vy: Math.random() * -12 - 4,
      size: Math.random() * 7 + 4,
      color: colors[(Math.random() * colors.length) | 0],
      rot: Math.random() * Math.PI,
      vr: (Math.random() - 0.5) * 0.3
    });
  }
  var start = null;
  var duration = 1500;
  function frame(ts) {
    if (start === null) start = ts;
    var elapsed = ts - start;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    parts.forEach(function(p) {
      p.vy += 0.35;  // gravité
      p.x += p.vx; p.y += p.vy; p.rot += p.vr;
      ctx.save();
      ctx.globalAlpha = Math.max(0, 1 - elapsed / duration);
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.fillStyle = p.color;
      ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * 0.6);
      ctx.restore();
    });
    if (elapsed < duration) requestAnimationFrame(frame);
    else if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
  }
  requestAnimationFrame(frame);
}
window.resaConfetti = resaConfetti;

/* ---- Reveal du jour v2 : parcours choreographié (matchs -> classement -> CTA) ---- */
function initReveal() {
  var root = document.querySelector('[data-reveal]');
  if (!root) return;
  var stages = Array.prototype.slice.call(root.querySelectorAll('[data-reveal-stage]'));
  if (!stages.length) return;
  var token = document.body.dataset.token;
  var revealDay = root.dataset.revealDay || '';
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var idx = -1;
  var timers = [];
  var seenSent = false;

  function clearTimers() { timers.forEach(clearTimeout); timers = []; }
  function after(ms, fn) { timers.push(setTimeout(fn, ms)); }

  function markSeen() {
    if (seenSent || !token) return;
    seenSent = true;
    fetch('/api/reveal/seen?token=' + encodeURIComponent(token) + '&sporting_day=' + encodeURIComponent(revealDay), { method: 'POST' }).catch(function() {});
  }

  // Barre de progression (un segment par étape).
  var prog = document.createElement('div');
  prog.className = 'rv-progress';
  stages.forEach(function() {
    var s = document.createElement('span');
    s.className = 'rv-seg';
    prog.appendChild(s);
  });
  root.appendChild(prog);
  function updateProgress() {
    for (var i = 0; i < prog.children.length; i++) {
      prog.children[i].classList.toggle('done', i < idx);
      prog.children[i].classList.toggle('current', i === idx);
    }
  }

  function advance() { if (idx < stages.length - 1) enter(idx + 1); }

  // Verdict en deux temps : le point (info reine) "tombe" après le résultat.
  // Confetti calé sur l'apparition du point (pas du score), une seule fois.
  function revealPoints(stage) {
    stage.classList.add('show-points');
    if (stage.dataset.exact === '1' && !stage.dataset.celebrated && window.resaConfetti) {
      stage.dataset.celebrated = '1';
      window.resaConfetti({ count: 90 });
    }
  }

  function enterMatch(stage) {
    stage.classList.remove('show-result', 'show-points');
    if (reduce) { stage.classList.add('show-result', 'show-points'); return; }
    // 1) Court temps de lecture du prono (~1s), puis le résultat monte.
    after(1000, function() {
      stage.classList.add('show-result');
      // 2) Le point tombe ~350ms plus tard. Pas d'auto-avance : on attend le tap.
      after(350, function() { revealPoints(stage); });
    });
  }

  function setRankText(rk, val) {
    rk.textContent = (val === +rk.dataset.to && val === 1) ? '🥇' : val;
  }

  function animateRanks(rows, dur) {
    var start = null;
    function frame(ts) {
      if (start === null) start = ts;
      var p = Math.min((ts - start) / dur, 1);
      var e = 1 - Math.pow(1 - p, 3);  // ease-out
      rows.forEach(function(r) {
        var rk = r.querySelector('.rk');
        var from = +rk.dataset.from, to = +rk.dataset.to;
        setRankText(rk, Math.round(from + (to - from) * e));
        var sc = r.querySelector('.sc');
        var ptsEl = r.querySelector('.pts-val');
        if (sc && ptsEl) {
          var fp = +sc.dataset.fromPts, tp = +sc.dataset.toPts;
          ptsEl.textContent = Math.round(fp + (tp - fp) * e);
        }
      });
      if (p < 1) requestAnimationFrame(frame);
      else rows.forEach(function(r) {  // micro-pulse du rang à l'arrivée
        var rk = r.querySelector('.rk');
        if (rk) { rk.classList.remove('pulse'); void rk.offsetWidth; rk.classList.add('pulse'); }
      });
    }
    requestAnimationFrame(frame);
  }

  // Animation ascenseur : MOI centré fixe, les slots voisins glissent via CSS.
  function finishClimb(stage) {
    var climb = stage.querySelector('[data-rv-climb]');
    if (climb) {
      // Supprime les transitions pour un saut instantané
      Array.prototype.forEach.call(climb.querySelectorAll('.rv-face'), function(f) {
        f.style.transition = 'none';
      });
      void climb.offsetHeight;
      climb.classList.add('animating');
    }
    var meRow = climb ? climb.querySelector('.rv-crow.me') : null;
    if (meRow) {
      meRow.style.animation = 'none';  // coupe rv-rev / rv-lift / rv-drop en cours
      var rk = meRow.querySelector('.rk');
      if (rk) setRankText(rk, +rk.dataset.to);
      var sc = meRow.querySelector('.sc');
      var ptsEl = meRow.querySelector('.pts-val');
      if (sc && ptsEl) ptsEl.textContent = +sc.dataset.toPts;
    }
    stage.classList.remove('revving', 'lifting', 'dropping');
    stage.classList.add('climbing', 'rv-done');  // garde le halo sur la position d'arrivée
  }

  function playClimb(stage) {
    var climb = stage.querySelector('[data-rv-climb]');
    var meRow = climb ? climb.querySelector('.rv-crow.me') : null;
    if (!climb || !meRow || reduce) {
      finishClimb(stage);
      if (!reduce) after(2600, advance);
      return;
    }
    var down = (+stage.dataset.delta) < 0;
    var moveDur = down ? 1000 : 900;
    // 1) Vibration de décollage (le halo s'allume, MOI monte en puissance / se crispe).
    stage.classList.add('climbing', 'revving');
    after(360, function() {
      // 2) Tout démarre ensemble : voisins, count-up et poussée/chute → fins alignées.
      stage.classList.remove('revving');
      stage.classList.add(down ? 'dropping' : 'lifting');
      climb.classList.add('animating');
      animateRanks([meRow], 900);
      after(moveDur + 60, function() { stage.classList.remove('lifting', 'dropping'); stage.classList.add('rv-done'); });
      after(moveDur + 900, advance);
    });
  }

  function enterRank(stage) {
    if (stage.dataset.moved !== '1') {  // pas de mouvement : extrait statique
      stage.classList.add('rv-done');
      if (!reduce) after(2600, advance);
      return;
    }
    playClimb(stage);
  }

  function enter(i) {
    clearTimers();
    idx = i;
    stages.forEach(function(s, k) { s.classList.toggle('is-active', k === i); });
    updateProgress();
    var stage = stages[i];
    if (stage.hasAttribute('data-reveal-match')) enterMatch(stage);
    else if (stage.hasAttribute('data-reveal-rank')) enterRank(stage);
    else if (stage.hasAttribute('data-reveal-final')) markSeen();
    // intro : on attend le tap.
  }

  // Tap : accélère la phase en cours, sinon avance.
  function onTap() {
    var stage = stages[idx];
    if (!stage || stage.hasAttribute('data-reveal-final')) return;  // CTA : liens cliquables
    if (stage.hasAttribute('data-reveal-match')) {
      clearTimers();
      if (!stage.classList.contains('show-result')) {       // 1er tap : révèle le résultat
        stage.classList.add('show-result');
        after(280, function() { revealPoints(stage); });
        return;
      }
      if (!stage.classList.contains('show-points')) {        // 2e tap : fait tomber le point
        revealPoints(stage);
        return;
      }
      advance();                                             // 3e tap : match suivant
      return;
    }
    if (stage.hasAttribute('data-reveal-rank') && !stage.classList.contains('rv-done')) {
      clearTimers();
      finishClimb(stage);  // saute directement aux positions finales
      return;
    }
    clearTimers();
    advance();
  }

  root.addEventListener('click', function(e) {
    if (e.target.closest('a, button')) return;  // ne pas voler les clics des CTA
    onTap();
  });
  document.addEventListener('keydown', function(e) {
    if (e.key === 'ArrowRight' || e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onTap(); }
  });

  enter(0);
}

function initConfettiTriggers() {
  var els = document.querySelectorAll('[data-confetti]');
  if (!els.length) return;
  if (!('IntersectionObserver' in window)) { resaConfetti(); return; }
  var obs = new IntersectionObserver(function(entries) {
    entries.forEach(function(e) {
      if (!e.isIntersecting) return;
      obs.unobserve(e.target);
      resaConfetti();
    });
  }, { threshold: 0.6 });
  els.forEach(function(el) { obs.observe(el); });
}

/* Médailles : reflet one-shot au scroll-in + rejouable au tap (pulse/burst via .go).
   Le flottement est géré en CSS (animation continue) ; ici on ne déclenche que le
   reflet et l'éclat. prefers-reduced-motion => on ne fait rien. */
function initTrophyMedals() {
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var medals = document.querySelectorAll('[data-cabinet] .trophy.on');
  if (!medals.length) return;
  function play(t) { t.classList.remove('go'); void t.offsetWidth; t.classList.add('go'); }
  medals.forEach(function(t) {
    t.addEventListener('pointerdown', function() { play(t); });
  });
  if (!('IntersectionObserver' in window)) return;
  var obs = new IntersectionObserver(function(entries) {
    entries.forEach(function(e) {
      if (!e.isIntersecting) return;
      obs.unobserve(e.target);
      play(e.target);
    });
  }, { threshold: 0.45 });
  medals.forEach(function(t) { obs.observe(t); });
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
  initFavoriteToggles();
  initResultForms();
  initAdminTables();
  initAdminPushTarget();
  initDashboardRefresh(60);
  initCsvImport();
  initPhaseFilter();
  initStepper('goals-stepper', 50, 300);
  initWinnerFinalistGuard();
  initScorerCombos();
  initBonusForms();
  initAdminBonusQuestionForms();
  initPush();
  initMatchLive();
  initCountUp();
  initStickyTop();
  initCompactCards();
  initTrophyCarousel();
  initDepartmentRanking();
  initRankingFilters();
  initStoryPlayer();
  initConfettiTriggers();
  initTrophyMedals();
  initReveal();
});
