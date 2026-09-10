// orb_synth_pi.scd — headless synth host for the Pi rig (no GUI, no scide).
// @orb-version: 0.1
//
// Run under sclang with QT_QPA_PLATFORM=offscreen (orb-synth.service).
// Companion: sound/conductor_pi.py sends MIDI on a virtual ALSA port.
//
// Channel map (MIDI ch 1-16 shown 1-based; SC sees 0-based):
//   1 bass      — cluster voice (sustained, gate)
//   2 perc      — cluster voice (short hits, conductor sends pulses)
//   3 pluck     — cluster voice "piano"
//   4 pad       — cluster voice "strings"
//   5 lead      — cluster voice synth line
//   7 sting     — jump events; note-ons are QUANTISED to the next 16th
//   8 win       — prompt-topped cadence chord (immediate)
//  16 control   — CC20 tempo bpm(0-127 -> 20-180), CC21 kick amp,
//                 CC22 master cutoff, CC23 reverb mix, CC24 kick density
//
// Per-voice CCs (same contract as orb_synth.scd / bridge.py):
//   CC7 amp, CC10 pan, CC71 reso, CC72 release, CC73 attack,
//   CC74 cutoff, CC16 extra
//
// The kick is NOT a MIDI voice: it runs on TempoClock inside sclang so the
// grid never inherits Python jitter. The conductor only steers tempo/amp.

(
var voiceDefs, sustained, channels, masterBus, revBus, pianoRev, ccState;
var revSends;
var padGrp, padVoices, padCap;
var chimeGrp, chimeVoices, chimeCap;
var pianoBright, pianoDecay, pianoSend;
var kickAmp = 0.0, kickDensity = 0.5, tempoClock, stingQueue;

"orb_synth_pi booting...".postln;

Server.default.options.numOutputBusChannels = 2;
Server.default.options.memSize = 32768;   // headroom for GVerb delay lines

Server.default.waitForBoot({

    // ================== SynthDefs ==================
    SynthDef(\okick, { |out=0, amp=0.5, tone=0.5, decay=0.35|
        var env = EnvGen.kr(Env.perc(0.002, decay), doneAction: 2);
        var pitchEnv = EnvGen.kr(Env([tone.linlin(0,1,90,160), 45], [0.04], \exp));
        var sig = SinOsc.ar(pitchEnv) * env;
        var click = HPF.ar(WhiteNoise.ar(0.15 * tone), 1500) *
                    EnvGen.kr(Env.perc(0.001, 0.01));
        Out.ar(out, ((sig + click) * amp).dup);
    }).add;

    SynthDef(\obass, { |out=0, rev=99, revsend=0.12, freq=55, amp=0.3, gate=1,
                       cutoff=0.5, reso=0.2, attack=0.05, release=0.6, extra=0.5|
        var env = EnvGen.kr(Env.asr(attack.linlin(0,1,0.01,0.8), 1,
                                    release.linlin(0,1,0.1,2.5)), gate, doneAction: 2);
        var sig = SinOsc.ar(freq) + (Pulse.ar(freq, 0.5) * 0.3 * extra);
        sig = LPF.ar(sig, cutoff.linexp(0, 1, 120, 2000) * (1 + (reso * 0.5)));
        Out.ar(out, (sig * env * amp).dup);
        Out.ar(rev, (sig * env * amp * revsend).dup);
    }).add;

    SynthDef(\operc, { |out=0, freq=800, amp=0.25, gate=1,
                       cutoff=0.6, reso=0.3, attack=0, release=0.2, extra=0.5|
        var env = EnvGen.kr(Env.perc(0.001, release.linlin(0,1,0.03,0.4)),
                            doneAction: 2);
        var sig = BPF.ar(PinkNoise.ar, freq.clip(200, 8000),
                         reso.linlin(0, 1, 1.2, 0.15));
        sig = sig + (SinOsc.ar(freq * 1.7) * 0.2 * extra);
        Out.ar(out, Pan2.ar(sig * env * amp * 3, extra.linlin(0,1,-0.6,0.6)));
    }).add;

    SynthDef(\opluck, { |out=0, rev=99, revsend=0.25, freq=330, amp=0.3, gate=1,
                        cutoff=0.7, reso=0.2, attack=0, release=0.5, extra=0.5|
        var env = EnvGen.kr(Env.perc(0.002, release.linlin(0,1,0.3,2.0)),
                            doneAction: 2);
        var sig = Pluck.ar(PinkNoise.ar(0.7), Impulse.kr(0), 1/40, 1/freq,
                           release.linlin(0,1,1,5), extra.linlin(0,1,0.1,0.6));
        sig = LPF.ar(sig, cutoff.linexp(0,1,800,8000));
        Out.ar(out, (sig * env * amp * 2).dup);
        Out.ar(rev, (sig * env * amp * 2 * revsend).dup);
    }).add;

    SynthDef(\opad, { |out=0, rev=99, revsend=0.7, freq=220, amp=0.25, gate=1,
                      cutoff=0.5, reso=0.2, attack=0.8, release=2.0, extra=0.5|
        var env = EnvGen.kr(Env.asr(attack.linlin(0,1,0.3,3), 1,
                                    release.linlin(0,1,1,5)), gate, doneAction: 2);
        // Triangle-leaning tone: sine core + a measured triangle layer for a
        // hollow edge, plus a sub saw for body. Per-voice detune kept negligible
        // (1.0006) — the throb comes from inter-orb beating instead.
        var sig = (SinOsc.ar(freq) * 0.6
                 + LFTri.ar(freq) * 0.45
                 + SinOsc.ar(freq*1.0006) * 0.3
                 + Saw.ar(freq/2, 0.4) * 0.4);
        sig = LPF.ar(sig, cutoff.linexp(0,1,300,4000) * (1 + reso));
        Out.ar(out, (sig * env * amp * 0.62).dup);
        Out.ar(rev, (sig * env * amp * 0.62 * revsend).dup);
    }).add;

    // Spin "pad" — a SHORT, self-freeing struck-piano note (fire-and-forget, like
    // the chimes). The conductor re-triggers it a few times a second while an orb
    // spins, and the single shared GVerb smears the strikes into a continuous
    // shimmering wash. NO gate, NO note-off: every voice ALWAYS self-frees via
    // doneAction, so a flood of re-triggers (heavy shaking, dropped MIDI) can never
    // orphan or leak a held voice — the root-cause fix for the cutouts. `bright`/
    // `decay`/`revsend` are the per-cluster variant. Mostly wet into the GVerb.
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
        Out.ar(out, (sig * 0.5).dup);          // a little dry...
        Out.ar(rev, sig * revsend);            // ...mostly wet into the shared GVerb
    }).add;

    // Shared lush, very-wet, long-decay reverb for the pianos (one instance).
    SynthDef(\opverb, { |in=0, out=0|
        var s = In.ar(in, 1);
        var w = GVerb.ar(s, 110, 10, 0.4, 0.5, 15, 0, 0.6, 0.75, 300);
        Out.ar(out, w * 0.9);
    }).add;

    // Six jump-accent "chimes" — each cluster randomly picks one (CC#12 on ch5).
    // Dry -> out, a touch -> rev (FreeVerb). All self-free once they ring out.
    // 0 — original glockenspiel bell (inharmonic partials)
    SynthDef(\ochime0, { |out=0, rev=99, revsend=0.35, freq=523, amp=0.4|
        var p = [1.0, 2.76, 4.07, 5.40], pa = [1.0, 0.55, 0.35, 0.22], pd = [1.3, 0.8, 0.55, 0.4];
        var sig = Mix.fill(4, { |i| SinOsc.ar(freq * p[i], 0, pa[i]) * EnvGen.kr(Env.perc(0.001, pd[i])) }) * amp * 0.5;
        DetectSilence.ar(sig, 0.0002, 0.1, doneAction: 2);
        Out.ar(out, sig.dup); Out.ar(rev, (sig * revsend).dup);
    }).add;
    // 1 — glass FM bell, bright
    SynthDef(\ochime1, { |out=0, rev=99, revsend=0.35, freq=523, amp=0.4|
        var env = EnvGen.kr(Env.perc(0.001, 1.0), doneAction: 2);
        var mod = SinOsc.ar(freq * 2.4) * freq * 1.1 * EnvGen.kr(Env.perc(0.001, 0.22));
        var sig = SinOsc.ar(freq + mod) * env * amp;
        Out.ar(out, sig.dup); Out.ar(rev, (sig * revsend).dup);
    }).add;
    // 2 — kalimba, soft & round
    SynthDef(\ochime2, { |out=0, rev=99, revsend=0.3, freq=523, amp=0.5|
        var env = EnvGen.kr(Env.perc(0.005, 0.9), doneAction: 2);
        var click = LPF.ar(WhiteNoise.ar(0.3) * EnvGen.kr(Env.perc(0.001, 0.006)), 2200);
        var sig = (SinOsc.ar(freq) * 0.85 + SinOsc.ar(freq * 2, 0, 0.10) + click) * env * amp;
        Out.ar(out, sig.dup); Out.ar(rev, (sig * revsend).dup);
    }).add;
    // 3 — kalimba, bright & buzzy
    SynthDef(\ochime3, { |out=0, rev=99, revsend=0.3, freq=523, amp=0.5|
        var env = EnvGen.kr(Env.perc(0.002, 0.55), doneAction: 2);
        var click = LPF.ar(WhiteNoise.ar(0.6) * EnvGen.kr(Env.perc(0.001, 0.01)), 4500);
        var sig = (SinOsc.ar(freq) * 0.7 + SinOsc.ar(freq * 2, 0, 0.25) + SinOsc.ar(freq * 3, 0, 0.12) + click) * env * amp;
        Out.ar(out, sig.dup); Out.ar(rev, (sig * revsend).dup);
    }).add;
    // 4 — music box, pure & sweet
    SynthDef(\ochime4, { |out=0, rev=99, revsend=0.35, freq=523, amp=0.45|
        var p = [1, 2, 3], pa = [1, 0.35, 0.18], pd = [1.9, 1.1, 0.7];
        var sig = Mix.fill(3, { |i| SinOsc.ar(freq * p[i], 0, pa[i]) * EnvGen.kr(Env.perc(0.001, pd[i])) }) * amp * 0.55;
        DetectSilence.ar(sig, 0.0001, 0.1, doneAction: 2);
        Out.ar(out, sig.dup); Out.ar(rev, (sig * revsend).dup);
    }).add;
    // 5 — music box, rich & shimmer
    SynthDef(\ochime5, { |out=0, rev=99, revsend=0.35, freq=523, amp=0.4|
        var p = [1, 2, 3, 4.2, 5.4, 6.8], pa = [1, 0.4, 0.25, 0.15, 0.1, 0.07], pd = [1.6, 1.0, 0.7, 0.5, 0.4, 0.3];
        var sig = Mix.fill(6, { |i| SinOsc.ar(freq * p[i], 0, pa[i]) * EnvGen.kr(Env.perc(0.001, pd[i])) });
        var shim = SinOsc.ar(freq * 1.004, 0, 0.3) * EnvGen.kr(Env.perc(0.001, 1.4));
        var mix = (sig + shim) * amp * 0.5;
        DetectSilence.ar(mix, 0.0001, 0.1, doneAction: 2);
        Out.ar(out, mix.dup); Out.ar(rev, (mix * revsend).dup);
    }).add;

    SynthDef(\olead, { |out=0, rev=99, revsend=0.3, freq=440, amp=0.22, gate=1,
                       cutoff=0.6, reso=0.4, attack=0.02, release=0.3, extra=0.5|
        var env = EnvGen.kr(Env.asr(attack.linlin(0,1,0.005,0.3), 1,
                                    release.linlin(0,1,0.05,1)), gate, doneAction: 2);
        var vib = SinOsc.kr(extra.linlin(0,1,2,7), 0, freq*0.008);
        var sig = RLPF.ar(Saw.ar(freq + vib),
                          cutoff.linexp(0,1,400,7000),
                          reso.linlin(0,1,1.0,0.15));
        Out.ar(out, (sig * env * amp).dup);
        Out.ar(rev, (sig * env * amp * revsend).dup);
    }).add;

    SynthDef(\osting, { |out=0, rev=99, revsend=0.45, freq=880, amp=0.4, gate=1,
                        cutoff=0.7, reso=0.2, attack=0, release=0.6, extra=0.5|
        var env = EnvGen.kr(Env.perc(0.001, release.linlin(0,1,0.2,1.2)),
                            doneAction: 2);
        var mod = SinOsc.ar(freq * extra.linlin(0,1,1.5,3.5)) *
                  freq * EnvGen.kr(Env.perc(0.001, 0.15)) * 2;
        var sig = SinOsc.ar(freq + mod);
        sig = LPF.ar(sig, cutoff.linexp(0,1,1000,10000)) * env * amp;
        Out.ar(out, sig.dup);
        Out.ar(rev, (sig * revsend).dup);
    }).add;

    SynthDef(\owin, { |out=0, rev=99, revsend=0.6, freq=523, amp=0.35, gate=1,
                      cutoff=0.8, reso=0.2, attack=0.01, release=2.5, extra=0.5|
        var env = EnvGen.kr(Env.perc(0.01, release.linlin(0,1,1,4)), doneAction: 2);
        var sig = Mix.fill(3, { |i| SinOsc.ar(freq * (i+1) * [1, 1.003].choose, 0, 1/(i+1)) });
        sig = sig + (SinOsc.ar(freq * 2) * EnvGen.kr(Env.perc(0.001, 0.3)) * 0.4);
        Out.ar(out, (sig * env * amp).dup);
        Out.ar(rev, (sig * env * amp * revsend).dup);
    }).add;

    SynthDef(\orevmix, { |out=0, in=0, mix=1.0|
        // Send-return reverb: only voices with a revsend tap feed `in`;
        // kick and percussion stay fully dry for punch.
        var send = In.ar(in, 2);
        var wet = FreeVerb2.ar(send[0], send[1], 1.0, 0.85, 0.5);
        Out.ar(out, wet * mix);
    }).add;

    // Master bus glue: everything (dry voices + reverb return) sums on the
    // master bus, then this synth rides + limits it before the hardware out.
    // Two stages: a gentle bus compressor that DYNAMICALLY pulls down loud
    // moments (a roomful of orbs) smoothly, then a brick-wall limiter as a
    // hard backstop — so it never clips the HDMI codec but quiet passages
    // (1-2 orbs) keep their level instead of everything being squashed flat.
    SynthDef(\omaster, { |out=0, in=0, amp=0.6, thresh=0.4, makeup=1.4|
        var sig = In.ar(in, 2) * amp;
        sig = Compander.ar(sig, sig,
            thresh: thresh,
            slopeBelow: 1.0,        // no change below threshold
            slopeAbove: 0.33,       // ~3:1 compression above
            clampTime: 0.004, relaxTime: 0.15) * makeup;
        sig = Limiter.ar(sig, 0.95, 0.01);
        Out.ar(out, sig);
    }).add;

    Server.default.sync;

    // ================== Routing ==================
    // Voices write to bus 0; one global FreeVerb taps it (kept small for the
    // Pi). Channel -> SynthDef map; nil channels are unused.
    // ch3 (piano pad) and ch5 (chime) are handled specially below as per-cluster
    // voices that pick a variant, so both are nil here (not generic voices).
    voiceDefs = [\obass, \operc, \opluck, nil, \olead, nil, \osting, \owin,
                 nil, nil, nil, nil, nil, nil, nil, nil];
    // Gated ASR voices we must track for noteOff/live-CC; percussive defs
    // (operc/opluck/ochime/osting/owin) free themselves — fire and forget, or
    // the server fills the log with "/n_set Node not found".
    sustained = [true, false, false, true, true, false, false, false,
                 false, false, false, false, false, false, false, false];
    channels = Array.fill(16, { (notes: Dictionary.new) });
    ccState = Array.fill(16, { Dictionary.new });
    // Pads (ch3) and chimes (ch5) are BOTH fire-and-forget short notes now: they
    // self-free, so they can never orphan/leak. Each lives in its own group with a
    // node-count cap (free the oldest beyond the cap) — the pattern that already
    // worked for chimes, now used for pads too. No gate, no note-off, no reaper.
    padGrp = Group.new;            // pad strikes live here (head, before tail FX)
    padVoices = List.new;          // recent pad synths, for oldest-first culling
    padCap = 14;                   // max live pad strikes
    padGrp.freeAll;                // clean slate on (re)boot
    chimeGrp = Group.new;
    chimeVoices = List.new;
    chimeCap = 8;
    chimeGrp.freeAll;

    // Voices and the reverb returns all write to masterBus (not straight to
    // the hardware out 0); the master limiter is the only thing on out 0.
    masterBus = Bus.audio(Server.default, 2);
    revBus = Bus.audio(Server.default, 2);
    pianoRev = Bus.audio(Server.default, 1);          // mono send into the piano GVerb
    // The 6 per-cluster piano variants (timbre + wet amount). Index = CC#12.
    pianoBright = [0.40, 0.75, 0.25, 0.60, 0.30, 0.50];
    // Per-variant SHORT decay (s) for the fire-and-forget pad strike, and how wet it
    // goes into the shared GVerb (mostly wet — the reverb carries the sustain).
    pianoDecay  = [1.2,  0.9,  1.5,  1.0,  1.7,  0.9];
    pianoSend   = [0.85, 0.9,  0.8,  0.85, 0.95, 0.8];
    // Node order matters: reverb returns + master must sit AFTER the voices
    // (which spawn at the group head), so these tail synths in this order.
    Synth.tail(nil, \orevmix, [\in, revBus.index, \out, masterBus.index, \mix, 1.0]);
    Synth.tail(nil, \opverb, [\in, pianoRev.index, \out, masterBus.index]);
    Synth.tail(nil, \omaster, [\in, masterBus.index, \out, 0, \amp, 0.6]);

    // ================== Kick clock ==================
    tempoClock = TempoClock(100/60);
    stingQueue = List.new;

    // Beat 1..n: kick every beat; sting queue flushes every 16th.
    tempoClock.schedAbs(tempoClock.beats.ceil, { |beat|
        if (kickAmp > 0.01) {
            // density gates off-beat ghost kicks at high zones
            Synth(\okick, [\out, masterBus.index,
                           \amp, kickAmp,
                           \tone, kickDensity,
                           \decay, kickDensity.linlin(0,1,0.5,0.25)]);
        };
        1
    });
    tempoClock.schedAbs(tempoClock.beats.ceil, { |beat|
        if (stingQueue.notEmpty) {
            stingQueue.do { |ev|
                Synth(\osting, [\out, masterBus.index, \freq, ev[0].midicps,
                               \amp, ev[1], \rev, revBus.index]);
            };
            stingQueue.clear;
        };
        0.25   // every 16th
    });

    // ================== MIDI ==================
    MIDIClient.init;
    MIDIIn.connectAll;       // bind all sources (robust across SC restarts). This also
                             // grabs our OWN mirror (nOdes-out/f_midi), which echoes
                             // notes back in — but the noteOn guards below accept ONLY
                             // real conductor traffic (ch3 handles 1-24, ch5 chimes),
                             // so the mirror can no longer spawn stray voices.

    MIDIdef.noteOn(\on, { |vel, note, chan|
        var def = voiceDefs[chan];
        var amp = (vel / 127).squared * 0.8;
        // Only ch3 (pad; note = handle 1-24) and ch5 (chime) are real conductor
        // channels. The nOdes-out mirror (also grabbed by connectAll) echoes notes
        // on cluster-slot channels and pitch-range notes on ch3 — the guards below
        // reject all of that so the loopback can't spawn stray voices (obass etc.).
        case
        { chan == 3 } {                       // PAD STRIKE — short, fire-and-forget
            var cc = ccState[chan];
            var idx = ((cc[12] ? 0) * 127).round.clip(0, 5);  // variant CC#12
            var cents = ((cc[9] ? (64/127)) * 127) - 64;      // detune CC#9
            while { padVoices.size >= padCap } {              // bound the strike-flood
                padVoices.removeAt(0).free;                   // free oldest (self-freed -> harmless)
            };
            padVoices.add(Synth(\opiano, [
                \out, masterBus.index, \rev, pianoRev.index,
                \freq, note.midicps * (2 ** (cents / 1200)), \amp, amp,
                \bright, pianoBright[idx], \decay, pianoDecay[idx], \revsend, pianoSend[idx],
            ], padGrp));
        }
        { chan == 5 } {                       // jump chime: cluster picks 1 of 6 timbres
            var cc = ccState[chan];
            var idx = ((cc[12] ? 0) * 127).round.clip(0, 5);  // variant CC#12
            var cents = ((cc[9] ? (64/127)) * 127) - 64;      // detune CC#9
            var defs = [\ochime0, \ochime1, \ochime2, \ochime3, \ochime4, \ochime5];
            while { chimeVoices.size >= chimeCap } {   // bound the shake-flood
                chimeVoices.removeAt(0).free;          // free oldest (self-freed -> harmless)
            };
            chimeVoices.add(Synth(defs[idx], [\out, masterBus.index, \rev, revBus.index,
                \freq, note.midicps * (2 ** (cents / 1200)), \amp, amp], chimeGrp));
        };
    });

    MIDIdef.noteOff(\off, { |vel, note, chan|
        // Pads (ch3) and chimes (ch5) are fire-and-forget — they self-free and need
        // no note-off, and nothing else here is gated. Note-offs are simply ignored.
    });

    MIDIdef.cc(\cc, { |val, num, chan|
        var v = val / 127;
        if (chan == 15) {
            case
            { num == 20 } { tempoClock.tempo = v.linlin(0, 1, 20, 180) / 60 }
            { num == 21 } { kickAmp = v }
            { num == 24 } { kickDensity = v };
        } {
            if (num == 123) {   // CC123: all notes off (conductor hygiene)
                channels[chan].notes.do { |syn| syn.set(\gate, 0) };
                channels[chan].notes.clear;
                (chan == 3).if { padGrp.freeAll; padVoices.clear };
            };
            ccState[chan][num] = v;
            // live-update sustained voices
            channels[chan].notes.do { |syn|
                case
                { num == 74 } { syn.set(\cutoff, v) }
                { num == 71 } { syn.set(\reso, v) }
                { num == 16 } { syn.set(\extra, v) };
            };
        };
    });

    "orb_synth_pi ready — kick clock armed, MIDI bound.".postln;
});
)
