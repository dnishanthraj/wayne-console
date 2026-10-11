/* ==========================================================================
   The diary: his days — Bruce Wayne's evenings, Batman's meetings, the plans
   he's made with the family, and anything he writes in himself.

   The days down the side (a few behind, a fortnight ahead), the day he picks
   as a timeline: when, what, where, who's coming and what they said, where it
   came from (a letter, a plan in the family chat) and how it went. From each:
   go there, change it, call it off, or take it out. And a form to write
   something in.
   ========================================================================== */
(function () {
  'use strict';

  var root = null, opts = null, data = { days: [], places: [], today: '' }, picked = null, editing = null, timer = null;

  function $(sel) { return root.querySelector(sel); }
  function esc(t) { return String(t || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function hm(at) { var d = new Date(at * 1000); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); }
  function dayName(iso, long) {
    var d = new Date(iso + 'T12:00:00');
    var label = d.toLocaleDateString(undefined, long ? { weekday: 'long', day: 'numeric', month: 'long' } : { weekday: 'short', day: 'numeric', month: 'short' });
    if (iso === data.today) return long ? 'Today · ' + label : 'Today';
    var t = new Date(data.today + 'T12:00:00');
    var diff = Math.round((d - t) / 86400000);
    if (diff === 1) return long ? 'Tomorrow · ' + label : 'Tomorrow';
    if (diff === -1) return long ? 'Yesterday · ' + label : 'Yesterday';
    return label;
  }

  function load() {
    return fetch('/api/diary').then(function (r) { return r.json(); }).then(function (d) {
      data = d;
      if (!picked) picked = d.today;
      render();
    }).catch(function () {});
  }

  function render() {
    var list = $('.dx-days');
    list.innerHTML = '';
    data.days.forEach(function (day) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'dx-dayname' + (day.date === picked ? ' is-on' : '') + (day.date < data.today ? ' is-past' : '');
      var live = day.entries.filter(function (e) { return e.status === 'accepted'; }).length;
      b.innerHTML = '<span></span><b></b>';
      b.querySelector('span').textContent = dayName(day.date);
      b.querySelector('b').textContent = day.entries.length ? (day.date < data.today ? day.entries.length : (live || '')) : '';
      b.addEventListener('click', function () { picked = day.date; editing = null; render(); });
      list.appendChild(b);
    });
    var day = data.days.filter(function (x) { return x.date === picked; })[0] || data.days[0];
    var pane = $('.dx-day');
    pane.innerHTML = '';
    if (!day) return;
    var head = document.createElement('header');
    head.className = 'dx-day__head';
    head.innerHTML = '<h2></h2>';
    head.querySelector('h2').textContent = dayName(day.date, true);
    pane.appendChild(head);
    if (editing) pane.appendChild(form(editing === 'new' ? null : editing, day.date));
    if (!day.entries.length && !editing) {
      var none = document.createElement('p');
      none.className = 'dx-none';
      none.textContent = day.date < data.today ? 'Nothing in the diary that day.' : 'Nothing in the diary yet.';
      pane.appendChild(none);
    }
    var line = document.createElement('ol');
    line.className = 'dx-line';
    day.entries.slice().sort(function (a, b) { return a.at - b.at; }).forEach(function (e) {
      if (editing && editing !== 'new' && editing.id === e.id) return;
      line.appendChild(card(e));
    });
    pane.appendChild(line);
  }

  /* One entry: when, what, where — who's coming, what it was, how it went — and what he can do about it. */
  function card(e) {
    var li = document.createElement('li');
    li.className = 'dx-entry' + (e.as === 'batman' ? ' is-batman' : '') + ' is-' + e.state.replace(/\s+/g, '-');
    li.innerHTML = '<time></time><div class="dx-entry__body"><p class="dx-entry__top"><b></b><span class="dx-state"></span></p>' +
                   '<p class="dx-entry__where"></p><p class="dx-entry__who"></p><p class="dx-entry__story"></p>' +
                   '<p class="dx-entry__from"></p><div class="dx-entry__acts"></div></div>';
    li.querySelector('time').innerHTML = esc(hm(e.at)) + '<span>' + esc(hm(e.until)) + '</span>';
    li.querySelector('.dx-entry__top b').textContent = (e.as === 'batman' ? 'Batman · ' : '') + e.what;
    li.querySelector('.dx-state').textContent = e.state;
    var where = [e.where, e.dress, e.host && e.source === 'invitation' ? 'hosted by ' + e.host : ''].filter(Boolean).join(' · ');
    var w = li.querySelector('.dx-entry__where');
    if (where) {
      w.textContent = where;
      if (e.venue && opts.showOnMap) {
        var pin = document.createElement('button');
        pin.type = 'button';
        pin.className = 'dx-link';
        pin.textContent = 'On the map';
        pin.addEventListener('click', function () { opts.showOnMap(e.venue[0], e.venue[1]); });
        w.appendChild(pin);
      }
    } else w.remove();
    var who = li.querySelector('.dx-entry__who');
    if (e.who && e.who.length) {
      e.who.forEach(function (p) {
        var chip = document.createElement('span');
        chip.className = 'dx-who dx-who--' + p.said;
        if (p.id && opts.portrait) {                    // one of theirs: a face; anyone else, just the name
          var face = document.createElement('i');
          opts.portrait(face, p.id);
          chip.appendChild(face);
        } else chip.classList.add('dx-who--plain');
        chip.appendChild(document.createTextNode(p.name + (p.said && p.said !== 'yes' ? ' · ' + p.said : '')));
        who.appendChild(chip);
      });
    } else who.remove();
    var story = li.querySelector('.dx-entry__story');
    var said = e.story ? e.story : e.kept_away_by ? 'Out on the ' + e.kept_away_by.toLowerCase() + ' instead.' :
               e.left_for ? 'Left for the ' + e.left_for.toLowerCase() + '.' : e.notes || '';
    if (said) story.textContent = said; else story.remove();
    var from = li.querySelector('.dx-entry__from');
    if (e.origin) {
      from.textContent = 'From ' + e.origin;
      if (e.thread && opts.openThread) from.appendChild(link('Open the letter', function () { opts.openThread(e.thread); }));
      if (e.group && opts.openGroup) from.appendChild(link('Open the chat', function () { opts.openGroup(e.group); }));
    } else from.remove();
    var acts = li.querySelector('.dx-entry__acts');
    if (e.readonly) { acts.remove(); return li; }          // theirs, at his: nothing of his to change
    if (e.status === 'accepted') {
      if (opts.go && e.until * 1000 > Date.now()) acts.appendChild(button('Go', 'dx-go', function (btn) {
        btn.disabled = true;
        opts.go(e.id).then(function () { btn.textContent = 'On the way'; });
      }));
      acts.appendChild(button('Change', '', function () { editing = e; render(); }));
      acts.appendChild(button('Call it off', 'dx-off', function () {
        fetch('/api/diary/' + e.id + '/cancel', { method: 'POST' }).then(load);
      }));
    }
    acts.appendChild(button('Remove', 'dx-del', function () {
      fetch('/api/diary/' + e.id, { method: 'DELETE' }).then(load);
    }));
    return li;
  }

  function link(text, fn) {
    var a = document.createElement('button');
    a.type = 'button';
    a.className = 'dx-link';
    a.textContent = text;
    a.addEventListener('click', fn);
    return a;
  }
  function button(text, cls, fn) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'cx-btn ' + cls;
    b.textContent = text;
    b.addEventListener('click', function () { fn(b); });
    return b;
  }

  /* Writing something in, or changing it: what, where (any place on the map), when, as which man, with whom. */
  function form(e, date) {
    var f = document.createElement('form');
    f.className = 'dx-form';
    var people = opts.people ? opts.people() : [];
    f.innerHTML =
      '<input class="modal__input dx-what" name="what" placeholder="What" maxlength="120" required>' +
      '<input class="modal__input dx-where" name="where" placeholder="Where" list="dx-places" autocomplete="off">' +
      '<datalist id="dx-places"></datalist>' +
      '<div class="dx-form__row"><input class="modal__input" type="date" name="date">' +
      '<input class="modal__input" type="time" name="from"><span>to</span><input class="modal__input" type="time" name="to"></div>' +
      '<div class="dx-form__row dx-as"><label><input type="radio" name="as" value="bruce"><span>Bruce Wayne</span></label>' +
      '<label><input type="radio" name="as" value="batman"><span>Batman</span></label></div>' +
      '<div class="dx-form__people"></div>' +
      '<label class="dx-host"><input type="checkbox" name="host"><span>I\u2019m hosting \u2014 invite them</span></label>' +
      '<input class="modal__input" name="notes" placeholder="Notes" maxlength="300">' +
      '<div class="dx-form__acts"><button type="button" class="cx-btn dx-cancel">Cancel</button>' +
      '<button type="submit" class="cx-btn dx-save">' + (e ? 'Save' : 'Add to the diary') + '</button></div>';
    var dl = f.querySelector('datalist');
    (data.places || []).forEach(function (n) { var o = document.createElement('option'); o.value = n; dl.appendChild(o); });
    f.what.value = e ? e.what : '';
    f.where.value = e ? e.where : '';
    f.date.value = date;
    var at = e ? new Date(e.at * 1000) : null, until = e ? new Date(e.until * 1000) : null;
    f.from.value = at ? hm(e.at) : '19:00';
    f.to.value = until ? hm(e.until) : '21:00';
    f.querySelector('input[name=as][value=' + (e && e.as === 'batman' ? 'batman' : 'bruce') + ']').checked = true;
    var chosen = {};
    (e && e.who || []).forEach(function (p) { if (p.id) chosen[p.id] = true; });
    var box = f.querySelector('.dx-form__people');
    people.forEach(function (p) {
      var chip = document.createElement('label');
      chip.className = 'dx-pick';
      chip.innerHTML = '<input type="checkbox"><span></span>';
      chip.querySelector('input').value = p.id;
      chip.querySelector('input').checked = !!chosen[p.id];
      chip.querySelector('span').textContent = p.name;
      box.appendChild(chip);
    });
    if (e && e.source !== 'manual') box.hidden = true;      // who's coming to someone else's evening isn't his to set
    if (e) f.querySelector('.dx-host').hidden = true;         // invitations go out once, when it's made
    f.querySelector('.dx-cancel').addEventListener('click', function () { editing = null; render(); });
    f.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var body = { what: f.what.value, where: f.where.value, date: f.date.value, from: f.from.value, to: f.to.value,
                   host: !e && f.host.checked,
                   as: (f.querySelector('input[name=as]:checked') || {}).value || 'bruce', notes: f.notes.value,
                   with: Array.prototype.map.call(box.querySelectorAll('input:checked'), function (i) {
                     return (people.filter(function (p) { return p.id === i.value; })[0] || {}).name || i.value; }) };
      fetch('/api/diary' + (e ? '/' + e.id : ''), { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                   body: JSON.stringify(body) })
        .then(function (r) { return r.json(); }).then(function (d) {
          if (d.error) return;
          editing = null;
          picked = f.date.value;
          load();
        }).catch(function () {});
    });
    setTimeout(function () { f.what.focus(); }, 0);
    return f;
  }

  function open(day) {
    if (!root) return;
    if (day) picked = day;
    root.hidden = false;
    if (opts.toggled) opts.toggled(true);
    load();
    clearInterval(timer);
    timer = setInterval(function () { if (!root.hidden && !editing) load(); }, 60000);
  }

  function close() {
    if (!root || root.hidden) return;
    root.hidden = true;
    editing = null;
    clearInterval(timer);
    if (opts.toggled) opts.toggled(false);
  }

  function init(options) {
    opts = options;
    root = options.root;
    $('.dx-close').addEventListener('click', close);
    $('.dx-add').addEventListener('click', function () { editing = 'new'; render(); });
    root.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        e.stopPropagation();
        if (editing) { editing = null; render(); } else close();
      }
    });
  }

  window.Diary = {
    init: init, open: open, close: close, refresh: function () { if (root && !root.hidden && !editing) load(); },
    isOpen: function () { return !!root && !root.hidden; }
  };
})();
