// nOdes 7.1 spatial voice engine  —  SuperCollider sketch
// -----------------------------------------------------------------------------
// Receives per-source 8-channel gain vectors from spatial_router.py over OSC and
// routes one mono voice per source through that gain matrix into the 7.1 output.
//
// Channel order matches the StarTech ICUSBAUDIO7D card map (do NOT reorder):
//     0:FL 1:FR 2:FC 3:LFE 4:RL 5:RR 6:SL 7:SR
//
// Run headless against PipeWire's JACK API, e.g.:
//     pw-jack sclang orb_spatial.scd
// then point the router at us:   python3 spatial_router.py --osc 127.0.0.1:57120
//
// This is a scaffold: one simple voice + the spatial plumbing. Swap the SynthDef
// guts for the real nOdes timbres (mirror orb_synth_pi.scd) once the routing feels
// right. Keep it under the same "each cluster a note / spin a pad / jump a chime"
// model — here each source just carries whatever it's given, placed in space.
// -----------------------------------------------------------------------------

s.options.numOutputBusChannels = 8;   // 7.1
s.options.numInputBusChannels  = 2;
s.waitForBoot({

    // --- a spatial voice: mono source × 8-ch gain vector, gains smoothed on-server
    SynthDef(\orbVoice, { |out = 0, freq = 220, amp = 0.2, gate = 1|
        var sig, gains, env;
        env  = EnvGen.kr(Env.asr(0.05, 1, 0.4), gate, doneAction: 2);
        // placeholder tone — replace with the real nOdes voice
        sig  = SinOsc.ar(freq) * 0.5 + Saw.ar(freq * 1.001) * 0.2;
        sig  = sig * env * amp;
        // \gains is an 8-element control set live from OSC; lag kills zipper noise
        gains = Lag.kr(\gains.kr(Array.fill(8, 0)), 0.1);
        Out.ar(out, sig ! 8 * gains);
    }).add;

    s.sync;

    // --- one persistent voice per source slot, created on first sight
    ~voices = IdentityDictionary.new;

    ~voiceFor = { |slot|
        ~voices[slot] ?? {
            ("orb_spatial: + voice for source " ++ slot).postln;
            ~voices[slot] = Synth(\orbVoice, [\freq, 180 + (slot * 7 % 400), \amp, 0.15]);
        };
        ~voices[slot];
    };

    // --- OSC in:  /orb/gains  i f f f f f f f f   (slot, then 8 gains)
    // Fixed literal path. SC's OSC dispatch is exact-match on the receiver
    // side (pattern matching applies to sender-side wildcards only), so the
    // earlier /orb/source/<slot>/gains template could never be delivered.
    OSCdef(\gains, { |msg|
        var slot  = msg[1].asInteger;
        var gains = msg[2..9];
        ~voiceFor.(slot).set(\gains, gains);
    }, '/orb/gains');

    ("orb_spatial: listening on " ++ NetAddr.langPort ++ " for /orb/gains").postln;
});

// Idle-voice reaping (a source gone silent for a while) is left as a TODO — see
// ROADMAP "voice lifecycle". For a first run the handful of voices is harmless.
