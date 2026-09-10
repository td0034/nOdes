// nrt_voices.scd — write the finale voices as .scsyndef files for NRT.
// @orb-version: 0.1
//
// Synthdefs are COPIED from sound/71surround/orb_synth_spatial.scd (the live
// engine) minus MIDI/OSC plumbing — keep the two in sync when voicing changes.
// The artefact renderer (render_artefact.py) runs this once, then drives
// scsynth -N directly with a python-generated score; no live server involved.
//
// Run:  sclang nrt_voices.scd <output-dir>
(
var dir = thisProcess.argv[0] ? "/tmp/finale_synthdefs";
File.mkdir(dir);

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
    var wA = gains[0] max: gains[1];
    var wB = (gains[4] max: gains[5]) max: (gains[6] max: gains[7]);
    Out.ar(out, sig ! 8 * gains * 0.5);
    Out.ar(rev, [sig * revsend * wA, sig * revsend * wB]);
}).writeDefFile(dir);

SynthDef(\opverb, { |in=0, out=0|
    var s = In.ar(in, 2);
    var wa = GVerb.ar(s[0], 110, 10, 0.4, 0.5, 15, 0, 0.6, 0.75, 300);
    var wb = GVerb.ar(s[1], 110, 10, 0.4, 0.5, 15, 0, 0.6, 0.75, 300);
    var a = (wa[0] + wa[1]) * 0.45;
    var b = (wb[0] + wb[1]) * 0.45;
    Out.ar(out, [a, a, 0, 0, b, b, b, b]);
}).writeDefFile(dir);

SynthDef(\ochime0, { |out=0, rev=99, revsend=0.35, freq=523, amp=0.4|
    var p = [1.0, 2.76, 4.07, 5.40], pa = [1.0, 0.55, 0.35, 0.22], pd = [1.3, 0.8, 0.55, 0.4];
    var sig = Mix.fill(4, { |i| SinOsc.ar(freq * p[i], 0, pa[i]) * EnvGen.kr(Env.perc(0.001, pd[i])) }) * amp * 0.5;
    DetectSilence.ar(sig, 0.0002, 0.1, doneAction: 2);
    Out.ar(out, sig ! 8 * \gains.kr(Array.fill(8, 0.35)));
    Out.ar(rev, sig * revsend * [\gains.kr(Array.fill(8, 0.35))[0..1].reduce('max'), \gains.kr(Array.fill(8, 0.35))[4..7].reduce('max')]);
}).writeDefFile(dir);
SynthDef(\ochime1, { |out=0, rev=99, revsend=0.35, freq=523, amp=0.4|
    var env = EnvGen.kr(Env.perc(0.001, 1.0), doneAction: 2);
    var mod = SinOsc.ar(freq * 2.4) * freq * 1.1 * EnvGen.kr(Env.perc(0.001, 0.22));
    var sig = SinOsc.ar(freq + mod) * env * amp;
    Out.ar(out, sig ! 8 * \gains.kr(Array.fill(8, 0.35)));
    Out.ar(rev, sig * revsend * [\gains.kr(Array.fill(8, 0.35))[0..1].reduce('max'), \gains.kr(Array.fill(8, 0.35))[4..7].reduce('max')]);
}).writeDefFile(dir);
SynthDef(\ochime2, { |out=0, rev=99, revsend=0.3, freq=523, amp=0.5|
    var env = EnvGen.kr(Env.perc(0.005, 0.9), doneAction: 2);
    var click = LPF.ar(WhiteNoise.ar(0.3) * EnvGen.kr(Env.perc(0.001, 0.006)), 2200);
    var sig = (SinOsc.ar(freq) * 0.85 + SinOsc.ar(freq * 2, 0, 0.10) + click) * env * amp;
    Out.ar(out, sig ! 8 * \gains.kr(Array.fill(8, 0.35)));
    Out.ar(rev, sig * revsend * [\gains.kr(Array.fill(8, 0.35))[0..1].reduce('max'), \gains.kr(Array.fill(8, 0.35))[4..7].reduce('max')]);
}).writeDefFile(dir);
SynthDef(\ochime3, { |out=0, rev=99, revsend=0.3, freq=523, amp=0.5|
    var env = EnvGen.kr(Env.perc(0.002, 0.55), doneAction: 2);
    var click = LPF.ar(WhiteNoise.ar(0.6) * EnvGen.kr(Env.perc(0.001, 0.01)), 4500);
    var sig = (SinOsc.ar(freq) * 0.7 + SinOsc.ar(freq * 2, 0, 0.25) + SinOsc.ar(freq * 3, 0, 0.12) + click) * env * amp;
    Out.ar(out, sig ! 8 * \gains.kr(Array.fill(8, 0.35)));
    Out.ar(rev, sig * revsend * [\gains.kr(Array.fill(8, 0.35))[0..1].reduce('max'), \gains.kr(Array.fill(8, 0.35))[4..7].reduce('max')]);
}).writeDefFile(dir);
SynthDef(\ochime4, { |out=0, rev=99, revsend=0.35, freq=523, amp=0.45|
    var p = [1, 2, 3], pa = [1, 0.35, 0.18], pd = [1.9, 1.1, 0.7];
    var sig = Mix.fill(3, { |i| SinOsc.ar(freq * p[i], 0, pa[i]) * EnvGen.kr(Env.perc(0.001, pd[i])) }) * amp * 0.55;
    DetectSilence.ar(sig, 0.0001, 0.1, doneAction: 2);
    Out.ar(out, sig ! 8 * \gains.kr(Array.fill(8, 0.35)));
    Out.ar(rev, sig * revsend * [\gains.kr(Array.fill(8, 0.35))[0..1].reduce('max'), \gains.kr(Array.fill(8, 0.35))[4..7].reduce('max')]);
}).writeDefFile(dir);
SynthDef(\ochime5, { |out=0, rev=99, revsend=0.35, freq=523, amp=0.4|
    var p = [1, 2, 3, 4.2, 5.4, 6.8], pa = [1, 0.4, 0.25, 0.15, 0.1, 0.07], pd = [1.6, 1.0, 0.7, 0.5, 0.4, 0.3];
    var sig = Mix.fill(6, { |i| SinOsc.ar(freq * p[i], 0, pa[i]) * EnvGen.kr(Env.perc(0.001, pd[i])) });
    var shim = SinOsc.ar(freq * 1.004, 0, 0.3) * EnvGen.kr(Env.perc(0.001, 1.4));
    var mix = (sig + shim) * amp * 0.5;
    DetectSilence.ar(mix, 0.0001, 0.1, doneAction: 2);
    Out.ar(out, mix ! 8 * \gains.kr(Array.fill(8, 0.35)));
    Out.ar(rev, mix * revsend * [\gains.kr(Array.fill(8, 0.35))[0..1].reduce('max'), \gains.kr(Array.fill(8, 0.35))[4..7].reduce('max')]);
}).writeDefFile(dir);

SynthDef(\orevmix, { |out=0, in=0, mix=1.0|
    var send = In.ar(in, 2);
    var a = FreeVerb.ar(send[0], 1.0, 0.85, 0.5) * 0.5 * mix;
    var b = FreeVerb.ar(send[1], 1.0, 0.85, 0.5) * 0.5 * mix;
    Out.ar(out, [a, a, 0, 0, b, b, b, b]);
}).writeDefFile(dir);

// ---- sample voices (finale library, sound/finale/samples) ----
// jump: one-shot with a SHARP attack and release, pitched by playback rate
// from the file's base midi (encoded in the filename _NN suffix).
SynthDef(\osmp_jump, { |out=0, rev=99, revsend=0.2, freq=523, amp=0.4,
                       buf=0, basefreq=523|
    var rate = freq / basefreq;
    var env = EnvGen.kr(Env.new([0, 1, 0.55, 0], [0.001, 0.22, 0.55], [0, -3, -4]),
                        doneAction: 2);
    var sig = PlayBuf.ar(1, buf, rate * BufRateScale.kr(buf)) * env * amp * 0.9;
    Out.ar(out, sig ! 8 * \gains.kr(Array.fill(8, 0.35)));
    Out.ar(rev, sig * revsend * [\gains.kr(Array.fill(8, 0.35))[0..1].reduce('max'), \gains.kr(Array.fill(8, 0.35))[4..7].reduce('max')]);
}).writeDefFile(dir);
// pad: the samples are ALREADY 100% wet (make_wet.py), so each strike is a
// wash — low extra rev send, gentle bloom envelope, restrikes overlap.
SynthDef(\osmp_pad, { |out=0, rev=99, revsend=0.08, freq=261, amp=0.5,
                      buf=0, basefreq=261|
    var rate = freq / basefreq;
    var env = EnvGen.kr(Env.new([0, 1, 1, 0], [0.02, 1.8, 1.6], [0, 0, -3]),
                        doneAction: 2);
    var sig = PlayBuf.ar(1, buf, rate * BufRateScale.kr(buf)) * env * amp * 0.7;
    Out.ar(out, sig ! 8 * \gains.kr(Array.fill(8, 0.35)));
    Out.ar(rev, sig * revsend * [\gains.kr(Array.fill(8, 0.35))[0..1].reduce('max'), \gains.kr(Array.fill(8, 0.35))[4..7].reduce('max')]);
}).writeDefFile(dir);
// offline wet-processor: sample -> huge 100%-wet GVerb -> mono (make_wet.py)
SynthDef(\wetify, { |out=0, buf=0|
    var sig = PlayBuf.ar(1, buf, BufRateScale.kr(buf));
    var w = GVerb.ar(sig, 240, 7, 0.45, 0.6, 20, 0, 0.7, 0.8, 300);
    Out.ar(out, (w[0] + w[1]) * 0.5);
}).writeDefFile(dir);

SynthDef(\omaster8, { |out=0, in=0, amp=0.6, thresh=0.4, makeup=1.4|
    var sig = In.ar(in, 8) * amp;
    sig = Compander.ar(sig, sig,
        thresh: thresh, slopeBelow: 1.0, slopeAbove: 0.33,
        clampTime: 0.004, relaxTime: 0.15) * makeup;
    sig = Limiter.ar(sig, 0.95, 0.01);
    Out.ar(out, sig);
}).writeDefFile(dir);

("wrote synthdefs to " ++ dir).postln;
0.exit;
)
