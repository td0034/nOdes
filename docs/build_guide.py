#!/usr/bin/env python3
"""build_guide.py — generate the nOdes field guide in English and Italian.

One content model, two outputs. Maintaining two hand-written translations of a
12-page illustrated manual guarantees they drift; here every string lives once,
side by side with its translation, and both PDFs are rendered from the same
page code. Change the English and the Italian is right next to it.

    python3 docs/build_guide.py            # writes the two HTML files
    python3 docs/build_guide.py --pdf      # ...and renders both to PDF

Merges what used to be EGGBOX_GUIDE + SIMULATOR_GUIDE into a single guide, so
the artists have one document per language rather than four files.

Line-art conventions live in docs/guide.css: .ln normal, .lnT thin,
.ctx greyed-out context, .act the one thing this step asks you to touch.
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# ---------------------------------------------------------------------------
# Content. EN first, IT second — keep them adjacent so they cannot drift.
# TouchDesigner operator names (OSC In CHOP, WebSocket DAT, Network Port) stay
# in English on purpose: that is what the artists see in the TD interface.
# ---------------------------------------------------------------------------

T = {
"lang_tag":        ("en", "it"),
"doc_title":       ("nOdes — Field Guide", "nOdes — Guida sul Campo"),
"foot_name":       ("nOdes", "nOdes"),

# -- cover
"cover_title":     ("nOdes", "nOdes"),
"cover_sub":       ("Eggbox field kit — setup, data &amp; TouchDesigner",
                    "Kit da campo eggbox — installazione, dati e TouchDesigner"),
"cover_f1":        ("No tools required", "Nessun attrezzo necessario"),
"cover_f2":        ("~3 minutes to first data", "~3 minuti al primo dato"),
"cover_f3":        ("Works with no internet", "Funziona senza internet"),
"cover_meta":      ("Start on page 3 — you do not need the box yet",
                    "Inizia da pagina 3 — la valigetta non serve ancora"),
"cover_warn":      ("Read the safety note on page 2 before charging",
                    "Leggi la nota di sicurezza a pagina 2 prima di ricaricare"),

# -- page 2: parts + safety
"p2_kick":         ("Before you start", "Prima di iniziare"),
"p2_title":        ("What is in the box", "Cosa c'è nella valigetta"),
"p2_lede":         ("Check everything is present. The eggbox case holds the whole "
                    "system — a small computer, a screen, and six wireless charging wells.",
                    "Controlla che ci sia tutto. La valigetta eggbox contiene l'intero "
                    "sistema: un piccolo computer, uno schermo e sei alloggiamenti "
                    "di ricarica wireless."),
"p2_case":         ("Eggbox case<br>(computer + screen in the lid)",
                    "Valigetta eggbox<br>(computer + schermo nel coperchio)"),
"p2_orbs":         ("Orbs<br>(70&nbsp;mm, serial printed on each)",
                    "Sfere<br>(70&nbsp;mm, numero di serie stampato su ognuna)"),
"p2_psu_usb":      ("USB-C power supply<br>(runs the computer)",
                    "Alimentatore USB-C<br>(alimenta il computer)"),
"p2_psu_chg":      ("Charger power supply<br>(runs the six wells)",
                    "Alimentatore dei caricatori<br>(alimenta i sei alloggiamenti)"),
"p2_cable":        ("USB-C cable<br>(your laptop → eggbox)",
                    "Cavo USB-C<br>(il tuo computer → eggbox)"),
"p2_laptop":       ("Your laptop<br>(not supplied)",
                    "Il tuo computer<br>(non incluso)"),
"p2_flat":         ("flat / oval", "piatto / ovale"),
"p2_round":        ("round", "rotondo"),
"p2_safety_t":     ("Safety — charging gets hot", "Sicurezza — la ricarica scalda"),
"p2_safety_1":     ("<strong>Orbs become quite hot while charging.</strong> This is "
                    "normal for wireless charging, but it means charging must be "
                    "<strong>attended</strong>.",
                    "<strong>Le sfere diventano piuttosto calde durante la "
                    "ricarica.</strong> È normale per la ricarica wireless, ma "
                    "significa che la ricarica deve avvenire <strong>solo in "
                    "presenza di qualcuno</strong>."),
"p2_safety_2":     ("Never leave the charger supply running unattended — not "
                    "overnight, and not while the room is empty. Switch the charger "
                    "supply off when you leave.",
                    "Non lasciare mai l'alimentatore dei caricatori acceso senza "
                    "sorveglianza: né di notte, né quando la stanza è vuota. "
                    "Spegni l'alimentatore quando esci."),
"p2_safety_3":     ("Keep the lid open while charging so heat can escape, and do "
                    "not cover the case with fabric or paper.",
                    "Tieni il coperchio aperto durante la ricarica per far uscire "
                    "il calore e non coprire la valigetta con tessuti o carta."),
"p2_safety_4":     ("A warm orb is fine. An orb too hot to hold comfortably is not "
                    "— take it off the charger and let it cool.",
                    "Una sfera tiepida è normale. Una sfera troppo calda da tenere "
                    "in mano no: toglila dal caricatore e lasciala raffreddare."),
"p2_routes_t":     ("Two ways to connect. Prefer the cable.",
                    "Due modi per collegarsi. Meglio il cavo."),
"p2_th_route":     ("Route", "Collegamento"),
"p2_th_addr":      ("Address", "Indirizzo"),
"p2_th_when":      ("When to use it", "Quando usarlo"),
"p2_r1":           ("USB-C cable", "Cavo USB-C"),
"p2_r1d":          ("<strong>Preferred.</strong> Faster, more reliable, and it does "
                    "not use up one of the station's scarce Wi-Fi seats.",
                    "<strong>Consigliato.</strong> Più veloce, più affidabile e non "
                    "occupa uno dei pochi posti Wi-Fi della stazione."),
"p2_r2":           ("Wi-Fi hotspot", "Hotspot Wi-Fi"),
"p2_r2d":          ("Only when a cable is impractical. The radio holds about "
                    "<strong>eight</strong> devices in total and the orbs need those "
                    "seats — every laptop that joins may cost you an orb.",
                    "Solo se il cavo non è praticabile. La radio regge circa "
                    "<strong>otto</strong> dispositivi in tutto e le sfere hanno "
                    "bisogno di quei posti: ogni computer che si collega può "
                    "costarti una sfera."),

# -- page 3: simulator
"p3_kick":         ("Start here", "Inizia da qui"),
"p3_title":        ("Build without the box", "Lavora senza la valigetta"),
"p3_lede":         ("Six imaginary orbs run on your own laptop and publish exactly "
                    "what the real station publishes. Build your patch now; when the "
                    "eggbox arrives you change one address and it works.",
                    "Sei sfere immaginarie girano sul tuo computer e pubblicano "
                    "esattamente ciò che pubblica la stazione reale. Costruisci il "
                    "tuo patch adesso: quando arriva l'eggbox cambi un indirizzo e "
                    "funziona."),
"p3_s1":           ("Check you have <strong>Python 3</strong>.",
                    "Verifica di avere <strong>Python 3</strong>."),
"p3_s1h":          ("Version 3.8 or newer. Nothing else to install.<br>"
                    "macOS — <code>xcode-select --install</code><br>"
                    "Windows — install from <code>python.org</code>",
                    "Versione 3.8 o successiva. Nient'altro da installare.<br>"
                    "macOS — <code>xcode-select --install</code><br>"
                    "Windows — installa da <code>python.org</code>"),
"p3_s2":           ("In a terminal, go to the <code>simulator</code> folder and run it.",
                    "In un terminale, entra nella cartella <code>simulator</code> ed eseguilo."),
"p3_s2h":          ("It prints the addresses and keeps running. Leave it open; "
                    "<strong>Ctrl-C</strong> stops it. More orbs: <code>./run.sh 12</code>. "
                    "To test docked orbs: <code>./run.sh 6 --charging 2</code>.",
                    "Stampa gli indirizzi e resta in esecuzione. Lascialo aperto; "
                    "<strong>Ctrl-C</strong> lo ferma. Più sfere: <code>./run.sh 12</code>. "
                    "Per provare le sfere in carica: <code>./run.sh 6 --charging 2</code>."),
"p3_s2w":          ("On Windows, or if <code>./run.sh</code> will not execute, run the "
                    "two parts in two terminals:",
                    "Su Windows, o se <code>./run.sh</code> non parte, esegui le due "
                    "parti in due terminali:"),
"p3_s3":           ("Open a browser at <code>http://127.0.0.1:8080</code>",
                    "Apri il browser su <code>http://127.0.0.1:8080</code>"),
"p3_s3h":          ("A live table of six orbs, refreshing every two seconds. "
                    "<strong>If this page works, everything else in this guide will "
                    "work.</strong>",
                    "Una tabella dal vivo con sei sfere, aggiornata ogni due secondi. "
                    "<strong>Se questa pagina funziona, funzionerà tutto il resto di "
                    "questa guida.</strong>"),

# -- page 4: power
"p4_kick":         ("The real box", "La valigetta vera"),
"p4_title":        ("Power up", "Accensione"),
"p4_lede":         ("The two power supplies are independent. The USB-C one runs the "
                    "computer; the other runs the six charging wells.",
                    "I due alimentatori sono indipendenti. Quello USB-C alimenta il "
                    "computer, l'altro i sei alloggiamenti di ricarica."),
"p4_s1":           ("Plug the <strong>USB-C power supply</strong> into the port on the "
                    "side of the case, then into the wall.",
                    "Collega l'<strong>alimentatore USB-C</strong> alla presa sul "
                    "fianco della valigetta, poi alla corrente."),
"p4_s1h":          ("The screen in the lid lights up on its own. Nothing to press.",
                    "Lo schermo nel coperchio si accende da solo. Non c'è nulla da premere."),
"p4_wait":         ("Wait about <strong>60 seconds</strong> for the station to start",
                    "Aspetta circa <strong>60 secondi</strong> che la stazione si avvii"),
"p4_s2":           ("Plug the <strong>charger power supply</strong> into the round "
                    "socket, then into the wall.",
                    "Collega l'<strong>alimentatore dei caricatori</strong> alla presa "
                    "rotonda, poi alla corrente."),
"p4_s2h":          ("Only do this while someone is present — see the safety note on "
                    "page 2. Switch it off when you leave the room.",
                    "Fallo solo se c'è qualcuno presente — vedi la nota di sicurezza "
                    "a pagina 2. Spegnilo quando esci dalla stanza."),
"p4_s3":           ("Lift an orb out and <strong>shake it</strong> to wake it.",
                    "Prendi una sfera e <strong>scuotila</strong> per svegliarla."),
"p4_s3h":          ("It joins within a couple of seconds. Repeat for as many orbs as "
                    "you want live.",
                    "Si collega in un paio di secondi. Ripeti per tutte le sfere che "
                    "vuoi attive."),
"p4_s3h2":         ("<strong>An orb that is not charging goes to sleep after 30 "
                    "seconds of stillness</strong> and disappears from the data until "
                    "shaken. This is normal.",
                    "<strong>Una sfera che non è in carica va in standby dopo 30 "
                    "secondi di immobilità</strong> e sparisce dai dati finché non "
                    "viene scossa. È normale."),
"p4_ok":           ("Leave orbs in the wells between sessions, with the charger on and "
                    "someone present. While actually charging they stay awake.",
                    "Lascia le sfere negli alloggiamenti tra una sessione e l'altra, "
                    "con il caricatore acceso e qualcuno presente. Mentre sono "
                    "davvero in carica restano sveglie."),
"p4_no":           ("Do not power the station from a laptop USB port if you can avoid "
                    "it. A weak port browns the computer out and the link dies. Use "
                    "the supplied wall supply.",
                    "Evita di alimentare la stazione da una porta USB del computer. "
                    "Una porta debole fa calare la tensione e il collegamento cade. "
                    "Usa l'alimentatore da parete in dotazione."),

# -- page 5: connect
"p5_kick":         ("The real box", "La valigetta vera"),
"p5_title":        ("Connect your laptop", "Collega il computer"),
"p5_lede":         ("Pick <strong>one</strong>. Route A is better in every way; use B "
                    "only if a cable will not reach.",
                    "Scegli <strong>uno</strong> dei due. Il metodo A è migliore sotto "
                    "ogni aspetto; usa il B solo se il cavo non arriva."),
"p5_a":            ("<strong>Route A — USB-C cable.</strong> Connect laptop to eggbox. "
                    "That is the whole step.",
                    "<strong>Metodo A — cavo USB-C.</strong> Collega il computer "
                    "all'eggbox. Il passaggio è tutto qui."),
"p5_ah":           ("The station appears as a wired network device. Leave your network "
                    "settings on automatic — your normal Wi-Fi and internet keep "
                    "working. macOS and Windows need no driver.",
                    "La stazione appare come un dispositivo di rete via cavo. Lascia "
                    "le impostazioni di rete su automatico: il tuo Wi-Fi e internet "
                    "continuano a funzionare. macOS e Windows non richiedono driver."),
"p5_at":           ("The station is at", "La stazione si trova a"),
"p5_b":            ("<strong>Route B — Wi-Fi hotspot.</strong> Join the network the "
                    "station broadcasts.",
                    "<strong>Metodo B — hotspot Wi-Fi.</strong> Collegati alla rete "
                    "trasmessa dalla stazione."),
"p5_net":          ("Network", "Rete"),
"p5_pass":         ("Password", "Password"),
"p5_stat":         ("Station at", "Stazione a"),
"p5_bh":           ("This network has <strong>no internet</strong> — that is normal. "
                    "Your laptop may warn you; ignore it.",
                    "Questa rete <strong>non ha internet</strong>: è normale. Il "
                    "computer potrebbe avvisarti; ignora l'avviso."),
"p5_c":            ("Open a browser and go to the station's page.",
                    "Apri il browser e vai alla pagina della stazione."),
"p5_cable":        ("(cable)", "(cavo)"),
"p5_wifi":         ("(Wi-Fi)", "(Wi-Fi)"),
"p5_ch":           ("A live table of orbs, refreshing every two seconds. <strong>If "
                    "this page works, everything else will work.</strong>",
                    "Una tabella dal vivo delle sfere, aggiornata ogni due secondi. "
                    "<strong>Se questa pagina funziona, funzionerà tutto il "
                    "resto.</strong>"),

# -- page 6: sound out
"p6s_kick":        ("Sound", "Audio"),
"p6s_title":       ("Getting sound out", "Far uscire l'audio"),
"p6s_lede":        ("The orbs make their own sound, and the station also produces a "
                    "full mix of the whole group. That mix comes out of a 3.5&nbsp;mm "
                    "jack — but not the one you expect.",
                    "Le sfere producono il proprio suono e la stazione genera anche un "
                    "mix completo dell'intero gruppo. Quel mix esce da un jack da "
                    "3,5&nbsp;mm — ma non quello che ti aspetti."),
"p6s_s1":          ("Plug speakers or headphones into the 3.5&nbsp;mm socket "
                    "<strong>on the screen</strong>, in the lid.",
                    "Collega casse o cuffie alla presa da 3,5&nbsp;mm "
                    "<strong>sullo schermo</strong>, nel coperchio."),
"p6s_warn":        ("<strong>Not the socket on the small computer.</strong> That one is "
                    "switched off in software and will always be silent, no matter what "
                    "you plug into it. The sound travels to the screen through the "
                    "internal video cable.",
                    "<strong>Non la presa sul piccolo computer.</strong> Quella è "
                    "disattivata via software e resterà sempre muta, qualunque cosa tu "
                    "ci colleghi. Il suono arriva allo schermo attraverso il cavo video "
                    "interno."),
"p6s_s1h":         ("Powered speakers work best. Set the volume on the speakers "
                    "themselves — the station has no volume control of its own.",
                    "Le casse amplificate funzionano meglio. Regola il volume sulle "
                    "casse stesse: la stazione non ha un proprio controllo di volume."),
"p6s_s2":          ("Want to drive your own instruments instead? The same USB-C cable "
                    "also carries <strong>MIDI</strong>.",
                    "Vuoi invece pilotare i tuoi strumenti? Lo stesso cavo USB-C porta "
                    "anche il <strong>MIDI</strong>."),
"p6s_s2h":         ("Your laptop sees a MIDI device called <strong>orbstation</strong> "
                    "with no setup. Select it as a MIDI input in TouchDesigner, Ableton "
                    "or anything else.",
                    "Il tuo computer vede un dispositivo MIDI chiamato "
                    "<strong>orbstation</strong> senza alcuna configurazione. "
                    "Selezionalo come ingresso MIDI in TouchDesigner, Ableton o "
                    "qualsiasi altro programma."),
"p6s_midi_t":      ("What arrives on MIDI", "Cosa arriva via MIDI"),
"p6s_m1":          ("MIDI channel", "Canale MIDI"),
"p6s_m1d":         ("the group (cluster) — 1st group → channel 1, 2nd → channel 2, and "
                    "so on. Route each to a different instrument.",
                    "il gruppo (cluster) — 1° gruppo → canale 1, 2° → canale 2, e così "
                    "via. Instrada ognuno su uno strumento diverso."),
"p6s_m2":          ("Low notes (around C3)", "Note basse (intorno al Do3)"),
"p6s_m2d":         ("a swelling pad — an orb being spun",
                    "un pad che cresce — una sfera che ruota"),
"p6s_m3":          ("High notes (around C5)", "Note alte (intorno al Do5)"),
"p6s_m3d":         ("a chime — an orb being jolted or thrown",
                    "un rintocco — una sfera scossa o lanciata"),
"p6s_tip":         ("So you can take the station's own mix from the screen's jack, or "
                    "ignore it entirely and build your own sound from the MIDI. Both at "
                    "once is fine.",
                    "Puoi quindi prendere il mix della stazione dal jack dello schermo, "
                    "oppure ignorarlo del tutto e costruire il tuo suono dal MIDI. "
                    "Entrambe le cose insieme vanno bene."),

# -- page 7: charging vs display  (the surprise)
"p6_kick":         ("Important", "Importante"),
"p6_title":        ("Charging orbs vanish from the screen",
                    "Le sfere in carica spariscono dallo schermo"),
"p6_lede":         ("This surprises everyone, so it is worth knowing before it "
                    "confuses you.",
                    "Questo sorprende tutti, quindi è meglio saperlo prima che ti "
                    "confonda."),
"p6_body":         ("The station's own display — the screen in the lid, and the "
                    "proximity graph on it — <strong>deliberately hides any orb that "
                    "is sitting on a charger</strong>. So a case full of charging orbs "
                    "shows an empty screen. Nothing is broken.",
                    "Il display della stazione — lo schermo nel coperchio, con il "
                    "grafico di prossimità — <strong>nasconde di proposito ogni sfera "
                    "appoggiata su un caricatore</strong>. Quindi una valigetta piena "
                    "di sfere in carica mostra uno schermo vuoto. Non è un guasto."),
"p6_body2":        ("<strong>But they are still in the data.</strong> Charging orbs "
                    "come through to TouchDesigner as normal, flagged so you can "
                    "choose what to do with them.",
                    "<strong>Ma sono comunque nei dati.</strong> Le sfere in carica "
                    "arrivano normalmente a TouchDesigner, contrassegnate così puoi "
                    "decidere cosa farne."),
"p6_th_w":         ("Where", "Dove"),
"p6_th_s":         ("A charging orb…", "Una sfera in carica…"),
"p6_w1":           ("Screen in the lid", "Schermo nel coperchio"),
"p6_s1":           ("is hidden entirely", "è completamente nascosta"),
"p6_w2":           ("Your TouchDesigner patch", "Il tuo patch TouchDesigner"),
"p6_s2":           ("arrives as normal, with <code>charging</code> = 1",
                    "arriva normalmente, con <code>charging</code> = 1"),
"p6_w3":           ("The <code>/</code> status page", "La pagina di stato <code>/</code>"),
"p6_s3":           ("is listed, marked <em>charging</em>",
                    "è elencata, indicata come <em>charging</em>"),
"p6_tip":          ("So: to see orbs on the screen in the lid, take them out of the "
                    "wells. To decide in your patch, filter on <code>charging</code>.",
                    "Quindi: per vedere le sfere sullo schermo nel coperchio, toglile "
                    "dagli alloggiamenti. Per decidere nel tuo patch, filtra su "
                    "<code>charging</code>."),
"p6_test":         ("You can test this without the box: <code>./run.sh 6 --charging 2</code> "
                    "marks two simulated orbs as docked.",
                    "Puoi provarlo senza la valigetta: <code>./run.sh 6 --charging 2</code> "
                    "segna due sfere simulate come in carica."),

# -- page 7: OSC
"p7_kick":         ("TouchDesigner", "TouchDesigner"),
"p7_title":        ("Route 1 — OSC, no code", "Metodo 1 — OSC, senza codice"),
"p7_lede":         ("The quickest way in. Two steps, no scripting, every orb as "
                    "ordinary CHOP channels. Start here.",
                    "Il modo più rapido. Due passaggi, nessuno script, ogni sfera "
                    "come normali canali CHOP. Inizia da qui."),
"p7_s1":           ("Visit this address once, in any browser:",
                    "Visita questo indirizzo una volta, da qualsiasi browser:"),
"p7_s1h":          ("This tells the station to stream to <em>your</em> machine. It is "
                    "<strong>permanent</strong> — you can close the browser. Use "
                    "<code>127.0.0.1</code> instead for the simulator.",
                    "Questo dice alla stazione di trasmettere al <em>tuo</em> computer. "
                    "È <strong>permanente</strong>: puoi chiudere il browser. Usa "
                    "<code>127.0.0.1</code> per il simulatore."),
"p7_s2":           ("In TouchDesigner, add an <strong>OSC In CHOP</strong> and set "
                    "<strong>Network Port</strong> to <code>7000</code>.",
                    "In TouchDesigner, aggiungi un <strong>OSC In CHOP</strong> e "
                    "imposta <strong>Network Port</strong> su <code>7000</code>."),
"p7_s2h":          ("Channels appear straight away. Nothing else to configure.",
                    "I canali appaiono subito. Non c'è altro da configurare."),
"p7_get":          ("What you get", "Cosa ottieni"),
"p7_th_c":         ("Channel", "Canale"),
"p7_th_m":         ("Meaning", "Significato"),
"p7_c1":           ("Accelerometer x, y, z — in g, gravity included",
                    "Accelerometro x, y, z — in g, gravità inclusa"),
"p7_c2":           ("Gyroscope x, y, z — degrees per second",
                    "Giroscopio x, y, z — gradi al secondo"),
"p7_c3":           ("One number for \"is it spinning\"",
                    "Un solo numero per \"sta ruotando?\""),
"p7_c4":           ("One number for \"is it being shaken\"",
                    "Un solo numero per \"la stanno scuotendo?\""),
"p7_c5":           ("−1 to 1, where 1 is upright",
                    "da −1 a 1, dove 1 è dritta"),
"p7_c6":           ("Which group the station has put it in (−1 = none)",
                    "In quale gruppo l'ha messa la stazione (−1 = nessuno)"),
"p7_c7":           ("1 while the orb is sat in a well",
                    "1 mentre la sfera è in un alloggiamento"),
"p7_c8":           ("Volts. 3.0 is empty, 4.2 is full",
                    "Volt. 3.0 è scarica, 4.2 è carica"),
"p7_c9":           ("<strong>How strongly those two orbs hear each other</strong> — 0 to 1",
                    "<strong>Quanto forte quelle due sfere si sentono</strong> — da 0 a 1"),
"p7_c10":          ("How many orbs are live, and the station's tick rate",
                    "Quante sfere sono attive e la frequenza della stazione"),
"p7_serial":       ("<code>a1b2c3</code> is an orb's serial number, printed on the orb "
                    "itself. It never changes, so you can safely point your patch at "
                    "one particular orb.",
                    "<code>a1b2c3</code> è il numero di serie di una sfera, stampato "
                    "sulla sfera stessa. Non cambia mai, quindi puoi tranquillamente "
                    "puntare il patch su una sfera precisa."),

# -- page 8: websocket + proximity
"p8_kick":         ("TouchDesigner", "TouchDesigner"),
"p8_title":        ("Route 2 — WebSocket, full data",
                    "Metodo 2 — WebSocket, dati completi"),
"p8_lede":         ("Use this when you want the complete picture — in particular the "
                    "<strong>whole proximity grid</strong> as a fixed matrix. Three "
                    "ready-made scripts are supplied in <code>touchdesigner/</code>.",
                    "Usalo quando vuoi il quadro completo — in particolare "
                    "<strong>l'intera griglia di prossimità</strong> come matrice "
                    "fissa. Tre script pronti sono in <code>touchdesigner/</code>."),
"p8_add":          ("Add a <strong>WebSocket DAT</strong>.",
                    "Aggiungi un <strong>WebSocket DAT</strong>."),
"p8_paste":        ("Paste <code>websocket_callbacks.py</code> into its callbacks. Then "
                    "add two Script CHOPs beside it named <code>orbs</code> and "
                    "<code>proximity</code>, and paste the two matching scripts.",
                    "Incolla <code>websocket_callbacks.py</code> nelle sue callback. "
                    "Poi aggiungi due Script CHOP accanto, chiamati <code>orbs</code> "
                    "e <code>proximity</code>, e incolla i due script corrispondenti."),
"p8_prox_t":       ("Understanding proximity", "Capire la prossimità"),
"p8_prox_1":       ("Each orb <strong>listens for every other orb</strong> on a "
                    "short-range radio and reports how strongly it hears them. That is "
                    "the proximity data. It is measured by the orbs themselves — not "
                    "worked out from positions, and nothing to do with the Wi-Fi signal.",
                    "Ogni sfera <strong>ascolta tutte le altre sfere</strong> su una "
                    "radio a corto raggio e riferisce quanto forte le sente. Questi "
                    "sono i dati di prossimità. Sono misurati dalle sfere stesse — non "
                    "ricavati da posizioni, e non hanno nulla a che vedere con il Wi-Fi."),
"p8_prox_2":       ("<strong>0</strong> = cannot hear it &nbsp;·&nbsp; <strong>255</strong> "
                    "(or 1.0 normalised) = touching. It is <strong>not a distance</strong>: "
                    "it falls off unevenly, it is noisy, and bodies between two orbs "
                    "weaken it. Treat it as <em>togetherness</em>, not metres.",
                    "<strong>0</strong> = non la sente &nbsp;·&nbsp; <strong>255</strong> "
                    "(o 1.0 normalizzato) = a contatto. <strong>Non è una "
                    "distanza</strong>: cala in modo irregolare, è rumorosa, e i corpi "
                    "fra due sfere la indeboliscono. Consideralo un indice di "
                    "<em>vicinanza sociale</em>, non metri."),
"p8_sat":          ("<strong>Expect saturation.</strong> On the firmware currently in "
                    "the fleet, orbs closer than about half a metre all report near "
                    "255. A tight huddle therefore looks uniformly \"touching\" with "
                    "little structure inside it. Spread the orbs out to see the "
                    "gradient.",
                    "<strong>Aspettati la saturazione.</strong> Con il firmware "
                    "attualmente installato, le sfere a meno di circa mezzo metro "
                    "riportano tutte valori vicini a 255. Un gruppo stretto appare "
                    "quindi uniformemente \"a contatto\", con poca struttura interna. "
                    "Allontana le sfere per vedere il gradiente."),
"p8_ok":           ("Key everything on the orb's <strong>serial</strong> "
                    "(<code>a1b2c3</code>). It is printed on the orb and never changes.",
                    "Basa tutto sul <strong>numero di serie</strong> della sfera "
                    "(<code>a1b2c3</code>). È stampato sulla sfera e non cambia mai."),
"p8_no":           ("Do not rely on <strong>slot numbers</strong> or a fixed orb count. "
                    "Orbs join and drop out mid-session, and slots are reassigned on "
                    "restart.",
                    "Non fidarti dei <strong>numeri di slot</strong> né di un numero "
                    "fisso di sfere. Le sfere entrano ed escono durante la sessione e "
                    "gli slot vengono riassegnati al riavvio."),

# -- page 9: real vs simulated
"p9_kick":         ("Important", "Importante"),
"p9_title":        ("Simulated versus real", "Simulato contro reale"),
"p9_lede":         ("The program serving the simulated data is <strong>the same one "
                    "the real station runs</strong> — only the orbs are imaginary. "
                    "Every field name and address is identical. But imaginary orbs "
                    "behave too well.",
                    "Il programma che serve i dati simulati è <strong>lo stesso che "
                    "gira sulla stazione reale</strong>: solo le sfere sono "
                    "immaginarie. Ogni nome di campo e indirizzo è identico. Ma le "
                    "sfere immaginarie si comportano troppo bene."),
"p9_right_t":      ("What the simulator gets right",
                    "Cosa riproduce bene il simulatore"),
"p9_right":        ("Clusters genuinely form, split and merge. Proximity saturates "
                    "close-up exactly as the real firmware does. Orbs cycle through "
                    "rest, shake and spin, and occasionally jump. Battery, signal, "
                    "firmware version and packet timing all match the real ranges.",
                    "I gruppi si formano, si dividono e si uniscono davvero. La "
                    "prossimità satura da vicino esattamente come il firmware reale. "
                    "Le sfere alternano riposo, scuotimento e rotazione, e ogni tanto "
                    "saltano. Batteria, segnale, versione del firmware e tempi dei "
                    "pacchetti rientrano tutti negli intervalli reali."),
"p9_wrong_t":      ("What it gets wrong — design for these",
                    "Cosa non riproduce — progetta tenendone conto"),
"p9_th_n":         ("Not simulated", "Non simulato"),
"p9_th_w":         ("Why it matters on the day", "Perché conta il giorno stesso"),
"p9_w1":           ("Orbs dropping out", "Sfere che spariscono"),
"p9_w1d":          ("A real orb that is not charging sleeps after 30 seconds of "
                    "stillness and vanishes until shaken. Your patch must survive orbs "
                    "coming and going. <strong>This is the one that catches people "
                    "out.</strong>",
                    "Una sfera reale che non è in carica va in standby dopo 30 secondi "
                    "di immobilità e sparisce finché non viene scossa. Il tuo patch "
                    "deve sopravvivere alle sfere che vanno e vengono. <strong>È "
                    "questo che frega tutti.</strong>"),
"p9_w2":           ("Packet loss", "Perdita di pacchetti"),
"p9_w2d":          ("Signal is perfect here. A room full of bodies on a congested "
                    "2.4&nbsp;GHz band is not. Smooth your inputs.",
                    "Qui il segnale è perfetto. Una stanza piena di persone su una "
                    "banda 2.4&nbsp;GHz congestionata no. Smorza i tuoi ingressi."),
"p9_w3":           ("Battery draining", "Scaricamento della batteria"),
"p9_w3d":          ("Voltages are static — nothing drains or fills. Do not build a "
                    "piece that depends on battery level without testing on hardware.",
                    "Le tensioni sono statiche: nulla si scarica o si ricarica. Non "
                    "costruire un'opera che dipenda dal livello di batteria senza "
                    "provarla sull'hardware."),
"p9_w4":           ("Heat", "Calore"),
"p9_w4d":          ("Real orbs warm up in the wells and the case needs air. Plan for "
                    "attended charging breaks in a long installation.",
                    "Le sfere reali si scaldano negli alloggiamenti e la valigetta ha "
                    "bisogno d'aria. In un'installazione lunga, prevedi pause di "
                    "ricarica sorvegliate."),
"p9_ok":           ("Test with <code>./run.sh 12</code> and again with "
                    "<code>./run.sh 2</code>. If it survives both, it will survive the "
                    "room.",
                    "Prova con <code>./run.sh 12</code> e poi con <code>./run.sh 2</code>. "
                    "Se sopravvive a entrambi, sopravvivrà alla stanza."),
"p9_no":           ("Do not assume a fixed orb count, and do not key anything on slot "
                    "numbers.",
                    "Non dare per scontato un numero fisso di sfere e non basare nulla "
                    "sui numeri di slot."),
"p9_move_t":       ("Moving to the real orbs", "Passare alle sfere reali"),
"p9_move_l":       ("One address, that is all.", "Un indirizzo, tutto qui."),
"p9_th_where":     ("Where you are", "Dove ti trovi"),
"p9_th_use":       ("Address to use", "Indirizzo da usare"),
"p9_m1":           ("Simulator, on your own machine", "Simulatore, sul tuo computer"),
"p9_m2":           ("Eggbox, USB-C cable — preferred", "Eggbox, cavo USB-C — consigliato"),
"p9_m3":           ("Eggbox, joined to its <code>OrbAP</code> hotspot",
                    "Eggbox, collegato al suo hotspot <code>OrbAP</code>"),

# -- page 10: data + shutdown
"p10_kick":        ("After a session", "Dopo una sessione"),
"p10_title":       ("Taking the data away", "Portare via i dati"),
"p10_lede":        ("The station records every session. <strong>Those recordings live "
                    "in memory and are erased when it powers off</strong> — copy them "
                    "before you unplug anything.",
                    "La stazione registra ogni sessione. <strong>Queste registrazioni "
                    "stanno in memoria e vengono cancellate allo spegnimento</strong>: "
                    "copiale prima di staccare qualsiasi cosa."),
"p10_term":        ("In a terminal on your laptop:", "In un terminale sul tuo computer:"),
"p10_pass":        ("Password is <code>pass</code>. Use <code>10.0.0.8</code> if you "
                    "are on Wi-Fi. This copies every recording into the current folder.",
                    "La password è <code>pass</code>. Usa <code>10.0.0.8</code> se sei "
                    "in Wi-Fi. Copia ogni registrazione nella cartella corrente."),
"p10_th_f":        ("File", "File"),
"p10_th_r":        ("One row per…", "Una riga per…"),
"p10_th_c":        ("Columns", "Colonne"),
"p10_f1r":         ("orb, per sample", "sfera, per campione"),
"p10_f1c":         ("time, serial, cluster, accelerometer, gyroscope, hue, charging, "
                    "battery, signal, nearest orb",
                    "tempo, numero di serie, gruppo, accelerometro, giroscopio, tinta, "
                    "carica, batteria, segnale, sfera più vicina"),
"p10_f2r":         ("<strong>pair of orbs</strong>, per sample",
                    "<strong>coppia di sfere</strong>, per campione"),
"p10_f2c":         ("time, orb A, orb B, strength 0–255 — the full proximity graph "
                    "over the whole session",
                    "tempo, sfera A, sfera B, intensità 0–255 — l'intero grafo di "
                    "prossimità per tutta la sessione"),
"p10_join":        ("Both are plain CSV, ten samples a second. Join them on the "
                    "<code>t</code> column. Time is seconds since the recording "
                    "started — the station has no clock.",
                    "Entrambi sono semplici CSV, dieci campioni al secondo. Uniscili "
                    "sulla colonna <code>t</code>. Il tempo è in secondi dall'inizio "
                    "della registrazione: la stazione non ha un orologio."),
"p10_off_k":       ("Shutting down", "Spegnimento"),
"p10_off_t":       ("Powering off", "Spegnere"),
"p10_off":         ("Once you have copied your recordings — <strong>just pull the "
                    "power out.</strong>",
                    "Dopo aver copiato le registrazioni — <strong>stacca semplicemente "
                    "la corrente.</strong>"),
"p10_offh":        ("No shutdown sequence is needed; the station's disk is protected "
                    "against sudden power loss by design. <strong>Switch the charger "
                    "supply off too</strong> — never leave it running unattended.",
                    "Non serve alcuna procedura di spegnimento: il disco della "
                    "stazione è protetto per costruzione dalle interruzioni di "
                    "corrente. <strong>Spegni anche l'alimentatore dei "
                    "caricatori</strong>: non lasciarlo mai acceso senza sorveglianza."),
"p10_no":          ("Do not unplug before copying. Recordings are held in memory and "
                    "are gone the instant power is lost.",
                    "Non staccare prima di aver copiato. Le registrazioni stanno in "
                    "memoria e spariscono nell'istante in cui manca la corrente."),
"p10_ok":          ("Copy first, unplug second. The files are small — copying twice "
                    "costs nothing.",
                    "Prima copia, poi stacca. I file sono piccoli: copiare due volte "
                    "non costa nulla."),

# -- page 11: troubleshooting
"p11_kick":        ("If something is wrong", "Se qualcosa non va"),
"p11_title":       ("Troubleshooting", "Risoluzione dei problemi"),
"p11_lede":        ("Work down the list. Almost everything is one of the first three rows.",
                    "Procedi dall'alto. Quasi tutto rientra nelle prime tre righe."),
"p11_th_s":        ("What you see", "Cosa vedi"),
"p11_th_d":        ("What to do", "Cosa fare"),
"p11_q1":          ("The screen in the lid shows no orbs",
                    "Lo schermo nel coperchio non mostra sfere"),
"p11_a1":          ("Almost always because they are <strong>charging</strong> — the "
                    "display hides those on purpose (page 7). Lift one out and shake "
                    "it. They are still in your data either way.",
                    "Quasi sempre perché sono <strong>in carica</strong>: il display "
                    "le nasconde di proposito (pagina 7). Prendine una e scuotila. In "
                    "ogni caso restano nei tuoi dati."),
"p11_q2":          ("The browser cannot reach <code>:8080</code> at all",
                    "Il browser non raggiunge <code>:8080</code>"),
"p11_a2":          ("Wrong address for your route. Cable → <code>10.55.0.1</code>, "
                    "Wi-Fi → <code>10.0.0.8</code>, simulator → <code>127.0.0.1</code>. "
                    "If still nothing, try a <strong>different USB port</strong> — a "
                    "weak port is the single most common cause.",
                    "Indirizzo sbagliato per il tuo collegamento. Cavo → "
                    "<code>10.55.0.1</code>, Wi-Fi → <code>10.0.0.8</code>, simulatore "
                    "→ <code>127.0.0.1</code>. Se ancora nulla, prova una "
                    "<strong>porta USB diversa</strong>: una porta debole è la causa "
                    "più comune in assoluto."),
"p11_q3":          ("Page loads, but says no orbs", "La pagina si apre ma non ci sono sfere"),
"p11_a3":          ("The orbs are asleep — one that is not charging drops off after "
                    "30 seconds of stillness. Lift one out and shake it. If a good "
                    "shake does nothing, that orb is flat: charge it for an hour, with "
                    "someone present.",
                    "Le sfere sono in standby: una che non è in carica si stacca dopo "
                    "30 secondi di immobilità. Prendine una e scuotila. Se una bella "
                    "scossa non fa nulla, quella sfera è scarica: ricaricala per "
                    "un'ora, con qualcuno presente."),
"p11_q4":          ("OSC channels never appear", "I canali OSC non appaiono mai"),
"p11_a4":          ("Visit the <code>/osc/subscribe?port=7000</code> address again, "
                    "then open <code>/health</code> — your machine's address should be "
                    "listed under <code>osc_targets</code>. Check the port matches.",
                    "Visita di nuovo l'indirizzo <code>/osc/subscribe?port=7000</code>, "
                    "poi apri <code>/health</code>: l'indirizzo del tuo computer deve "
                    "comparire sotto <code>osc_targets</code>. Verifica che la porta "
                    "corrisponda."),
"p11_q5":          ("Data arrives, then freezes", "I dati arrivano, poi si bloccano"),
"p11_a5":          ("Open <code>/health</code>. If <code>ok</code> is <code>false</code>, "
                    "unplug the station, wait ten seconds, plug it back in.",
                    "Apri <code>/health</code>. Se <code>ok</code> è <code>false</code>, "
                    "stacca la stazione, aspetta dieci secondi e riattaccala."),
"p11_q6":          ("A <code>prox/…</code> channel vanished",
                    "Un canale <code>prox/…</code> è sparito"),
"p11_a6":          ("Normal. Those two orbs stopped hearing each other. It returns "
                    "when they are close again. Use Route 2 for a fixed-size grid.",
                    "Normale. Quelle due sfere hanno smesso di sentirsi. Torna quando "
                    "si riavvicinano. Usa il Metodo 2 per una griglia di dimensione "
                    "fissa."),
"p11_q7":          ("Orbs keep dropping off the Wi-Fi",
                    "Le sfere continuano a cadere dal Wi-Fi"),
"p11_a7":          ("Too many devices. The radio holds about eight in total. Take "
                    "laptops and phones off <code>OrbAP</code> and use the cable.",
                    "Troppi dispositivi. La radio ne regge circa otto in tutto. Togli "
                    "computer e telefoni da <code>OrbAP</code> e usa il cavo."),
"p11_q8":          ("An orb is very hot", "Una sfera è molto calda"),
"p11_a8":          ("Take it off the charger and let it cool. Warm is normal while "
                    "charging; too hot to hold is not. Never charge unattended.",
                    "Toglila dal caricatore e lasciala raffreddare. Tiepida è normale "
                    "durante la ricarica; troppo calda da tenere in mano no. Non "
                    "ricaricare mai senza sorveglianza."),
"p11_q10":         ("No sound from the speakers", "Nessun suono dalle casse"),
"p11_a10":         ("Check you are plugged into the jack <strong>on the screen</strong>, "
                    "not the one on the small computer — that one is switched off and is "
                    "always silent (page 6). Then check the speakers' own volume.",
                    "Verifica di essere collegato al jack <strong>sullo schermo</strong>, "
                    "non a quello sul piccolo computer: quest'ultimo è disattivato ed è "
                    "sempre muto (pagina 6). Poi controlla il volume delle casse."),
"p11_q9":          ("Everything is strange and you want to start again",
                    "Tutto è strano e vuoi ricominciare"),
"p11_a9":          ("Unplug both supplies, wait ten seconds, plug them back in. The "
                    "station rebuilds itself on every start — you cannot break it this "
                    "way.",
                    "Stacca entrambi gli alimentatori, aspetta dieci secondi e "
                    "riattaccali. La stazione si ricostruisce a ogni avvio: così non "
                    "puoi romperla."),
"p11_stuck":       ("Still stuck?", "Ancora bloccato?"),
"p11_stuckh":      ("Take a photo of the screen in the lid and of the <code>/health</code> "
                    "page in your browser, and send both. Those two images answer "
                    "nearly every question we could ask you.",
                    "Fai una foto dello schermo nel coperchio e della pagina "
                    "<code>/health</code> nel browser, e mandale entrambe. Quelle due "
                    "immagini rispondono a quasi ogni domanda che potremmo farti."),
"p11_ref":         ("Full technical reference: <code>touchdesigner/README.md</code>, and "
                    "<code>http://10.55.0.1:8080/schema.json</code> on the station "
                    "itself, which documents every field in the data.",
                    "Riferimento tecnico completo: <code>touchdesigner/README.md</code> "
                    "e <code>http://10.55.0.1:8080/schema.json</code> sulla stazione "
                    "stessa, che documenta ogni campo dei dati."),
"p11_end":         ("Buona fortuna — we are very curious to see what you make of the "
                    "proximity data. Nobody has built anything visual on it yet.",
                    "Buon lavoro — siamo molto curiosi di vedere cosa farete con i dati "
                    "di prossimità. Nessuno ci ha ancora costruito nulla di visivo."),

# -- shared furniture
"foot_cover":      ("Cover", "Copertina"),
"foot_p2":         ("Page 2 · Parts &amp; safety", "Pagina 2 · Componenti e sicurezza"),
"foot_p3":         ("Page 3 · Simulator", "Pagina 3 · Simulatore"),
"foot_p4":         ("Page 4 · Power", "Pagina 4 · Accensione"),
"foot_p5":         ("Page 5 · Connect", "Pagina 5 · Collegamento"),
"foot_p6s":        ("Page 6 · Sound", "Pagina 6 · Audio"),
"foot_p6":         ("Page 7 · Charging", "Pagina 7 · Ricarica"),
"foot_p7":         ("Page 8 · OSC", "Pagina 8 · OSC"),
"foot_p8":         ("Page 9 · WebSocket", "Pagina 9 · WebSocket"),
"foot_p9":         ("Page 10 · Simulated vs real", "Pagina 10 · Simulato e reale"),
"foot_p10":        ("Page 11 · Data &amp; shutdown", "Pagina 11 · Dati e spegnimento"),
"foot_p11":        ("Page 12 · Troubleshooting", "Pagina 12 · Problemi"),
"qty":             ("×", "×"),
}


def build(idx):
    """Render the whole guide for language index 0 (en) or 1 (it)."""
    def t(k):
        return T[k][idx]
    lang = t("lang_tag")

    # ---- reusable line art -------------------------------------------------
    art_case_open = '''<svg width="300" height="200" viewBox="0 0 300 200">
    <path class="ln fillw" d="M40 118 L150 152 L260 118 L260 150 L150 184 L40 150 Z"/>
    <path class="ln fillg" d="M40 118 L150 84 L260 118 L150 152 Z"/>
    <g class="lnT">
      <ellipse cx="103" cy="112" rx="17" ry="8.5"/><ellipse cx="150" cy="126" rx="17" ry="8.5"/>
      <ellipse cx="197" cy="112" rx="17" ry="8.5"/><ellipse cx="103" cy="127" rx="17" ry="8.5"/>
      <ellipse cx="150" cy="141" rx="17" ry="8.5"/><ellipse cx="197" cy="127" rx="17" ry="8.5"/>
    </g>
    <g class="ln fillw">
      <circle cx="103" cy="104" r="13"/><circle cx="150" cy="118" r="13"/>
      <circle cx="197" cy="104" r="13"/>
    </g>
    <path class="ln fillw" d="M40 118 L40 46 L150 12 L150 84 Z"/>
    <path class="ln fillg" d="M56 108 L56 56 L136 31 L136 83 Z"/>
    <g class="lnT" opacity=".6">
      <circle cx="80" cy="80" r="6"/><circle cx="106" cy="66" r="6"/><circle cx="120" cy="88" r="6"/>
      <path d="M80 80 L106 66 M106 66 L120 88 M80 80 L120 88"/>
    </g>
  </svg>'''

    def term(lines, w=200, h=92):
        rows = "".join(
            f'<text class="{c}" x="22" y="{50 + i*17}">{s}</text>'
            for i, (s, c) in enumerate(lines))
        return f'''<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">
        <rect class="ln fillw" x="10" y="10" width="{w-20}" height="{h-20}" rx="6"/>
        <path class="ln" d="M10 30h{w-20}"/>
        <g class="lnT"><circle cx="22" cy="21" r="3"/><circle cx="32" cy="21" r="3"/><circle cx="42" cy="21" r="3"/></g>
        {rows}</svg>'''

    def browser(url, tick=True, w=200, h=100):
        t_ = f'<g class="act"><circle cx="{w-40}" cy="{h-18}" r="9"/><path d="M{w-44} {h-18}l3 3 6-7"/></g>' if tick else ""
        return f'''<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">
        <rect class="ln fillw" x="10" y="10" width="{w-20}" height="{h-18}" rx="6"/>
        <path class="ln" d="M10 30h{w-20}"/>
        <g class="lnT"><circle cx="22" cy="21" r="3"/><circle cx="32" cy="21" r="3"/><circle cx="42" cy="21" r="3"/></g>
        <rect class="fillg" x="56" y="15" width="{w-72}" height="10" rx="5"/>
        <text class="tinyg" x="61" y="23" style="font-size:8px">{url}</text>
        <rect class="actf" x="22" y="42" width="56" height="9" rx="4.5"/>
        <g class="lnT"><path d="M22 60h{w-50}M22 72h{w-50}M22 84h{w-90}"/></g>
        {t_}</svg>'''

    def page(cls, body, foot):
        return (f'<div class="page {cls}">\n{body}\n'
                f'  <div class="foot"><span>{t("foot_name")}</span>'
                f'<span>{foot}</span></div>\n</div>\n')

    def hdr(kick, title, lede):
        return (f'  <div class="hdr">\n'
                f'    <div class="steptitle"><span class="kicker">{kick}</span>'
                f'<h2>{title}</h2></div>\n'
                f'    <p class="lede">{lede}</p>\n  </div>\n')

    def step(n, art, txt):
        num = f'<div class="num">{n}</div>' if n else ''
        return (f'  <div class="step">{num}<div class="art">{art}</div>'
                f'<div class="txt">{txt}</div></div>\n')

    P = []

    # ---- cover -------------------------------------------------------------
    P.append(page("cover", f'''  <svg class="art" width="300" height="200" viewBox="0 0 300 200">
    {art_case_open[art_case_open.index(">")+1:]}
  <h1>{t("cover_title")}</h1>
  <p class="sub">{t("cover_sub")}</p>
  <div class="nowords">
    <figure><svg width="100%" viewBox="0 0 80 60">
      <rect class="ln fillw" x="12" y="14" width="56" height="34" rx="4"/>
      <path class="ln" d="M22 26h26M22 34h18"/>
      <circle class="act" cx="58" cy="40" r="9"/><path class="act" d="M54 40l3 3 6-7"/>
    </svg><figcaption>{t("cover_f1")}</figcaption></figure>
    <figure><svg width="100%" viewBox="0 0 80 60">
      <circle class="ln fillw" cx="40" cy="30" r="19"/><path class="ln" d="M40 19v12l8 5"/>
    </svg><figcaption>{t("cover_f2")}</figcaption></figure>
    <figure><svg width="100%" viewBox="0 0 80 60">
      <path class="ln fillw" d="M26 42a8 8 0 0 1 .6-16 12 12 0 0 1 22.6-2A9 9 0 0 1 52 42Z"/>
      <path style="stroke:#c0392b;stroke-width:2.6;fill:none;stroke-linecap:round" d="M18 48L60 16"/>
    </svg><figcaption>{t("cover_f3")}</figcaption></figure>
  </div>
  <p class="meta">{t("cover_meta")}</p>
  <p class="meta" style="margin-top:3mm;color:#c0392b">⚠ {t("cover_warn")}</p>''',
        t("foot_cover")))

    # ---- page 2: parts + safety -------------------------------------------
    def part(svg, qty, name):
        return (f'    <div class="part"><svg width="100%" height="92" viewBox="0 0 120 80">{svg}</svg>'
                f'<div class="qty">{t("qty")}{qty}</div><div class="nm">{name}</div></div>\n')
    parts = (
      part('<path class="ln fillw" d="M20 48 L60 60 L100 48 L100 62 L60 74 L20 62 Z"/>'
           '<path class="ln fillg" d="M20 48 L60 36 L100 48 L60 60 Z"/>'
           '<path class="ln fillw" d="M20 48 L20 16 L60 4 L60 36 Z"/>', 1, t("p2_case")) +
      part('<g class="ln fillw"><circle cx="42" cy="40" r="15"/><circle cx="78" cy="40" r="15"/></g>'
           '<g class="lnT"><circle cx="42" cy="40" r="7"/><circle cx="78" cy="40" r="7"/></g>', 6, t("p2_orbs")) +
      part('<rect class="ln fillw" x="14" y="24" width="30" height="32" rx="5"/>'
           '<path class="lnT" d="M22 34h14M22 42h9"/><path class="ln" d="M44 40h20"/>'
           '<rect class="act" x="64" y="31" width="34" height="18" rx="9"/>'
           '<path class="lnT" d="M72 40h18"/>'
           f'<text class="tinyg" x="58" y="68" style="font-size:11px">{t("p2_flat")}</text>', 1, t("p2_psu_usb")) +
      part('<rect class="ln fillw" x="14" y="24" width="30" height="32" rx="5"/>'
           '<path class="lnT" d="M22 34h14M22 42h9"/><path class="ln" d="M44 40h26"/>'
           '<circle class="act" cx="83" cy="40" r="13"/><circle class="fillk" cx="83" cy="40" r="4"/>'
           f'<text class="tinyg" x="62" y="68" style="font-size:11px">{t("p2_round")}</text>', 1, t("p2_psu_chg")) +
      part('<path class="ln" d="M26 40c14-16 54 16 68 0"/>'
           '<rect class="ln fillg" x="18" y="33" width="12" height="14" rx="4"/>'
           '<rect class="ln fillg" x="90" y="33" width="12" height="14" rx="4"/>', 1, t("p2_cable")) +
      part('<path class="ln fillw" d="M32 54V26a4 4 0 0 1 4-4h48a4 4 0 0 1 4 4v28"/>'
           '<path class="ln" d="M22 54h76l-6 8H28z"/><path class="lnT" d="M44 34h32M44 42h20"/>', 1, t("p2_laptop")))

    P.append(page("spread", hdr(t("p2_kick"), t("p2_title"), t("p2_lede")) +
      f'  <div class="parts">\n{parts}  </div>\n'
      f'''  <div class="blk warnbox">
    <h2 style="font-size:11.5pt;margin:0 0 1.5mm;color:#a33">⚠ {t("p2_safety_t")}</h2>
    <p class="warn">{t("p2_safety_1")}</p>
    <p class="warn">{t("p2_safety_2")}</p>
    <p class="warn">{t("p2_safety_3")}</p>
    <p class="warn">{t("p2_safety_4")}</p>
  </div>
  <div class="blk">
    <h2 style="font-size:11.5pt;margin:0 0 1mm">{t("p2_routes_t")}</h2>
    <table>
      <tr><th style="width:26mm">{t("p2_th_route")}</th><th style="width:28mm">{t("p2_th_addr")}</th><th>{t("p2_th_when")}</th></tr>
      <tr><td><strong>{t("p2_r1")}</strong></td><td><code>10.55.0.1</code></td><td>{t("p2_r1d")}</td></tr>
      <tr><td><strong>{t("p2_r2")}</strong></td><td><code>10.0.0.8</code></td><td>{t("p2_r2d")}</td></tr>
    </table>
  </div>''', t("foot_p2")))

    # ---- page 3: simulator -------------------------------------------------
    P.append(page("spread", hdr(t("p3_kick"), t("p3_title"), t("p3_lede")) +
      step(1, term([("$ python3 --version", "tinyg"), ("Python 3.12.3", "tinyg")]),
           f'<p class="big">{t("p3_s1")}</p><p class="hint">{t("p3_s1h")}</p>') +
      step(2, term([("$ cd simulator", "tinyg"), ("$ ./run.sh", "tiny")]),
           f'<p class="big">{t("p3_s2")}</p>'
           f'<p style="margin:2mm 0"><code style="font-size:11pt;font-weight:600">./run.sh</code></p>'
           f'<p class="hint">{t("p3_s2h")}</p>'
           f'<p class="hint" style="margin-top:2mm">{t("p3_s2w")}<br>'
           f'<code>python3 fake_orb_data.py --orbs 6 --hz 25</code><br>'
           f'<code>python3 orb_td_bridge.py</code></p>') +
      step(3, browser("127.0.0.1:8080"),
           f'<p class="big">{t("p3_s3")}</p><p class="hint">{t("p3_s3h")}</p>'),
      t("foot_p3")))

    # ---- page 4: power -----------------------------------------------------
    case_ctx = ('<path class="ctx" d="M28 84 L94 104 L160 84 L160 96 L94 116 L28 96 Z"/>'
                '<path class="ctx" d="M28 84 L94 64 L160 84 L94 104 Z"/>'
                '<path class="ctx" d="M28 84 L28 34 L94 14 L94 64 Z"/>')
    P.append(page("spread", hdr(t("p4_kick"), t("p4_title"), t("p4_lede")) +
      step(1, f'<svg width="188" height="118" viewBox="0 0 188 118">{case_ctx}'
              '<rect class="act" x="150" y="76" width="15" height="7" rx="3.5"/>'
              '<path class="act dash" d="M165 79 C182 79 182 40 168 34"/>'
              '<rect class="ln fillw" x="140" y="16" width="30" height="26" rx="5"/>'
              '<path class="lnT" d="M148 24h14M148 32h9"/></svg>',
           f'<p class="big">{t("p4_s1")}</p><p class="hint">{t("p4_s1h")}</p>'
           f'<div class="wait"><svg width="12" height="12" viewBox="0 0 24 24">'
           f'<circle class="lnT" cx="12" cy="12" r="9"/><path class="lnT" d="M12 7v5l3 2"/></svg>'
           f'{t("p4_wait")}</div>') +
      step(2, f'<svg width="188" height="118" viewBox="0 0 188 118">{case_ctx}'
              '<circle class="act" cx="34" cy="88" r="5"/>'
              '<path class="act dash" d="M29 88 C10 88 10 44 22 38"/>'
              '<rect class="ln fillw" x="14" y="16" width="34" height="24" rx="5"/>'
              '<path class="lnT" d="M22 28h18"/></svg>',
           f'<p class="big">{t("p4_s2")}</p>'
           f'<p class="hint warn" style="margin-top:2mm">⚠ {t("p4_s2h")}</p>') +
      step(3, '<svg width="188" height="118" viewBox="0 0 188 118">'
              '<path class="ctx" d="M40 82 L100 100 L160 82 L160 94 L100 112 L40 94 Z"/>'
              '<path class="ctx" d="M40 82 L100 64 L160 82 L100 100 Z"/>'
              '<ellipse class="ctx" cx="76" cy="78" rx="14" ry="7"/>'
              '<circle class="act" cx="112" cy="30" r="16"/>'
              '<path class="act dash" d="M92 66 C96 48 100 40 104 36"/>'
              '<path class="ln" d="M138 20c6 5 6 15 0 20M86 20c-6 5-6 15 0 20"/></svg>',
           f'<p class="big">{t("p4_s3")}</p><p class="hint">{t("p4_s3h")}</p>'
           f'<p class="hint" style="margin-top:2mm">{t("p4_s3h2")}</p>') +
      f'''  <div class="dodont">
    <div><div class="mark ok">✓</div><p>{t("p4_ok")}</p></div>
    <div><div class="mark no">✗</div><p>{t("p4_no")}</p></div>
  </div>''', t("foot_p4")))

    # ---- page 5: connect ---------------------------------------------------
    P.append(page("spread", hdr(t("p5_kick"), t("p5_title"), t("p5_lede")) +
      step(4, '<svg width="188" height="122" viewBox="0 0 188 122">'
              '<path class="ln fillw" d="M12 62V24a4 4 0 0 1 4-4h52a4 4 0 0 1 4 4v38"/>'
              '<path class="ln" d="M4 62h84l-6 9H10z"/>'
              '<path class="act" d="M74 46 C104 46 108 84 130 88"/>'
              '<path class="ctx" d="M104 88 L142 100 L180 88 L180 100 L142 112 L104 100 Z"/>'
              '<path class="ctx" d="M104 88 L142 76 L180 88 L142 100 Z"/>'
              '<path class="ctx" d="M104 88 L104 52 L142 40 L142 76 Z"/>'
              '<text class="tiny" x="14" y="40">A</text></svg>',
           f'<p class="big">{t("p5_a")}</p><p class="hint">{t("p5_ah")}</p>'
           f'<p style="margin-top:2mm">{t("p5_at")} '
           f'<code style="font-size:11pt;font-weight:600">10.55.0.1</code></p>') +
      step(5, '<svg width="188" height="122" viewBox="0 0 188 122">'
              '<path class="ln fillw" d="M12 62V24a4 4 0 0 1 4-4h52a4 4 0 0 1 4 4v38"/>'
              '<path class="ln" d="M4 62h84l-6 9H10z"/>'
              '<g class="act"><path d="M92 58c5-6 5-16 0-22"/><path d="M102 64c9-11 9-29 0-40"/>'
              '<path d="M112 70c13-16 13-42 0-58"/></g>'
              '<path class="ctx" d="M124 88 L152 98 L180 88 L180 100 L152 110 L124 100 Z"/>'
              '<path class="ctx" d="M124 88 L152 78 L180 88 L152 98 Z"/>'
              '<path class="ctx" d="M124 88 L124 56 L152 46 L152 78 Z"/>'
              '<text class="tiny" x="14" y="40">B</text></svg>',
           f'<p class="big">{t("p5_b")}</p>'
           f'<table style="margin-top:1mm">'
           f'<tr><td style="width:24mm;border:0;padding-left:0">{t("p5_net")}</td><td style="border:0"><code>OrbAP</code></td></tr>'
           f'<tr><td style="border:0;padding-left:0">{t("p5_pass")}</td><td style="border:0"><code><code><your-passphrase></code>lt;your-passphrase<code><your-passphrase></code>gt;</code></td></tr>'
           f'<tr><td style="border:0;padding-left:0">{t("p5_stat")}</td><td style="border:0"><code>10.0.0.8</code></td></tr></table>'
           f'<p class="hint" style="margin-top:2mm">{t("p5_bh")}</p>') +
      step(6, browser("10.55.0.1:8080"),
           f'<p class="big">{t("p5_c")}</p>'
           f'<p class="big" style="margin-top:2mm">'
           f'<code style="font-size:10.5pt;font-weight:600">http://10.55.0.1:8080</code> '
           f'<span style="color:#99a0a8">{t("p5_cable")}</span><br>'
           f'<code style="font-size:10.5pt;font-weight:600">http://10.0.0.8:8080</code> '
           f'<span style="color:#99a0a8">{t("p5_wifi")}</span></p>'
           f'<p class="hint" style="margin-top:2mm">{t("p5_ch")}</p>'),
      t("foot_p5")))

    # ---- page 6: sound out -------------------------------------------------
    P.append(page("spread", hdr(t("p6s_kick"), t("p6s_title"), t("p6s_lede")) +
      step(None, '''<svg width="210" height="140" viewBox="0 0 210 140">
        <!-- lid + screen, with the jack called out on the SCREEN -->
        <path class="ln fillw" d="M30 96 L30 26 L126 4 L126 74 Z"/>
        <path class="ln fillg" d="M44 88 L44 36 L112 20 L112 72 Z"/>
        <path class="ctx" d="M30 96 L96 114 L162 96 L162 106 L96 124 L30 106 Z"/>
        <path class="ctx" d="M30 96 L96 78 L162 96 L96 114 Z"/>
        <!-- the live jack -->
        <circle class="act" cx="128" cy="52" r="7"/>
        <path class="act" d="M135 52 L168 52"/>
        <circle class="actf" cx="176" cy="52" r="7"/>
        <text class="tiny" x="132" y="34" style="fill:#1f6feb">✓</text>
        <!-- the dead jack on the computer -->
        <circle class="ctx" cx="150" cy="101" r="5"/>
        <path style="stroke:#c0392b;stroke-width:2.4;fill:none;stroke-linecap:round"
              d="M143 94 L157 108 M157 94 L143 108"/>
      </svg>''',
           f'<p class="big">{t("p6s_s1")}</p>'
           f'<p class="hint warn" style="margin-top:2mm">⚠ {t("p6s_warn")}</p>'
           f'<p class="hint" style="margin-top:2mm">{t("p6s_s1h")}</p>') +
      step(None, '''<svg width="210" height="104" viewBox="0 0 210 104">
        <path class="ln fillw" d="M14 60V24a4 4 0 0 1 4-4h50a4 4 0 0 1 4 4v36"/>
        <path class="ln" d="M6 60h82l-6 9H12z"/>
        <path class="act" d="M76 44 C104 44 108 62 128 64"/>
        <rect class="ln fillw" x="128" y="50" width="72" height="28" rx="4"/>
        <text class="tiny" x="138" y="68">orbstation</text>
        <g class="lnT"><path d="M30 34h26M30 42h16"/></g>
        <text class="tinyg" x="132" y="42" style="font-size:10px">MIDI</text>
      </svg>''',
           f'<p class="big">{t("p6s_s2")}</p>'
           f'<p class="hint">{t("p6s_s2h")}</p>') +
      f'''  <div class="blk">
    <h2 style="font-size:11pt;margin:0 0 1mm">{t("p6s_midi_t")}</h2>
    <table>
      <tr><td style="width:52mm"><strong>{t("p6s_m1")}</strong></td><td>{t("p6s_m1d")}</td></tr>
      <tr><td><strong>{t("p6s_m2")}</strong></td><td>{t("p6s_m2d")}</td></tr>
      <tr><td><strong>{t("p6s_m3")}</strong></td><td>{t("p6s_m3d")}</td></tr>
    </table>
    <p class="hint" style="margin-top:2mm">{t("p6s_tip")}</p>
  </div>''', t("foot_p6s")))

    # ---- page 7: charging vs display --------------------------------------
    P.append(page("spread", hdr(t("p6_kick"), t("p6_title"), t("p6_lede")) +
      step(None, '<svg width="200" height="130" viewBox="0 0 200 130">'
              '<rect class="ln fillw" x="10" y="10" width="120" height="78" rx="5"/>'
              '<path class="ln" d="M10 28h120"/>'
              '<g class="lnT"><circle cx="22" cy="19" r="3"/><circle cx="32" cy="19" r="3"/></g>'
              '<text class="tinyg" x="34" y="60" style="font-size:12px">— — —</text>'
              '<path class="ln" d="M46 96h48l6 10H40z"/>'
              '<g class="ctx"><circle cx="158" cy="36" r="13"/><circle cx="158" cy="72" r="13"/></g>'
              '<path class="lnT" d="M150 92h16M152 100h12"/>'
              '<path style="stroke:#c0392b;stroke-width:2.2;fill:none;stroke-linecap:round" d="M146 24L172 84"/>'
              '</svg>',
           f'<p>{t("p6_body")}</p><p style="margin-top:2mm">{t("p6_body2")}</p>') +
      f'''  <div class="blk">
    <table>
      <tr><th style="width:56mm">{t("p6_th_w")}</th><th>{t("p6_th_s")}</th></tr>
      <tr><td>{t("p6_w1")}</td><td><strong style="color:#c0392b">{t("p6_s1")}</strong></td></tr>
      <tr><td>{t("p6_w2")}</td><td><strong style="color:#1c7a3e">{t("p6_s2")}</strong></td></tr>
      <tr><td>{t("p6_w3")}</td><td>{t("p6_s3")}</td></tr>
    </table>
    <p class="lede" style="margin-top:3mm">{t("p6_tip")}</p>
    <p class="hint">{t("p6_test")}</p>
  </div>''', t("foot_p6")))

    # ---- page 7: OSC -------------------------------------------------------
    ch = lambda c, m: f'<tr><td><code>{c}</code></td><td>{m}</td></tr>'
    P.append(page("", hdr(t("p7_kick"), t("p7_title"), t("p7_lede")) +
      step(7, '<svg width="188" height="112" viewBox="0 0 188 112">'
              '<rect class="ln fillw" x="8" y="14" width="172" height="46" rx="6"/>'
              '<path class="ln" d="M8 32h172"/>'
              '<g class="lnT"><circle cx="20" cy="23" r="3"/><circle cx="30" cy="23" r="3"/></g>'
              '<rect class="fillg" x="52" y="18" width="120" height="10" rx="5"/>'
              '<text class="tinyg" x="57" y="26" style="font-size:8px">…/osc/subscribe?port=7000</text>'
              '<path class="act" d="M94 62v16"/><path class="act" d="M88 72l6 6 6-6"/>'
              '<rect class="ln fillw" x="46" y="84" width="96" height="22" rx="4"/>'
              '<text class="tiny" x="58" y="99">OSC In CHOP</text></svg>',
           f'<p class="big">{t("p7_s1")}</p>'
           f'<p class="big" style="margin:2mm 0"><code style="font-size:9.5pt;font-weight:600">'
           f'http://10.55.0.1:8080/osc/subscribe?port=7000</code></p>'
           f'<p class="hint">{t("p7_s1h")}</p>') +
      step(8, '<svg width="188" height="98" viewBox="0 0 188 98">'
              '<rect class="ln fillw" x="14" y="12" width="160" height="74" rx="5"/>'
              '<path class="lnT" d="M14 28h160"/><text class="tiny" x="24" y="24">OSC In CHOP</text>'
              '<text class="tinyg" x="24" y="46">Network Port</text>'
              '<rect class="act" x="104" y="36" width="52" height="14" rx="3"/>'
              '<text class="tiny" x="118" y="46">7000</text>'
              '<g class="lnT"><path d="M24 62h60M24 74h74"/></g></svg>',
           f'<p class="big">{t("p7_s2")}</p><p class="hint">{t("p7_s2h")}</p>') +
      f'''  <h2 style="font-size:11pt;margin:4mm 0 0">{t("p7_get")}</h2>
  <table>
    <tr><th style="width:52mm">{t("p7_th_c")}</th><th>{t("p7_th_m")}</th></tr>
    {ch("orb/a1b2c3/accel1..3", t("p7_c1"))}
    {ch("orb/a1b2c3/gyro1..3", t("p7_c2"))}
    {ch("orb/a1b2c3/spin", t("p7_c3"))}
    {ch("orb/a1b2c3/shake", t("p7_c4"))}
    {ch("orb/a1b2c3/tilt", t("p7_c5"))}
    {ch("orb/a1b2c3/cluster", t("p7_c6"))}
    {ch("orb/a1b2c3/charging", t("p7_c7"))}
    {ch("orb/a1b2c3/battery", t("p7_c8"))}
    <tr><td><code style="color:#1f6feb">prox/a1b2c3/d4e5f6</code></td><td>{t("p7_c9")}</td></tr>
    {ch("fleet/count &nbsp; fleet/rate", t("p7_c10"))}
  </table>
  <p class="hint" style="margin-top:3mm">{t("p7_serial")}</p>''', t("foot_p7")))

    # ---- page 8: websocket + proximity ------------------------------------
    P.append(page("spread", hdr(t("p8_kick"), t("p8_title"), t("p8_lede")) +
      step(9, '<svg width="188" height="132" viewBox="0 0 188 132">'
              '<rect class="ln fillw" x="10" y="10" width="168" height="34" rx="4"/>'
              '<text class="tiny" x="20" y="24">WebSocket DAT</text>'
              '<text class="tinyg" x="20" y="38">ws://10.55.0.1:8080/ws</text>'
              '<path class="act" d="M60 44v14"/><path class="act" d="M54 52l6 6 6-6"/>'
              '<path class="act" d="M128 44v14"/><path class="act" d="M122 52l6 6 6-6"/>'
              '<rect class="ln fillw" x="10" y="60" width="80" height="30" rx="4"/>'
              '<text class="tiny" x="20" y="72">Script CHOP</text><text class="tinyg" x="20" y="84">orbs</text>'
              '<rect class="ln fillw" x="98" y="60" width="80" height="30" rx="4"/>'
              '<text class="tiny" x="108" y="72">Script CHOP</text><text class="tinyg" x="108" y="84">proximity</text>'
              '<g class="lnT"><path d="M50 90v12M138 90v12"/></g>'
              '<rect class="fillg" x="10" y="102" width="168" height="22" rx="4"/></svg>',
           f'<p class="big">{t("p8_add")}</p>'
           f'<table style="margin-top:1mm">'
           f'<tr><td style="width:30mm;border:0;padding-left:0">Network Address</td><td style="border:0"><code>10.55.0.1</code></td></tr>'
           f'<tr><td style="border:0;padding-left:0">Network Port</td><td style="border:0"><code>8080</code></td></tr>'
           f'<tr><td style="border:0;padding-left:0">Request URL</td><td style="border:0"><code>/ws</code></td></tr></table>'
           f'<p class="hint" style="margin-top:2mm">{t("p8_paste")}</p>') +
      f'''  <div class="blk">
    <h2 style="font-size:12pt;margin:0 0 1mm">{t("p8_prox_t")}</h2>
    <div class="step"><div class="art"><svg width="188" height="118" viewBox="0 0 188 118">
      <g class="ln fillw"><circle cx="40" cy="30" r="14"/><circle cx="80" cy="22" r="14"/>
      <circle cx="64" cy="64" r="14"/><circle cx="160" cy="46" r="14"/></g>
      <g class="act"><path d="M53 27 L67 24"/><path d="M47 42 L58 51"/><path d="M78 36 L70 51"/></g>
      <path class="ctx dash" d="M96 52 L145 48"/></svg></div>
    <div class="txt"><p>{t("p8_prox_1")}</p>
      <p class="hint" style="margin-top:2mm">{t("p8_prox_2")}</p>
      <p class="hint warn" style="margin-top:2mm">{t("p8_sat")}</p></div></div>
  </div>
  <div class="dodont">
    <div><div class="mark ok">✓</div><p>{t("p8_ok")}</p></div>
    <div><div class="mark no">✗</div><p>{t("p8_no")}</p></div>
  </div>''', t("foot_p8")))

    # ---- page 9: simulated vs real ----------------------------------------
    P.append(page("spread", hdr(t("p9_kick"), t("p9_title"), t("p9_lede")) +
      f'''  <div class="blk">
    <h2 style="font-size:11pt;margin:0 0 1mm;color:#1c7a3e">✓ {t("p9_right_t")}</h2>
    <p class="lede" style="margin-bottom:0">{t("p9_right")}</p>
  </div>
  <div class="blk">
    <h2 style="font-size:11pt;margin:0 0 1mm">{t("p9_wrong_t")}</h2>
    <table>
      <tr><th style="width:44mm">{t("p9_th_n")}</th><th>{t("p9_th_w")}</th></tr>
      <tr><td><strong>{t("p9_w1")}</strong></td><td>{t("p9_w1d")}</td></tr>
      <tr><td><strong>{t("p9_w2")}</strong></td><td>{t("p9_w2d")}</td></tr>
      <tr><td><strong>{t("p9_w3")}</strong></td><td>{t("p9_w3d")}</td></tr>
      <tr><td><strong>{t("p9_w4")}</strong></td><td>{t("p9_w4d")}</td></tr>
    </table>
  </div>
  <div class="dodont">
    <div><div class="mark ok">✓</div><p>{t("p9_ok")}</p></div>
    <div><div class="mark no">✗</div><p>{t("p9_no")}</p></div>
  </div>
  <div class="blk">
    <h2 style="font-size:11pt;margin:0 0 1mm">{t("p9_move_t")}</h2>
    <p class="lede" style="margin-bottom:1mm">{t("p9_move_l")}</p>
    <table>
      <tr><th style="width:60mm">{t("p9_th_where")}</th><th>{t("p9_th_use")}</th></tr>
      <tr><td>{t("p9_m1")}</td><td><code>127.0.0.1</code></td></tr>
      <tr><td>{t("p9_m2")}</td><td><code>10.55.0.1</code></td></tr>
      <tr><td>{t("p9_m3")}</td><td><code>10.0.0.8</code></td></tr>
    </table>
  </div>''', t("foot_p9")))

    # ---- page 10: data + shutdown -----------------------------------------
    P.append(page("spread", hdr(t("p10_kick"), t("p10_title"), t("p10_lede")) +
      step(10, term([("$ scp nodes@10.55.0.1:", "tinyg"), ("   orb_logs/* .", "tinyg")]),
           f'<p class="big">{t("p10_term")}</p>'
           f'<p style="margin:2mm 0"><code style="font-size:10pt">scp nodes@10.55.0.1:orb_logs/* .</code></p>'
           f'<p class="hint">{t("p10_pass")}</p>') +
      f'''  <div class="blk">
    <table>
      <tr><th style="width:42mm">{t("p10_th_f")}</th><th style="width:30mm">{t("p10_th_r")}</th><th>{t("p10_th_c")}</th></tr>
      <tr><td><code>run-001.csv</code></td><td>{t("p10_f1r")}</td><td>{t("p10_f1c")}</td></tr>
      <tr><td><code>run-001.prox.csv</code></td><td>{t("p10_f2r")}</td><td>{t("p10_f2c")}</td></tr>
    </table>
    <p class="hint" style="margin-top:2mm">{t("p10_join")}</p>
  </div>
  <div class="blk">
    <div class="steptitle"><span class="kicker">{t("p10_off_k")}</span><h2>{t("p10_off_t")}</h2></div>
  </div>''' +
      step(11, '<svg width="188" height="96" viewBox="0 0 188 96">'
              '<path class="ctx" d="M40 56 L94 72 L148 56 L148 68 L94 84 L40 68 Z"/>'
              '<path class="ctx" d="M40 56 L94 40 L148 56 L94 72 Z"/>'
              '<path class="ctx" d="M40 56 L40 24 L94 8 L94 40 Z"/>'
              '<rect class="act" x="140" y="50" width="14" height="7" rx="3.5"/>'
              '<path class="act" d="M158 53 L178 53"/><path class="act" d="M170 45l8 8-8 8"/></svg>',
           f'<p class="big">{t("p10_off")}</p><p class="hint">{t("p10_offh")}</p>') +
      f'''  <div class="dodont">
    <div><div class="mark no">✗</div><p>{t("p10_no")}</p></div>
    <div><div class="mark ok">✓</div><p>{t("p10_ok")}</p></div>
  </div>''', t("foot_p10")))

    # ---- page 11: troubleshooting -----------------------------------------
    row = lambda q, a: f'<tr><td>{q}</td><td>{a}</td></tr>'
    P.append(page("", hdr(t("p11_kick"), t("p11_title"), t("p11_lede")) +
      f'''  <table>
    <tr><th style="width:56mm">{t("p11_th_s")}</th><th>{t("p11_th_d")}</th></tr>
    {row(t("p11_q1"), t("p11_a1"))}
    {row(t("p11_q2"), t("p11_a2"))}
    {row(t("p11_q3"), t("p11_a3"))}
    {row(t("p11_q4"), t("p11_a4"))}
    {row(t("p11_q5"), t("p11_a5"))}
    {row(t("p11_q6"), t("p11_a6"))}
    {row(t("p11_q7"), t("p11_a7"))}
    {row(t("p11_q8"), t("p11_a8"))}
    {row(t("p11_q10"), t("p11_a10"))}
    {row(t("p11_q9"), t("p11_a9"))}
  </table>''' +
      step(None, '<svg width="150" height="112" viewBox="0 0 150 112">'
              '<path class="ln fillw" d="M20 82V46a4 4 0 0 1 4-4h40a4 4 0 0 1 4 4v36"/>'
              '<path class="ln" d="M12 82h84l-6 9H18z"/>'
              '<circle class="ln fillw" cx="104" cy="52" r="12"/>'
              '<path class="ln" d="M104 68c-10 0-17 7-17 16"/>'
              '<path class="act" d="M112 26h22a8 8 0 0 1 8 8v8a8 8 0 0 1-8 8h-10l-8 7v-7h-4a8 8 0 0 1-8-8v-8a8 8 0 0 1 8-8"/>'
              '<text class="tiny" x="119" y="44" style="font-size:15px;fill:#1f6feb">?</text></svg>',
           f'<p class="big">{t("p11_stuck")}</p><p class="hint">{t("p11_stuckh")}</p>'
           f'<p class="hint" style="margin-top:3mm">{t("p11_ref")}</p>'
           f'<p class="hint" style="margin-top:3mm;font-style:italic">{t("p11_end")}</p>'),
      t("foot_p11")))

    return (f'<meta charset="utf-8">\n<html lang="{lang}">\n'
            f'<title>{t("doc_title")}</title>\n'
            f'<link rel="stylesheet" href="guide.css">\n'
            f'<style>\n'
            f'  .warnbox {{ border-left: 2.4pt solid #c0392b; padding-left: 4mm; }}\n'
            f'  p.warn {{ font-size: 9pt; color: #7a2a20; margin: 0 0 1.6mm; line-height: 1.45; }}\n'
            f'  .hint.warn {{ color: #a33; }}\n'
            f'</style>\n' + "".join(P))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", action="store_true", help="also render PDFs via headless Chrome")
    a = ap.parse_args()
    out = []
    for i, code in enumerate(("EN", "IT")):
        path = os.path.join(HERE, f"NODES_GUIDE_{code}.html")
        with open(path, "w") as f:
            f.write(build(i))
        print(f"wrote {os.path.relpath(path)}")
        out.append(path)
    if a.pdf:
        if not os.path.exists(CHROME):
            sys.exit("Chrome not found — render manually with any headless browser")
        for path in out:
            pdf = path[:-5] + ".pdf"
            subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                            f"--print-to-pdf={pdf}", f"file://{path}"],
                           capture_output=True, check=True)
            print(f"wrote {os.path.relpath(pdf)}")


if __name__ == "__main__":
    main()
