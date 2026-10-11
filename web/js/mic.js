/* ==========================================================================
   Microphone capture, in two modes.

   Push       — hold a key or button, speak, release. Unambiguous, and the only
                thing that works reliably in a noisy room.
   Ambient    — the channel stays open. A voice-activity detector decides when
                an utterance starts and ends, and speaking over a reply cuts it
                off, the way interrupting a person does.

   Ambient mode has two problems that decide its design:

     Echo — the contact's voice leaves the speakers and comes back in through
     the mic. `echoCancellation` is what makes ambient mode viable at all; the
     browser's AEC references the system output and subtracts it. It is not
     perfect, so the VAD threshold is also raised while audio is playing.

     Clipping — by the time energy crosses the threshold, the first syllable
     has already happened. A rolling pre-roll buffer is retained at all times
     and prepended to every utterance, so takes start slightly *before* the
     speech does.
   ========================================================================== */

(function (global) {
  'use strict';

  var TARGET_RATE = 16000;
  var MIN_TAKE_MS = 250;

  // Frames of ~128 samples arrive continuously; these are in frames, resolved
  // against the real sample rate at capture time.
  var PREROLL_MS = 320;
  // Push-to-talk keeps the last moment too: a word begun a beat before the key
  // went down still makes it into the take.
  var PTT_PREROLL_MS = 300;
  // Silence before an utterance is over — counted from the end of his *voice* (see VOICE_HOLD_MS), not from the room
  // going quiet: the level's slow release used to add the better part of a second on top, and a room that never
  // went quiet held a take open for thirty.
  var SILENCE_HANGOVER_MS = 850;
  var MIN_SPEECH_MS = 320;         // shorter than this is a cough, not a sentence
  var MAX_UTTERANCE_MS = 30000;
  var WAKE_MAX_MS = 4500;          // "Hey Alfred, where's Dick?" fits; a conversation across the room doesn't

  // The noise floor tracks the room asymmetrically: it drops to a quieter
  // floor readily, but rises very slowly. A symmetric tracker fast enough to
  // follow the room is also fast enough to follow *speech* — at ~375 frames a
  // second it climbed to meet each utterance within the onset window, so the
  // threshold outran the voice and the detector never fired at all.
  var NOISE_ADAPT_DOWN = 0.02;     // ~0.13s to settle onto a quieter room
  var NOISE_ADAPT_UP = 0.0008;     // ~3s to accept a louder one
  var SPEECH_FACTOR = 2.6;         // energy above floor that counts as speech
  var SPEECH_FACTOR_DUCKED = 7.0;  // far stricter while the contact is talking
  var ABSOLUTE_FLOOR = 0.0025;
  var ONSET_MS = 120;              // sustained energy before a take opens: a knock or a click is shorter
  // Interrupting has to be deliberate. Echo cancellation is imperfect, and a
  // reply leaking back through the speakers at the same bar as real speech
  // would let a contact interrupt itself — then answer what it just said, over
  // and over. Barging in costs a longer, louder commitment.
  // With a voice to go on, not just loudness, a quarter of a second is deliberate enough.
  var ONSET_MS_DUCKED = 220;
  // Reverb and the audio pipeline both outlast the last sample, so the room is
  // still full of the reply for a moment after playback reports it is done.
  var ECHO_TAIL_MS = 550;
  // How far below the opening threshold the room must fall before a take is
  // considered over. Deliberately forgiving: ending early truncates the
  // sentence and Whisper transcribes half a thought, while ending late costs a
  // moment of silence on the end of a clip nobody listens to. Err long.
  var EXIT_RATIO = 0.45;

  /* Is it his voice? Loud was never enough: a mug put down, the keyboard, a door, the TV — anything over the room's
     floor opened a take, held it open while it carried on, and was sent to be transcribed on the GPU the reply was
     being written on. A take opens on his voice now: loud — he's at the mic, close; the room isn't — and shaped like
     a voice: pitched (a fundamental between 70 and 420 Hz, which a click, a fan, a chair or a door hasn't), with its
     energy where speech keeps it (200 Hz – 3.4 kHz, not the rumble or the hiss). It ends when his voice does. */
  var VOICE_RATE = 8000;            // analysed at ~8 kHz: a voice's pitch and formants, at a sixth of the work
  var VOICE_WINDOW_MS = 48;         // long enough for two periods of the lowest voice
  var VOICE_HOP_MS = 20;
  var PITCH_MIN_HZ = 70, PITCH_MAX_HZ = 420;
  var VOICED_CLARITY = 0.55;        // how periodic a window must be (normalised autocorrelation: a voice is 0.7–0.95)
  var SPEECH_BAND = 0.4;            // share of its energy in the speech band (a voice 0.6 and up; hiss about 0.15)
  var VOICE_HOLD_MS = 320;          // after his last pitched moment he's still talking: consonants, the gaps between words
  var VOICE_RECENT_MS = 220;        // how far back an onset looks for pitch
  var ONSET_VOICED = 3;             // pitched windows in that time before a take opens…
  var ONSET_VOICED_DUCKED = 4;      // …and more to talk over someone
  var TAKE_VOICED_MIN = 5;          // a take with less voice in it than this is the room, not him: never sent
  // How loud is "him, at the mic"? Until his voice has been heard, a fixed bar; then a share of how loud he usually is,
  // learned from takes that turned out to be words (see heardHim) — the room's sounds sit well under it.
  var NEAR_DEFAULT = 0.008;
  var NEAR_SHARE = 0.3;
  var NEAR_MIN = 0.005, NEAR_MAX = 0.05;
  var NEAR_DUCKED = 1.6;            // over their voice, he has to be clearly louder than what leaks back in
  // What leaks back in: how loud their voice comes back through the mic while it plays (a high percentile of the
  // last moment and a half, so his own first words barely move it) — and he has to be well over it to cut in. A voice
  // is a voice: their reply leaking past the echo cancelling is pitched too, and used to let them interrupt themselves.
  var LEAK_SPAN_MS = 1500;
  var LEAK_MARGIN = 2.2;
  var VOICE_LEVEL_KEY = 'wayne-voice-level';

  var WORKLET = [
    'class Capture extends AudioWorkletProcessor {',
    '  process(inputs) {',
    '    const ch = inputs[0] && inputs[0][0];',
    '    if (ch) this.port.postMessage(new Float32Array(ch));',
    '    return true;',
    '  }',
    '}',
    'registerProcessor("capture", Capture);'
  ].join('\n');

  var state = {
    stream: null,
    node: null,
    source: null,
    sink: null,
    running: false,
    mode: 'ptt',
    // Held open for the length of a call, so pressing to talk is instant.
    held: false,
    // push mode
    pushing: false,
    chunks: [],
    startedAt: 0,
    // ambient mode
    preroll: [],
    prerollFrames: 0,
    speaking: false,
    silenceMs: 0,
    speechMs: 0,
    envelope: 0,
    noiseFloor: 0.01,
    lastPlayingAt: 0,
    // Live mic: how long a pause ends what he's saying — the quickest on the line decides (see setPatience) —
    // and how much has been heard since the last live caption.
    hangoverMs: SILENCE_HANGOVER_MS,
    sinceCaptionMs: 0,
    endedBy: '',
    // Is it his voice: the mic's own clock (so it holds however fast frames arrive), when he was last pitched, the
    // last moment's verdicts, and how much voice — and how loud — the take in hand has had.
    clock: 0,
    voicedAt: -1e9,
    recent: [],
    takeVoiced: 0,
    takeLevels: [],
    lastTakeLevel: 0,
    lastClarity: 0,
    lastBand: 0,
    voiceLevel: storedVoiceLevel(),
    leak: [],
    leakBar: 0,
    leakAt: 0
  };
  // A caption of what he's saying, this often, while he says it.
  var CAPTION_EVERY_MS = 1200;

  var handlers = {
    onLevel: null,        // (0..1)
    onUtterance: null,    // (Float32Array pcm at TARGET_RATE)
    onSpeechStart: null,
    onSpeechEnd: null,
    onBargeIn: null,
    onPartial: null,      // (Float32Array pcm so far) — for a live caption while he's still talking
    onWake: null,         // (Float32Array pcm) — a short phrase heard while listening for a name, no call on
    onError: null
  };

  function rate() { return global.ConsoleAudio.context().sampleRate; }

  /* Box-average decimation. Averaging over the window rather than picking
     every Nth sample gives a crude low-pass, which keeps a 48 kHz mic from
     aliasing hiss into the band Whisper cares about. */
  function resample(input, inRate) {
    if (inRate === TARGET_RATE) return input;
    var ratio = inRate / TARGET_RATE;
    var outLength = Math.floor(input.length / ratio);
    var out = new Float32Array(outLength);
    for (var i = 0; i < outLength; i++) {
      var start = Math.floor(i * ratio);
      var end = Math.min(Math.floor((i + 1) * ratio), input.length);
      var sum = 0;
      for (var j = start; j < end; j++) sum += input[j];
      out[i] = end > start ? sum / (end - start) : 0;
    }
    return out;
  }

  function merge(chunks) {
    var total = chunks.reduce(function (n, c) { return n + c.length; }, 0);
    var merged = new Float32Array(total);
    var offset = 0;
    chunks.forEach(function (c) { merged.set(c, offset); offset += c.length; });
    return merged;
  }

  function rms(frame) {
    var sum = 0;
    for (var i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
    return Math.sqrt(sum / frame.length);
  }

  /* --- is it his voice -------------------------------------------------- */

  function storedVoiceLevel() {
    try { return parseFloat(localStorage.getItem(VOICE_LEVEL_KEY)) || 0; } catch (e) { return 0; }
  }

  // The bar for "loud enough to be him": a share of his usual level once it's known.
  function nearBar() {
    if (!state.voiceLevel) return NEAR_DEFAULT;
    return Math.max(NEAR_MIN, Math.min(NEAR_MAX, state.voiceLevel * NEAR_SHARE));
  }

  // A second-order filter (the RBJ cookbook's), Q 1/√2: 'low' or 'high' pass at f0.
  function biquad(kind, f0, fs) {
    var w = 2 * Math.PI * f0 / fs, c = Math.cos(w), alpha = Math.sin(w) * Math.SQRT1_2;
    var a0 = 1 + alpha;
    var b0 = (kind === 'low' ? (1 - c) / 2 : (1 + c) / 2) / a0;
    var b1 = (kind === 'low' ? 1 - c : -(1 + c)) / a0;
    var a1 = -2 * c / a0, a2 = (1 - alpha) / a0;
    var x1 = 0, x2 = 0, y1 = 0, y2 = 0;
    return function (x) {
      var y = b0 * x + b1 * x1 + b0 * x2 - a1 * y1 - a2 * y2;
      x2 = x1; x1 = x; y2 = y1; y1 = y;
      return y;
    };
  }

  /* The voice meter: the mic's samples low-passed and taken down to ~8 kHz, the rumble taken off, and every hop a
     verdict on the last window — how periodic it is at a voice's pitch, and how much of its energy is speech's. */
  function makeMeter(inRate) {
    var step = Math.max(1, Math.round(inRate / VOICE_RATE)), rate8 = inRate / step;
    var low = biquad('low', 3400, inRate), high = biquad('high', 200, rate8);
    var size = Math.round(rate8 * VOICE_WINDOW_MS / 1000), hop = Math.round(rate8 * VOICE_HOP_MS / 1000);
    var minLag = Math.floor(rate8 / PITCH_MAX_HZ), maxLag = Math.min(size - 24, Math.ceil(rate8 / PITCH_MIN_HZ));
    var win = new Float32Array(size), pend = new Float32Array(hop), sq = new Float64Array(size + 1);
    var filled = 0, waiting = 0, skip = 0, full = 0, fullN = 0, band = 0, bandN = 0;

    function clarity() {
      for (var i = 0; i < size; i++) sq[i + 1] = sq[i] + win[i] * win[i];
      if (sq[size] < 1e-9) return 0;
      var best = 0;
      for (var lag = minLag; lag <= maxLag; lag++) {
        var n = size - lag, num = 0;
        for (var j = 0; j < n; j++) num += win[j] * win[j + lag];
        var r = num / Math.sqrt(sq[n] * (sq[size] - sq[lag]) + 1e-12);
        if (r > best) best = r;
      }
      return best;
    }
    function verdict(out) {
      var share = fullN && bandN ? (band / bandN) / (full / fullN + 1e-12) : 0;
      full = fullN = band = bandN = 0;
      out.push({ clarity: clarity(), band: Math.min(1, share) });
    }
    return function (frame, out) {
      for (var i = 0; i < frame.length; i++) {
        var x = frame[i];
        full += x * x; fullN++;
        var y = low(x);
        if (++skip < step) continue;
        skip = 0;
        var z = high(y);
        band += z * z; bandN++;
        if (filled < size) {
          win[filled++] = z;
          if (filled === size) verdict(out);
          continue;
        }
        pend[waiting++] = z;
        if (waiting === hop) {
          win.copyWithin(0, hop);
          win.set(pend, size - hop);
          waiting = 0;
          verdict(out);
        }
      }
    };
  }

  var meter = null, meterRate = 0;
  var heard = [];
  // This frame through the meter: whatever windows it completed, judged — and the moment's verdicts kept.
  function listen(frame) {
    var r = rate();
    if (!meter || meterRate !== r) { meter = makeMeter(r); meterRate = r; }
    heard.length = 0;
    meter(frame, heard);
    for (var i = 0; i < heard.length; i++) {
      var w = heard[i];
      // Pitched and in the speech band — and over the room: a hum at the floor is pitched too, and nobody's voice.
      var voiced = w.clarity >= VOICED_CLARITY && w.band >= SPEECH_BAND && state.envelope > state.noiseFloor * 1.5;
      state.lastClarity = w.clarity;
      state.lastBand = w.band;
      state.recent.push({ at: state.clock, voiced: voiced });
      if (voiced) {
        state.voicedAt = state.clock;
        if (state.speaking || state.pushing) {
          state.takeVoiced++;
          if (state.takeLevels.length < 600) state.takeLevels.push(state.envelope);
        }
      }
    }
    while (state.recent.length && state.clock - state.recent[0].at > VOICE_RECENT_MS) state.recent.shift();
  }

  // Their voice in the mic while it plays, sampled every few frames; the bar is a high percentile of it.
  function trackLeak(level, playing) {
    if (!playing) {
      if (state.leak.length && Date.now() - state.lastPlayingAt > ECHO_TAIL_MS) { state.leak = []; state.leakBar = 0; }
      return;
    }
    if (state.speaking) return;
    state.leak.push({ at: state.clock, level: level });
    while (state.leak.length && state.clock - state.leak[0].at > LEAK_SPAN_MS) state.leak.shift();
    if (state.clock - state.leakAt < 50) return;
    state.leakAt = state.clock;
    var levels = state.leak.map(function (l) { return l.level; }).sort(function (a, b) { return a - b; });
    state.leakBar = levels[Math.floor(levels.length * 0.8)] || 0;
  }

  function pitchedLately() {
    var n = 0;
    for (var i = 0; i < state.recent.length; i++) if (state.recent[i].voiced) n++;
    return n;
  }

  // How loud his voice was in the take just finished: the median of the pitched moments.
  function takeLevel() {
    var levels = state.takeLevels.slice().sort(function (a, b) { return a - b; });
    return levels.length ? levels[Math.floor(levels.length / 2)] : 0;
  }

  /* --- lifecycle --------------------------------------------------------- */

  // Each open() gets a token; close() spends it. A tap shorter than the
  // permission prompt closed nothing (the stream didn't exist yet), then the
  // stream arrived and the mic stayed live; a double tap opened two.
  var openToken = 0;
  var opening = null;
  // The capture worklet is registered once per audio context, not per press.
  var workletReady = null;

  function open() {
    if (state.running) return Promise.resolve();
    if (opening) return opening;
    var ctx = global.ConsoleAudio.context();
    var token = ++openToken;

    opening = navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        // Echo cancellation is what makes ambient mode possible at all: it
        // references the system output and subtracts the reply coming back in
        // through the speakers.
        echoCancellation: true,
        noiseSuppression: true,
        // Automatic gain control is deliberately off. It ramps gain up during
        // pauses, which lifts room noise above any level-based threshold — a
        // take opened and then never closed, because the "silence" kept
        // getting amplified into something the detector read as speech.
        autoGainControl: false
      }
    }).then(function (stream) {
      if (token !== openToken) {         // closed while we were asking
        stream.getTracks().forEach(function (t) { t.stop(); });
        return;
      }
      state.stream = stream;
      if (!workletReady) {
        var blob = new Blob([WORKLET], { type: 'application/javascript' });
        workletReady = ctx.audioWorklet.addModule(URL.createObjectURL(blob))
          .catch(function (err) { workletReady = null; throw err; });
      }
      return workletReady.then(function () {
        if (token !== openToken) {
          stream.getTracks().forEach(function (t) { t.stop(); });
          state.stream = null;
          return;
        }
        state.source = ctx.createMediaStreamSource(stream);
        state.node = new AudioWorkletNode(ctx, 'capture');
        state.node.port.onmessage = function (e) { onFrame(e.data); };
        state.source.connect(state.node);
        // Worklets are only pulled when connected to the graph; a zero-gain
        // sink keeps it running without feeding the mic back to the speakers.
        state.sink = ctx.createGain();
        state.sink.gain.value = 0;
        state.node.connect(state.sink).connect(ctx.destination);
        state.running = true;
      });
    }).catch(function (err) {
      state.running = false;
      if (handlers.onError) handlers.onError(err);
      throw err;
    }).finally(function () {
      if (token === openToken) opening = null;
    });
    return opening;
  }

  function close() {
    openToken++;
    opening = null;
    state.held = false;
    if (state.node) { try { state.node.port.onmessage = null; state.node.disconnect(); } catch (e) {} }
    if (state.source) { try { state.source.disconnect(); } catch (e) {} }
    if (state.sink) { try { state.sink.disconnect(); } catch (e) {} }
    if (state.stream) state.stream.getTracks().forEach(function (t) { t.stop(); });
    state.node = state.source = state.sink = state.stream = null;
    state.running = false;
    resetAmbient();
  }

  function resetAmbient() {
    state.preroll = [];
    state.prerollFrames = 0;
    state.speaking = false;
    state.silenceMs = 0;
    state.speechMs = 0;
    state.sinceCaptionMs = 0;
    state.chunks = [];
    state.takeVoiced = 0;
    state.takeLevels = [];
  }

  /* --- frame handling ---------------------------------------------------- */

  function onFrame(frame) {
    var level = rms(frame);
    var frameMs = (frame.length / rate()) * 1000;
    state.clock += frameMs;

    // Detection runs on an envelope, not on raw frame energy. A 128-sample
    // frame is under 3ms — far shorter than the gaps between words, so raw
    // RMS dips below any sensible threshold constantly and a take gets cut
    // mid-sentence. Fast attack keeps onset responsive; slow release bridges
    // the gaps. The visualizer still gets the unsmoothed level.
    state.envelope += (level - state.envelope) * (level > state.envelope ? 0.5 : 0.012);

    if (state.mode === 'ptt') {
      if (state.pushing) {
        listen(frame);                   // held to talk: how loud his voice is, learned here too
        state.chunks.push(frame);
        if (handlers.onLevel) handlers.onLevel(Math.min(level * 5, 1));
        return;
      }
      // Held open between presses: keep a short rolling history, and keep the
      // visualizer still — the room isn't talking to anyone. Not while the
      // contact is speaking, or the take would open on their own last words.
      if (global.ConsoleAudio.isPlaying) {
        state.preroll = [];
        state.prerollFrames = 0;
        return;
      }
      state.preroll.push(frame);
      state.prerollFrames += frame.length;
      var keep = (PTT_PREROLL_MS / 1000) * rate();
      while (state.preroll.length > 1 && state.prerollFrames - state.preroll[0].length > keep) {
        state.prerollFrames -= state.preroll.shift().length;
      }
      return;
    }

    listen(frame);
    // The visualiser moves for his voice, not for the room: a cough or a door shows as a flicker at most.
    var voicing = state.clock - state.voicedAt < VOICE_HOLD_MS;
    if (handlers.onLevel && state.mode !== 'wake') handlers.onLevel(Math.min(level * (voicing || state.speaking ? 5 : 1.5), 1));

    ambientFrame(frame, state.envelope, frameMs);
  }

  function ambientFrame(frame, level, frameMs) {
    // Track the room's noise floor only while nobody is talking *and* no take
    // is building — adapting through an onset lets the detector normalise away
    // the very speech it is trying to catch.
    if (!state.speaking && state.speechMs === 0) {
      var alpha = level < state.noiseFloor ? NOISE_ADAPT_DOWN : NOISE_ADAPT_UP;
      state.noiseFloor += (level - state.noiseFloor) * alpha;
    }

    // Two thresholds, not one. Opening a take takes real energy; closing it
    // requires falling well *below* that bar. With a single threshold the
    // detector chatters around it — a sentence would end and immediately
    // reopen, splitting one utterance into two half-transcribed fragments.
    if (global.ConsoleAudio.isPlaying) state.lastPlayingAt = Date.now();
    // Treat the moments just after playback as still "ducked": the tail of the
    // reply is physically still in the room.
    // And a sound of their room close by — a horn, a siren — is as loud in here as a voice.
    var ducked = global.ConsoleAudio.isPlaying || global.ConsoleAudio.sounding ||
                 (Date.now() - state.lastPlayingAt) < ECHO_TAIL_MS;
    var factor = ducked ? SPEECH_FACTOR_DUCKED : SPEECH_FACTOR;
    trackLeak(level, ducked);
    // Over the room by the usual margin — and loud enough to be him at the mic, not the room's own sounds; over
    // their voice, well over what of it comes back in.
    var enter = Math.max(state.noiseFloor * factor, ABSOLUTE_FLOOR, nearBar() * (ducked ? NEAR_DUCKED : 1),
                         ducked ? state.leakBar * LEAK_MARGIN : 0);
    // The exit bar is also clamped above the measured room, because an exit
    // threshold below the noise floor can never be crossed — the take opens
    // and then stays open forever, which is the other way this fails.
    var exit = Math.max(enter * EXIT_RATIO, state.noiseFloor * 1.8);
    // Loud, and his voice: pitched a moment ago. Loud and not a voice — the TV, the kettle, the room carrying on —
    // neither opens a take nor keeps one open.
    var voicing = state.clock - state.voicedAt < VOICE_HOLD_MS;
    var voiced = (state.speaking ? level > exit : level > enter) && voicing;

    if (!state.speaking) {
      // Always retain a little history so a take never starts mid-syllable.
      state.preroll.push(frame);
      state.prerollFrames += frame.length;
      var keep = (PREROLL_MS / 1000) * rate();
      while (state.prerollFrames - state.preroll[0].length > keep) {
        state.prerollFrames -= state.preroll.shift().length;
      }

      if (voiced) {
        state.speechMs += frameMs;
        if (state.speechMs >= (ducked ? ONSET_MS_DUCKED : ONSET_MS) &&
            pitchedLately() >= (ducked ? ONSET_VOICED_DUCKED : ONSET_VOICED)) {
          state.speaking = true;
          state.silenceMs = 0;
          state.takeVoiced = pitchedLately();
          state.chunks = state.preroll.slice();
          state.preroll = [];
          state.prerollFrames = 0;
          if (global.ConsoleAudio.isPlaying && handlers.onBargeIn) handlers.onBargeIn();
          if (handlers.onSpeechStart) handlers.onSpeechStart();
        }
      } else {
        // Decay rather than reset. Speech is full of micro-gaps — plosives,
        // the space between words — and zeroing the counter on any quiet frame
        // meant the onset restarted continuously and a normal sentence never
        // accumulated the sustained energy needed to open a take.
        state.speechMs = Math.max(0, state.speechMs - frameMs * 2);
      }
      return;
    }

    state.chunks.push(frame);
    state.speechMs += frameMs;
    state.silenceMs = voiced ? 0 : state.silenceMs + frameMs;
    state.sinceCaptionMs += frameMs;
    if (state.mode === 'wake' && state.speechMs >= WAKE_MAX_MS) {
      // Listening for a name, not a speech: a long stretch of talk in the room is somebody else's conversation.
      resetAmbient();
      return;
    }
    if (state.mode !== 'wake' && handlers.onPartial && state.sinceCaptionMs >= CAPTION_EVERY_MS && voiced) {
      state.sinceCaptionMs = 0;
      handlers.onPartial(resample(merge(state.chunks), rate()));
    }

    if (state.silenceMs >= state.hangoverMs || state.speechMs >= MAX_UTTERANCE_MS) {
      // Ended by a pause shorter than an ordinary one: someone quick on the line came in on it.
      state.endedBy = state.hangoverMs < SILENCE_HANGOVER_MS && state.silenceMs < SILENCE_HANGOVER_MS ? 'cut' : 'pause';
      finishUtterance();
    }
  }

  function finishUtterance() {
    var chunks = state.chunks;
    var spoke = state.speechMs - state.silenceMs;
    var voice = state.takeVoiced;
    state.lastTakeLevel = takeLevel();
    resetAmbient();
    if (handlers.onSpeechEnd) handlers.onSpeechEnd();
    // Too short, or too little voice in it to be him: the room. Not sent to be heard — which costs the GPU the
    // reply's being written on — at all.
    if (spoke < MIN_SPEECH_MS || !chunks.length || voice < TAKE_VOICED_MIN) return;
    var pcm = resample(merge(chunks), rate());
    if (state.mode === 'wake') { if (handlers.onWake) handlers.onWake(pcm); return; }
    if (handlers.onUtterance) handlers.onUtterance(pcm);
  }

  /* --- public API -------------------------------------------------------- */

  function setMode(mode) {
    if (state.mode === mode) return Promise.resolve();
    state.mode = mode;
    // 'wake': no call on, listening only for "Hey" and a name (see app.js) — the same detector as live mic.
    if (mode === 'ambient' || mode === 'wake') return open().then(resetAmbient);
    resetAmbient();
    if (!state.held) close();
    return Promise.resolve();
  }

  /**
   * Open the mic now and keep it open — called as a call starts ringing, so
   * the first press has nothing to wait for. Opening on every press cost the
   * permission check, the worklet and the hardware's wake-up each time, and
   * the first half-second of whatever was said went with them.
   */
  function warm() {
    state.held = true;
    return open().catch(function () {});
  }

  function pushStart() {
    if (state.mode !== 'ptt') return Promise.resolve();
    state.pushing = true;
    state.takeVoiced = 0;
    state.takeLevels = [];
    state.chunks = state.preroll.slice();     // the moment just before the key
    state.preroll = [];
    state.prerollFrames = 0;
    state.startedAt = Date.now();
    return state.running ? Promise.resolve() : open();
  }

  function pushStop() {
    if (state.mode !== 'ptt' || !state.pushing) return;
    state.pushing = false;
    var tooShort = Date.now() - state.startedAt < MIN_TAKE_MS;
    var chunks = state.chunks;
    state.chunks = [];
    state.lastTakeLevel = takeLevel();
    var captureRate = rate();
    if (!state.held) close();      // on a call it stays open for the next press
    if (handlers.onLevel) handlers.onLevel(0);
    if (tooShort || !chunks.length) return;
    if (handlers.onUtterance) handlers.onUtterance(resample(merge(chunks), captureRate));
  }

  /**
   * End the current ambient take immediately rather than waiting out the
   * silence hangover. Bound to the mic button in ambient mode, so there is
   * always a way to say "that's it, go" without sitting through the pause.
   */
  function cut() {
    if (state.mode !== 'ambient' || !state.speaking) return false;
    finishUtterance();
    return true;
  }

  /* How long a pause ends what he's saying, in live mic: an ordinary pause by default, shorter when someone quick
     to come in is on the line — Jason doesn't wait for him to finish a thought. Never longer than ordinary. */
  function setPatience(ms) {
    state.hangoverMs = Math.max(380, Math.min(SILENCE_HANGOVER_MS, ms || SILENCE_HANGOVER_MS));
  }

  /* The take just sent turned out to be words: how loud he was in it is how loud he is — folded into what "loud
     enough to be him" means from now on, and kept for the next visit. */
  function heardHim() {
    var level = state.lastTakeLevel;
    state.lastTakeLevel = 0;
    if (!(level > NEAR_MIN)) return;
    state.voiceLevel = state.voiceLevel ? state.voiceLevel * 0.75 + level * 0.25 : level;
    try { localStorage.setItem(VOICE_LEVEL_KEY, String(Math.round(state.voiceLevel * 1e5) / 1e5)); } catch (e) { /* kept for this visit only */ }
  }

  // Frames handed in directly, as the worklet would — for the page's own checks of the detector.
  function feed(frame) { onFrame(frame); }

  global.ConsoleMic = {
    setMode: setMode,
    setPatience: setPatience,
    heardHim: heardHim,
    feed: feed,
    get endedBy() { return state.endedBy; },
    pushStart: pushStart,
    pushStop: pushStop,
    warm: warm,
    cut: cut,
    close: close,
    on: function (name, fn) { handlers[name] = fn; },
    get mode() { return state.mode; },
    get isPushing() { return state.pushing; },
    get isOpen() { return state.running; },
    get isSpeaking() { return state.speaking; },
    // The detector is a handful of thresholds against a room that varies; being
    // able to read what it currently believes is the difference between tuning
    // it and guessing at it.
    get vad() {
      var playing = global.ConsoleAudio.isPlaying;
      return {
        noiseFloor: state.noiseFloor,
        threshold: Math.max(state.noiseFloor * (playing ? SPEECH_FACTOR_DUCKED : SPEECH_FACTOR), ABSOLUTE_FLOOR,
                            nearBar() * (playing ? NEAR_DUCKED : 1)),
        envelope: state.envelope,
        near: nearBar(),
        voiceLevel: state.voiceLevel,
        clarity: Math.round(state.lastClarity * 100) / 100,
        band: Math.round(state.lastBand * 100) / 100,
        voicing: state.clock - state.voicedAt < VOICE_HOLD_MS,
        speaking: state.speaking,
        speechMs: Math.round(state.speechMs),
        silenceMs: Math.round(state.silenceMs)
      };
    },
    TARGET_RATE: TARGET_RATE
  };
})(window);
