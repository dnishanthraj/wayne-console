/* ==========================================================================
   Audio playback.

   Clips arrive one sentence at a time and must play gapless and in order, so
   they queue here rather than firing as they land. Everything routes through a
   single AnalyserNode, which is what lets the visualizer read the real
   spectrum of the voice instead of animating a guess.

   `onSentenceStart` fires the moment a clip begins, with its duration — that
   is the hook the transcript uses to reveal words in time with the speech
   rather than dumping the whole reply on screen before it is spoken.
   ========================================================================== */

(function (global) {
  'use strict';

  var ctx = null;
  var analyser = null;
  var queue = [];
  var current = null;
  var playing = false;
  // The sentence playing now — what, whose, and when it began — for remaining().
  var currentItem = null;
  // Bumped by stop(). A clip still loading when he was cut off belongs to the
  // old generation and must never start once it lands.
  var generation = 0;

  var handlers = {
    onSentenceStart: null,   // (text, key, durationMs, words, speaker)
    onIdle: null,
    onBusy: null
  };

  function context() {
    if (ctx) return ctx;
    var Ctx = global.AudioContext || global.webkitAudioContext;
    ctx = new Ctx();
    analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    analyser.smoothingTimeConstant = 0.75;
    analyser.connect(ctx.destination);
    return ctx;
  }

  function resume() {
    var c = context();
    return c.state === 'suspended' ? c.resume() : Promise.resolve();
  }

  /* Fetched and decoded the moment it is queued, not when its turn comes.
     Waiting until the clip before it had ended put a network round trip and a
     decode into the gap between every pair of sentences. */
  function load(clipId) {
    return fetch('/api/audio/' + clipId)
      .then(function (r) { return r.arrayBuffer(); })
      .then(function (buf) { return context().decodeAudioData(buf); });
  }

  /* Someone in the room with whoever he called, heard through their phone on
     speaker: the phone's band, a little of the room, quieter than the one
     holding it. */
  var distant = {}, room = null;
  function impulse(c, seconds) {
    var n = Math.floor(c.sampleRate * seconds), buf = c.createBuffer(2, n, c.sampleRate);
    for (var ch = 0; ch < 2; ch++) {
      var d = buf.getChannelData(ch);
      for (var i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, 3);
    }
    return buf;
  }
  function roomChain() {
    if (room) return room;
    var c = context();
    var input = c.createGain();
    var low = c.createBiquadFilter(); low.type = 'highpass'; low.frequency.value = 280;
    var high = c.createBiquadFilter(); high.type = 'lowpass'; high.frequency.value = 2700;
    var dry = c.createGain(); dry.gain.value = 0.5;
    var wet = c.createGain(); wet.gain.value = 0.24;
    var verb = c.createConvolver(); verb.buffer = impulse(c, 0.45);
    input.connect(low); low.connect(high); high.connect(dry); high.connect(verb); verb.connect(wet);
    dry.connect(analyser); wet.connect(analyser);
    room = input;
    return room;
  }
  function setDistant(ids) {
    distant = {};
    (ids || []).forEach(function (id) { distant[id] = true; });
  }

  function enqueue(clipId, text, key, words, speaker) {
    var decoded = load(clipId);
    decoded.catch(function () { /* handled when its turn comes */ });
    queue.push({ decoded: decoded, text: text, key: key, words: words || [],
                 speaker: speaker, generation: generation });
    if (!playing) next();
  }

  function next() {
    if (!queue.length) {
      playing = false;
      current = null;
      currentItem = null;
      if (handlers.onIdle) handlers.onIdle();
      return;
    }
    if (!playing && handlers.onBusy) handlers.onBusy();
    playing = true;

    var item = queue.shift();

    item.decoded
      .then(function (decoded) {
        // Mid-exertion — the bag, the mat, a landing — their own room can cut in between two sentences.
        return cutIn(item.speaker).then(function () { return decoded; });
      })
      .then(function (decoded) {
        if (item.generation !== generation) return;   // stopped while loading
        var source = ctx.createBufferSource();
        source.buffer = decoded;
        source.connect(distant[item.speaker] ? roomChain() : analyser);
        source.onended = function () {
          lastLine[item.speaker || ''] = Date.now();
          if (current === source) { current = null; currentItem = null; next(); }
        };
        current = source;
        currentItem = { text: item.text, speaker: item.speaker, at: ctx.currentTime, duration: decoded.duration };
        if (handlers.onSentenceStart) {
          handlers.onSentenceStart(item.text, item.key, decoded.duration * 1000, item.words,
                                   item.speaker);
        }
        source.start();
      })
      .catch(function () {
        // A clip that won't decode shouldn't strand the rest of the reply.
        if (item.generation === generation) next();
      });
  }

  function stop() {
    generation += 1;
    queue.length = 0;
    if (current) {
      var source = current;
      current = null;
      try { source.onended = null; source.stop(); } catch (e) { /* already ended */ }
    }
    currentItem = null;
    playing = false;
    if (handlers.onIdle) handlers.onIdle();
  }

  /* --- the room behind the voice --------------------------------------------
     Where each of them is, under the call, all the time: the loops of the
     place (a pavement's traffic and its crowd, rain over it if it's raining)
     and the sounds that happen there — a horn beside them, a siren going past,
     a train far off — when the server says, so what they hear is what he hears
     (see wayne.audio.ambience). Louder while that one talks; on a group call,
     still there behind the others, quieter. Each bed has its own analyser, so
     a person's ring moves with a horn behind them; and a muted line carries no
     room at all. Straight to the speakers, never into the voice's analyser. */
  var beds = {};
  // Under the voice, not beside it: the room was loud enough that a horn behind Dick read as part of the call.
  var BED_IDLE = 0.045, BED_TALKING = 0.075, BED_BEHIND = 0.03;   // × how loud the place is
  var lastLine = {};

  var buffers = {};
  function buffer(name) {
    if (!buffers[name]) {
      buffers[name] = fetch('/api/ambience/file/' + encodeURIComponent(name))
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.arrayBuffer(); })
        .then(function (buf) { return context().decodeAudioData(buf); });
      buffers[name].catch(function () { delete buffers[name]; });
    }
    return buffers[name];
  }

  function makeEntry(spec) {
    var c = context();
    var entry = { key: bedKey(spec), mix: c.createGain(), gate: c.createGain(), gain: c.createGain(),
                  analyser: c.createAnalyser(), sources: [], cueTimers: {}, cuts: [], level: 1,
                  gated: false, talking: false, muted: false };
    entry.analyser.fftSize = 1024;
    entry.analyser.smoothingTimeConstant = 0.6;
    entry.gain.gain.value = 0;
    entry.mix.connect(entry.gate);
    entry.gate.connect(entry.analyser);          // what the ring reads: the room as it is, whatever the volume
    entry.gate.connect(entry.gain);
    entry.gain.connect(c.destination);
    (spec.beds || []).forEach(function (layer) {
      buffer(layer.name).then(function (decoded) {
        if (entry.dead) return;
        var source = c.createBufferSource(), g = c.createGain();
        source.buffer = decoded;
        source.loop = true;
        g.gain.value = layer.gain;
        source.connect(g).connect(entry.mix);
        // Started somewhere in the loop, so two people in the same kind of place aren't in step.
        source.start(0, Math.random() * Math.max(0.1, decoded.duration - 0.1));
        entry.sources.push(source);
      }).catch(function () { /* that layer isn't made: the rest still play */ });
    });
    return entry;
  }

  var soundingUntil = 0;           // a near sound of someone's room is playing until then

  function bedKey(spec) { return (spec.beds || []).map(function (b) { return b.name + ':' + b.gain; }).join('|'); }

  /* One sound in someone's room: from its distance, panned, a far one dulled, one going past swept across. */
  function playCue(entry, cue) {
    buffer(cue.file).then(function (decoded) {
      if (entry.dead) return;
      var c = context(), now = c.currentTime, dur = decoded.duration;
      var source = c.createBufferSource(), g = c.createGain(), node = source;
      source.buffer = decoded;
      if (cue.lp) {
        var lp = c.createBiquadFilter();
        lp.type = 'lowpass';
        lp.frequency.value = cue.lp;
        node.connect(lp);
        node = lp;
      }
      if (c.createStereoPanner) {
        var pan = c.createStereoPanner();
        pan.pan.value = cue.pan || 0;
        if (cue.sweep) {
          pan.pan.setValueAtTime(cue.sweep[0], now);
          pan.pan.linearRampToValueAtTime(cue.sweep[1], now + dur);
        }
        node.connect(pan);
        node = pan;
      }
      var peak = (cue.gain || 1) * 1.0;            // as loud as the room it's in, never above it
      if (cue.sweep) {
        // Coming, here, gone.
        g.gain.setValueAtTime(peak * 0.35, now);
        g.gain.linearRampToValueAtTime(peak, now + dur * 0.5);
        g.gain.linearRampToValueAtTime(peak * 0.25, now + dur);
      } else {
        g.gain.value = peak;
      }
      node.connect(g).connect(entry.mix);
      source.start();
      // A horn, a siren, a door close by is in the room as loud as a voice: the mic treats it as it treats theirs.
      if (!cue.lp) soundingUntil = Math.max(soundingUntil, Date.now() + dur * 1000 + 300);
    }).catch(function () {});
  }

  function scheduleCues(entry, cues) {
    Object.keys(entry.cueTimers).forEach(function (id) { clearTimeout(entry.cueTimers[id]); });
    entry.cueTimers = {};
    (cues || []).forEach(function (cue) {
      var wait = cue.at * 1000 - Date.now();
      if (wait < -500) return;
      if (wait > 4000) setTimeout(function () { if (!entry.dead) buffer(cue.file); }, wait - 4000);
      else buffer(cue.file);
      entry.cueTimers[cue.id] = setTimeout(function () {
        delete entry.cueTimers[cue.id];
        if (!entry.dead) playCue(entry, cue);
      }, Math.max(0, wait));
    });
  }

  /* The bed for someone on the call: the plan the server laid out for the next couple of minutes. Asked again
     with the same place, it only takes the new moments; somewhere else, the old room fades as the new comes up. */
  function bed(id, spec, gated) {
    var entry = beds[id];
    if (!entry || entry.key !== bedKey(spec)) {
      var old = entry;
      entry = beds[id] = makeEntry(spec);
      if (old) {
        entry.talking = old.talking;
        entry.muted = old.muted;
        retire(old);
      }
    }
    entry.level = spec.level || 1;
    entry.gated = gated;
    entry.cuts = spec.cuts || [];
    (entry.cuts).forEach(function (cut) { buffer(cut.file); });
    scheduleCues(entry, spec.cues);
    level(id, entry.talking);
  }

  function level(id, talking) {
    var entry = beds[id];
    if (!entry) return;
    entry.talking = !!talking;
    var to = entry.muted ? 0 : (talking ? BED_TALKING : entry.gated ? BED_BEHIND : BED_IDLE) * entry.level;
    var now = context().currentTime;
    entry.gain.gain.cancelScheduledValues(now);
    entry.gain.gain.setTargetAtTime(to, now, talking ? 0.25 : 0.9);
  }

  /* Their mic's off: nothing of their room comes down the line, and their ring goes still with it. */
  function muteBed(id, on) {
    var entry = beds[id];
    if (!entry) return;
    entry.muted = !!on;
    var now = context().currentTime;
    entry.gate.gain.cancelScheduledValues(now);
    entry.gate.gain.setTargetAtTime(on ? 0 : 1, now, 0.08);
    level(id, entry.talking);
  }

  /* Between two of their sentences, now and then, a sound of what they're doing — the bag, a landing — and a
     short beat for it. Rarely: at the catalogue's rates (up to most sentence breaks for an acrobat) and with a
     wait as long as the sound, Dick stopped mid-reply for no reason anyone could hear. Only mid-reply. */
  var CUT_RATE = 0.3, CUT_BEAT_MS = 650;
  function cutIn(speaker) {
    var ids = Object.keys(beds);
    var entry = beds[speaker] || (ids.length === 1 ? beds[ids[0]] : null);
    var mid = Date.now() - (lastLine[speaker || ''] || 0) < 1800;
    if (!entry || entry.muted || !mid || !entry.cuts.length) return Promise.resolve();
    var roll = Math.random(), sum = 0;
    var cut = entry.cuts.filter(function (c) { sum += c.p * CUT_RATE; return roll < sum; })[0];
    if (!cut) return Promise.resolve();
    return buffer(cut.file).then(function (decoded) {
      playCue(entry, { file: cut.file, gain: 0.7, pan: 0 });
      return new Promise(function (resolve) { setTimeout(resolve, Math.min(CUT_BEAT_MS, decoded.duration * 400)); });
    }).catch(function () {});
  }

  function retire(entry) {
    entry.dead = true;
    Object.keys(entry.cueTimers).forEach(function (cid) { clearTimeout(entry.cueTimers[cid]); });
    var now = context().currentTime;
    entry.gain.gain.cancelScheduledValues(now);
    entry.gain.gain.setTargetAtTime(0, now, 0.6);
    setTimeout(function () {
      entry.sources.forEach(function (s) { try { s.stop(); } catch (e) { /* already stopped */ } });
      entry.gain.disconnect();
    }, 2500);
  }

  function unbed(id) {
    var entry = beds[id];
    if (!entry) return;
    delete beds[id];
    retire(entry);
  }

  /* What's still to be heard: the sentence playing now, from how far into it we are (0–1), and every one
     queued behind it — what someone picking up mid-call actually hears of it. */
  function remaining() {
    var left = [];
    if (currentItem && ctx) {
      var into = currentItem.duration ? (ctx.currentTime - currentItem.at) / currentItem.duration : 0;
      left.push({ speaker: currentItem.speaker, text: currentItem.text, from: Math.max(0, Math.min(0.98, into)) });
    }
    queue.forEach(function (item) {
      if (item.generation === generation) left.push({ speaker: item.speaker, text: item.text, from: 0 });
    });
    return left;
  }

  global.ConsoleAudio = {
    remaining: remaining,
    setDistant: setDistant,
    context: context,
    resume: resume,
    enqueue: enqueue,
    stop: stop,
    bed: bed,
    unbed: unbed,
    bedLevel: level,
    muteBed: muteBed,
    bedAnalyser: function (id) { return beds[id] && !beds[id].dead ? beds[id].analyser : null; },
    beds: function () { return Object.keys(beds); },
    on: function (name, fn) { handlers[name] = fn; },
    get analyser() { return analyser; },
    get isPlaying() { return playing; },
    get sounding() { return Date.now() < soundingUntil; }
  };
})(window);
