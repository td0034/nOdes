// orb_synth_spatial.scd — the Keynsham/summer-school sound, spatialised.
// @orb-version: 0.1
//
// Same conductor, same MIDI contract, same voices as sound/orb_synth_pi.scd:
// chimes on jump/shake (ch5), piano pad strikes while spinning (ch3), cluster
// note = circle of fifths, 6 variants of each timbre. The ONE addition: the
// conductor tags each strike with the source orb's SLOT (CC#14), and each
// voice's DRY signal is placed through that orb's live 8-channel gain vector
// (from spatial_router.py via OSC /orb/gains). Reverbs are PER RIG (front /
// rear+side), each fed in proportion to its sources' placement gains — so at
// 12 clusters each rig's wash carries only its own orbs' notes, not a
// chromatic pile-up of every note in the room.
//
// Channel map (card order, do not reorder): 0:FL 1:FR 2:FC 3:LFE 4:RL 5:RR 6:SL 7:SR
// Run: pw-jack sclang orb_synth_spatial.scd   (start_rig.sh does everything)

(
var ccState, spatBus, revBus, pianoRev;
var padGrp, padVoices, padCap;
var chimeGrp, chimeVoices, chimeCap;
var pianoBright, pianoDecay, pianoSend;
var defaultGains;

"orb_synth_spatial booting...".postln;

Server.default.options.numOutputBusChannels = 8;
Server.default.options.memSize = 32768;   // headroom for GVerb delay lines

Server.default.waitForBoot({

    // ---- piano pad strike (identical voice; dry out is placed via \gains) ----
    SynthDef(\opiano, { |out=0, rev=0, freq=261, amp=0.5, bright=0.5, decay=1.1,
                        revsend=0.85, attack=0.01|
        var n = 8, inh = 0.0004;
        var roll = bright.linlin(0, 1, 1.6, 0.8);
        var env = EnvGen.kr(Env.perc(attack, decay, 1, -4), doneAction: 2);
        var voices = Mix.fill(n, { |i|
            var k = i + 1;
            SinOsc.ar(freq * k * (1 + (inh * k * k)).sqrt, 0, k ** roll.neg);
        });
        var sig = voices * env * amp * 0.45;
        var gains = \gains.kr(Array.fill(8, 0.35));
        // Rig-weighted reverb sends: the tail follows the strike's own
        // speakers. With 12 clusters live, each rig's wash only carries the
        // notes of the orbs AT that rig — not a chromatic pile-up of all 12.
        var wA = gains[0] max: gains[1];                                    // front rig (FL/FR)
        var wB = (gains[4] max: gains[5]) max: (gains[6] max: gains[7]);    // rear/side rig
        Out.ar(out, sig ! 8 * gains * 0.5);    // dry, placed at the orb
        Out.ar(rev, [sig * revsend * wA, sig * revsend * wB]);
    }).add;

    SynthDef(\opverb, { |in=0, out=0|
        // Two independent washes, one per rig: [front-rig send, rear-rig send].
        var s = In.ar(in, 2);
        var wa = GVerb.ar(s[0], 110, 10, 0.4, 0.5, 15, 0, 0.6, 0.75, 300);
        var wb = GVerb.ar(s[1], 110, 10, 0.4, 0.5, 15, 0, 0.6, 0.75, 300);
        var a = (wa[0] + wa[1]) * 0.45;
        var b = (wb[0] + wb[1]) * 0.45;
        Out.ar(out, [a, a, 0, 0, b, b, b, b]);
    }).add;

    // ---- the six chimes, verbatim voices, dry out placed via \gains ----
    SynthDef(\ochime0, { |out=0, rev=99, revsend=0.35, freq=523, amp=0.4|
        var p = [1.0, 2.76, 4.07, 5.40], pa = [1.0, 0.55, 0.35, 0.22], pd = [1.3, 0.8, 0.55, 0.4];
        var sig = Mix.fill(4, { |i| SinOsc.ar(freq * p[i], 0, pa[i]) * EnvGen.kr(Env.perc(0.001, pd[i])) }) * amp * 0.5;
        DetectSilence.ar(sig, 0.0002, 0.1, doneAction: 2);
        Out.ar(out, sig ! 8 * \gains.kr(Array.fill(8, 0.35)));
        Out.ar(rev, sig * revsend * [\gains.kr(Array.fill(8, 0.35))[0..1].reduce('max'), \gains.kr(Array.fill(8, 0.35))[4..7].reduce('max')]);
    }).add;
    SynthDef(\ochime1, { |out=0, rev=99, revsend=0.35, freq=523, amp=0.4|
        var env = EnvGen.kr(Env.perc(0.001, 1.0), doneAction: 2);
        var mod = SinOsc.ar(freq * 2.4) * freq * 1.1 * EnvGen.kr(Env.perc(0.001, 0.22));
        var sig = SinOsc.ar(freq + mod) * env * amp;
        Out.ar(out, sig ! 8 * \gains.kr(Array.fill(8, 0.35)));
        Out.ar(rev, sig * revsend * [\gains.kr(Array.fill(8, 0.35))[0..1].reduce('max'), \gains.kr(Array.fill(8, 0.35))[4..7].reduce('max')]);
    }).add;
    SynthDef(\ochime2, { |out=0, rev=99, revsend=0.3, freq=523, amp=0.5|
        var env = EnvGen.kr(Env.perc(0.005, 0.9), doneAction: 2);
        var click = LPF.ar(WhiteNoise.ar(0.3) * EnvGen.kr(Env.perc(0.001, 0.006)), 2200);
        var sig = (SinOsc.ar(freq) * 0.85 + SinOsc.ar(freq * 2, 0, 0.10) + click) * env * amp;
        Out.ar(out, sig ! 8 * \gains.kr(Array.fill(8, 0.35)));
        Out.ar(rev, sig * revsend * [\gains.kr(Array.fill(8, 0.35))[0..1].reduce('max'), \gains.kr(Array.fill(8, 0.35))[4..7].reduce('max')]);
    }).add;
    SynthDef(\ochime3, { |out=0, rev=99, revsend=0.3, freq=523, amp=0.5|
        var env = EnvGen.kr(Env.perc(0.002, 0.55), doneAction: 2);
        var click = LPF.ar(WhiteNoise.ar(0.6) * EnvGen.kr(Env.perc(0.001, 0.01)), 4500);
        var sig = (SinOsc.ar(freq) * 0.7 + SinOsc.ar(freq * 2, 0, 0.25) + SinOsc.ar(freq * 3, 0, 0.12) + click) * env * amp;
        Out.ar(out, sig ! 8 * \gains.kr(Array.fill(8, 0.35)));
        Out.ar(rev, sig * revsend * [\gains.kr(Array.fill(8, 0.35))[0..1].reduce('max'), \gains.kr(Array.fill(8, 0.35))[4..7].reduce('max')]);
    }).add;
    SynthDef(\ochime4, { |out=0, rev=99, revsend=0.35, freq=523, amp=0.45|
        var p = [1, 2, 3], pa = [1, 0.35, 0.18], pd = [1.9, 1.1, 0.7];
        var sig = Mix.fill(3, { |i| SinOsc.ar(freq * p[i], 0, pa[i]) * EnvGen.kr(Env.perc(0.001, pd[i])) }) * amp * 0.55;
        DetectSilence.ar(sig, 0.0001, 0.1, doneAction: 2);
        Out.ar(out, sig ! 8 * \gains.kr(Array.fill(8, 0.35)));
        Out.ar(rev, sig * revsend * [\gains.kr(Array.fill(8, 0.35))[0..1].reduce('max'), \gains.kr(Array.fill(8, 0.35))[4..7].reduce('max')]);
    }).add;
    SynthDef(\ochime5, { |out=0, rev=99, revsend=0.35, freq=523, amp=0.4|
        var p = [1, 2, 3, 4.2, 5.4, 6.8], pa = [1, 0.4, 0.25, 0.15, 0.1, 0.07], pd = [1.6, 1.0, 0.7, 0.5, 0.4, 0.3];
        var sig = Mix.fill(6, { |i| SinOsc.ar(freq * p[i], 0, pa[i]) * EnvGen.kr(Env.perc(0.001, pd[i])) });
        var shim = SinOsc.ar(freq * 1.004, 0, 0.3) * EnvGen.kr(Env.perc(0.001, 1.4));
        var mix = (sig + shim) * amp * 0.5;
        DetectSilence.ar(mix, 0.0001, 0.1, doneAction: 2);
        Out.ar(out, mix ! 8 * \gains.kr(Array.fill(8, 0.35)));
        Out.ar(rev, mix * revsend * [\gains.kr(Array.fill(8, 0.35))[0..1].reduce('max'), \gains.kr(Array.fill(8, 0.35))[4..7].reduce('max')]);
    }).add;

    SynthDef(\orevmix, { |out=0, in=0, mix=1.0|
        // Per-rig chime shimmer: [front-rig send, rear-rig send].
        var send = In.ar(in, 2);
        var a = FreeVerb.ar(send[0], 1.0, 0.85, 0.5) * 0.5 * mix;
        var b = FreeVerb.ar(send[1], 1.0, 0.85, 0.5) * 0.5 * mix;
        Out.ar(out, [a, a, 0, 0, b, b, b, b]);
    }).add;

    // 8-channel master: same compress-then-limit glue as the Pi rig, per channel.
    SynthDef(\omaster8, { |out=0, in=0, amp=0.6, thresh=0.4, makeup=1.4|
        var sig = In.ar(in, 8) * amp;
        sig = Compander.ar(sig, sig,
            thresh: thresh, slopeBelow: 1.0, slopeAbove: 0.33,
            clampTime: 0.004, relaxTime: 0.15) * makeup;
        sig = Limiter.ar(sig, 0.95, 0.01);
        Out.ar(out, sig);
    }).add;

    // ---- finale sample voices (sound/finale/samples, CC0/VCSL) ----
    // Variants 6-11 on both channels; base midi is the filename _NN suffix.
    // KEEP IN SYNC with sound/finale/nrt_voices.scd (artefact renderer).
    SynthDef(\osmp_jump, { |out=0, rev=99, revsend=0.2, freq=523, amp=0.4,
                           buf=0, basefreq=523|
        var rate = freq / basefreq;
        var env = EnvGen.kr(Env.new([0, 1, 0.55, 0], [0.001, 0.22, 0.55], [0, -3, -4]),
                            doneAction: 2);
        var sig = PlayBuf.ar(1, buf, rate * BufRateScale.kr(buf)) * env * amp * 0.9;
        Out.ar(out, sig ! 8 * \gains.kr(Array.fill(8, 0.35)));
        Out.ar(rev, sig * revsend * [\gains.kr(Array.fill(8, 0.35))[0..1].reduce('max'), \gains.kr(Array.fill(8, 0.35))[4..7].reduce('max')]);
    }).add;
    SynthDef(\osmp_pad, { |out=0, rev=99, revsend=0.08, freq=261, amp=0.5,
                          buf=0, basefreq=261|
        var rate = freq / basefreq;
        var env = EnvGen.kr(Env.new([0, 1, 1, 0], [0.02, 1.8, 1.6], [0, 0, -3]),
                            doneAction: 2);
        var sig = PlayBuf.ar(1, buf, rate * BufRateScale.kr(buf)) * env * amp * 0.7;
        Out.ar(out, sig ! 8 * \gains.kr(Array.fill(8, 0.35)));
        Out.ar(rev, sig * revsend * [\gains.kr(Array.fill(8, 0.35))[0..1].reduce('max'), \gains.kr(Array.fill(8, 0.35))[4..7].reduce('max')]);
    }).add;

    Server.default.sync;

    // Load the sample library (absent dir -> empty lists, synth-only variants).
    ~smpDir = thisProcess.nowExecutingPath.dirname +/+ ".." +/+ "finale" +/+ "samples";
    ~loadSmp = { |sub|
        var files = PathName(~smpDir +/+ sub).files.select({ |f| f.extension == "wav" })
                        .sort({ |a, b| a.fileName < b.fileName });
        files.collect({ |f|
            var midi = f.fileNameWithoutExtension.split($_).last.asInteger;
            (buf: Buffer.read(Server.default, f.fullPath), basefreq: midi.midicps)
        });
    };
    ~smpJump = ~loadSmp.("jump");
    ~smpPad = ~loadSmp.("pad");
    // Per-voice loudness trims (measure_voices.py --write): every voice sits
    // at its family's level so no cluster's sound masks another's.
    ~trims = (chime: 1.0 ! 12, pad: 1.0 ! 12);
    {
        var f = ~smpDir +/+ ".." +/+ "voice_trims.json";
        if (File.exists(f)) {
            var j = File.readAllString(f).parseYAML;
            ~trims = (chime: j["chime"].collect(_.asFloat),
                      pad: j["pad"].collect(_.asFloat));
            "voice trims loaded".postln;
        };
    }.value;
    Server.default.sync;
    ("sample voices: % jump, % pad".format(~smpJump.size, ~smpPad.size)).postln;

    // ================== Routing ==================
    ccState = Array.fill(16, { Dictionary.new });
    padGrp = Group.new;  padVoices = List.new;  padCap = 14;  padGrp.freeAll;
    chimeGrp = Group.new; chimeVoices = List.new; chimeCap = 8; chimeGrp.freeAll;

    spatBus = Bus.audio(Server.default, 8);
    revBus = Bus.audio(Server.default, 2);
    pianoRev = Bus.audio(Server.default, 2);   // [front-rig send, rear-rig send]
    pianoBright = [0.40, 0.75, 0.25, 0.60, 0.30, 0.50];
    pianoDecay  = [1.2,  0.9,  1.5,  1.0,  1.7,  0.9];
    pianoSend   = [0.85, 0.9,  0.8,  0.85, 0.95, 0.8];
    Synth.tail(nil, \orevmix, [\in, revBus.index, \out, spatBus.index]);
    Synth.tail(nil, \opverb, [\in, pianoRev.index, \out, spatBus.index]);
    Synth.tail(nil, \omaster8, [\in, spatBus.index, \out, 0, \amp, 0.6]);

    // ================== Spatial state ==================
    // slot -> latest 8-gain vector from spatial_router.py. Voices sample it at
    // spawn; a strike from an orb the router hasn't reported yet lands softly
    // in both rigs rather than nowhere.
    ~slotGains = IdentityDictionary.new;
    defaultGains = [0.35, 0.35, 0, 0, 0.35, 0.35, 0.35, 0.35];
    OSCdef(\gains, { |msg|
        ~slotGains[msg[1].asInteger] = msg[2..9];
    }, '/orb/gains');
    ~gainsFor = { |slot|
        var g = ~slotGains[slot];
        if (g.isNil or: { g.sum < 0.01 }) { defaultGains } { g };
    };

    // ================== MIDI ==================
    MIDIClient.init;
    MIDIIn.connectAll;

    MIDIdef.noteOn(\on, { |vel, note, chan|
        var amp = (vel / 127).squared * 0.8;
        case
        { chan == 3 } {                       // pad strike, placed at its orb
            var cc = ccState[chan];
            var idx = ((cc[12] ? 0) * 127).round.clip(0, 11);
            var cents = ((cc[9] ? (64/127)) * 127) - 64;
            var slot = ((cc[14] ? 0) * 127).round.asInteger;    // source orb slot
            amp = amp * ~trims.pad[idx];
            while { padVoices.size >= padCap } { padVoices.removeAt(0).free };
            if ((idx > 5) and: { ~smpPad.size > 0 }) {
                var s = ~smpPad[(idx - 6) % ~smpPad.size];
                padVoices.add(Synth(\osmp_pad, [
                    \out, spatBus.index, \rev, pianoRev.index,
                    \freq, note.midicps * (2 ** (cents / 1200)), \amp, amp,
                    \buf, s.buf, \basefreq, s.basefreq,
                    \gains, ~gainsFor.(slot),
                ], padGrp));
            } {
                padVoices.add(Synth(\opiano, [
                    \out, spatBus.index, \rev, pianoRev.index,
                    \freq, note.midicps * (2 ** (cents / 1200)), \amp, amp,
                    \bright, pianoBright[idx % 6], \decay, pianoDecay[idx % 6],
                    \revsend, pianoSend[idx % 6],
                    \gains, ~gainsFor.(slot),
                ], padGrp));
            };
        }
        { chan == 5 } {                       // chime, placed at its orb
            var cc = ccState[chan];
            var idx = ((cc[12] ? 0) * 127).round.clip(0, 11);
            var cents = ((cc[9] ? (64/127)) * 127) - 64;
            var slot = ((cc[14] ? 0) * 127).round.asInteger;
            var defs = [\ochime0, \ochime1, \ochime2, \ochime3, \ochime4, \ochime5];
            amp = amp * ~trims.chime[idx];
            while { chimeVoices.size >= chimeCap } { chimeVoices.removeAt(0).free };
            if ((idx > 5) and: { ~smpJump.size > 0 }) {
                var s = ~smpJump[(idx - 6) % ~smpJump.size];
                chimeVoices.add(Synth(\osmp_jump, [\out, spatBus.index, \rev, revBus.index,
                    \freq, note.midicps * (2 ** (cents / 1200)), \amp, amp,
                    \buf, s.buf, \basefreq, s.basefreq,
                    \gains, ~gainsFor.(slot)], chimeGrp));
            } {
                chimeVoices.add(Synth(defs[idx % 6], [\out, spatBus.index, \rev, revBus.index,
                    \freq, note.midicps * (2 ** (cents / 1200)), \amp, amp,
                    \gains, ~gainsFor.(slot)], chimeGrp));
            };
        };
    });

    MIDIdef.noteOff(\off, { |vel, note, chan| });   // all voices self-free

    MIDIdef.cc(\cc, { |val, num, chan|
        ccState[chan][num] = val / 127;
        if (num == 123) { (chan == 3).if { padGrp.freeAll; padVoices.clear } };
    });

    "orb_synth_spatial ready — MIDI bound, /orb/gains open on port %".format(NetAddr.langPort).postln;
});
)
