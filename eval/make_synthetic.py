"""Build Dolmi's synthetic meeting test set: scripted German/English meeting talk, voiced with the OS
text-to-speech, with exact reference timings.

    python eval/make_synthetic.py                 # ~40 min in 4 sessions + 3 three-voice speaker clips
    python eval/make_synthetic.py --minutes 10    # smaller set (sessions scale down)

Audio goes to eval/data/synthetic/ and eval/data/speakers/ (git-ignored, never committed). Each WAV has a
JSON sidecar with its references; eval/manifest.json lists every file with its sha256 and references.

Everything here is fictional: companies, people, products and figures are invented for the test set.
Voices: Windows System.Speech (Microsoft Hedda de-DE, Zira en-US, Hazel en-GB) driven by a PowerShell
child process, with word timings from its SpeakProgress events; macOS 'say' (Anna, Samantha, Daniel)
with word timings estimated from text length. Extra German speakers are Hedda (or Anna) at another
rate, resampled so the pitch moves too.
"""
import argparse
import hashlib
import io
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

SR = 16000
EVAL = Path(__file__).resolve().parent
DATA = EVAL / "data"
MANIFEST = EVAL / "manifest.json"
SEED = 7

# ------------------------------------------------------------------ fictional terms
# kind: keep = copied unchanged into English; german = German term with a fixed English rendering.
TERMS = [
    {"de": "Velorix", "en": "Velorix", "kind": "company"},
    {"de": "Brandtwerk", "en": "Brandtwerk", "kind": "company"},
    {"de": "Halvora", "en": "Halvora", "kind": "company"},
    {"de": "Nimbus Gateway", "en": "Nimbus Gateway", "kind": "product"},
    {"de": "Lumora", "en": "Lumora", "kind": "product"},
    {"de": "Quellsync", "en": "Quellsync", "kind": "product"},
    {"de": "Projekt Kranich", "en": "Project Crane", "kind": "german"},
    {"de": "Jana Kolbe", "en": "Jana Kolbe", "kind": "person"},
    {"de": "Mehmet Arslan", "en": "Mehmet Arslan", "kind": "person"},
    {"de": "Sophie Brenner", "en": "Sophie Brenner", "kind": "person"},
    {"de": "Lukas Hartmann", "en": "Lukas Hartmann", "kind": "person"},
    {"de": "Priya Raman", "en": "Priya Raman", "kind": "person"},
    {"de": "Tobias Wendt", "en": "Tobias Wendt", "kind": "person"},
    {"de": "Kubernetes", "en": "Kubernetes", "kind": "tech"},
    {"de": "Pull Request", "en": "pull request", "kind": "tech"},
    {"de": "Staging", "en": "staging", "kind": "tech"},
    {"de": "Backlog", "en": "backlog", "kind": "tech"},
    {"de": "Sprint Review", "en": "sprint review", "kind": "tech"},
    {"de": "Feature Flag", "en": "feature flag", "kind": "tech"},
    {"de": "CI-Pipeline", "en": "CI pipeline", "kind": "tech"},
    {"de": "Lastenheft", "en": "requirements specification", "kind": "german"},
    {"de": "Abnahme", "en": "sign-off", "kind": "german"},
    {"de": "Wissensdatenbank", "en": "knowledge base", "kind": "german"},
    {"de": "Betriebsrat", "en": "works council", "kind": "german"},
]

PEOPLE = [t["de"] for t in TERMS if t["kind"] == "person"]
COMPANIES = [t["de"] for t in TERMS if t["kind"] == "company"]
PRODUCTS = ["Nimbus Gateway", "Lumora", "Quellsync"]
DAYS = [("Montag", "Monday"), ("Dienstag", "Tuesday"), ("Mittwoch", "Wednesday"),
        ("Donnerstag", "Thursday"), ("Freitag", "Friday")]
MONTHS = [("Januar", "January"), ("Februar", "February"), ("März", "March"), ("April", "April"),
          ("Mai", "May"), ("Juni", "June"), ("Juli", "July"), ("August", "August"),
          ("September", "September"), ("Oktober", "October"), ("November", "November"),
          ("Dezember", "December")]

# ------------------------------------------------------------------ scripts
# (category, German or English text, English subtitle reference). lang is "en" for category E/ME.
# S status, Q question, C code-switching, E English, V verb-final long, B backchannel,
# M monologue (> 12 s), N numbers/dates.
TEMPLATES = [
    ("S", "Das Deployment für {prod} ist auf {day} verschoben.", "The deployment for {prod} has been moved to {day}."),
    ("S", "{p} hat den Pull Request gestern Abend gemergt.", "{p} merged the pull request last night."),
    ("S", "Auf Staging läuft jetzt Version {ver}.", "Version {ver} is now running on staging."),
    ("S", "Wir haben im Backlog noch {n} offene Tickets.", "We still have {n} open tickets in the backlog."),
    ("S", "Die Ladezeit vom Dashboard ist um {pct} gesunken.", "The loading time of the dashboard has dropped by {pct}."),
    ("S", "Der Hotfix ist seit heute Morgen live.", "The hotfix has been live since this morning."),
    ("S", "{c} möchte die Abnahme bis zum {date} haben.", "{c} wants the sign-off by {date}."),
    ("S", "Ich schicke euch das Lastenheft nach dem Meeting.", "I'll send you the requirements specification after the meeting."),
    ("S", "Das Sprint Review machen wir am {day} um {h} Uhr.", "We'll do the sprint review on {day} at {h}."),
    ("S", "{p} übernimmt ab nächster Woche das Onboarding für {c}.", "{p} will take over the onboarding for {c} from next week."),
    ("S", "Der Rollback hat ungefähr {n} Minuten gedauert.", "The rollback took about {n} minutes."),
    ("S", "Wir brauchen für {prod} noch ein Feature Flag.", "We still need a feature flag for {prod}."),
    ("S", "Die CI-Pipeline ist seit dem Update deutlich langsamer.", "The CI pipeline has been noticeably slower since the update."),
    ("S", "Das Budget für das zweite Quartal liegt bei {money}.", "The budget for the second quarter is {money}."),
    ("S", "{p} hat die Zahlen für {c} schon geprüft.", "{p} has already checked the figures for {c}."),
    ("S", "Die Migration auf Kubernetes ist zu {pct} fertig.", "The migration to Kubernetes is {pct} complete."),
    ("S", "Der Betriebsrat muss dem neuen Tool noch zustimmen.", "The works council still has to approve the new tool."),
    ("S", "In der Wissensdatenbank fehlen noch die Einträge zu {prod}.", "The entries on {prod} are still missing from the knowledge base."),
    ("S", "Mit Projekt Kranich starten wir offiziell am {date}.", "We officially start Project Crane on {date}."),
    ("S", "Das Single Sign-on für {c} funktioniert wieder.", "Single sign-on for {c} is working again."),
    ("S", "Die Schnittstelle liefert bei großen Anfragen manchmal einen Timeout.", "The interface sometimes returns a timeout for large requests."),
    ("S", "{p} ist heute krank, deshalb übernehme ich die Punkte.", "{p} is sick today, so I'll take over the items."),
    ("S", "Wir liegen beim Release etwa {n} Tage hinter dem Plan.", "We are about {n} days behind schedule on the release."),
    ("S", "Quellsync synchronisiert die Daten jetzt alle {n} Minuten.", "Quellsync now synchronizes the data every {n} minutes."),
    ("S", "Lumora hat inzwischen {big} aktive Nutzer.", "Lumora now has {big} active users."),
    ("Q", "Hat jemand schon mit {p} über das Nimbus Gateway gesprochen?", "Has anyone talked to {p} about the Nimbus Gateway yet?"),
    ("Q", "Können wir das Deployment auf {day} vorziehen?", "Can we bring the deployment forward to {day}?"),
    ("Q", "Wer kümmert sich um den Merge-Konflikt?", "Who is taking care of the merge conflict?"),
    ("Q", "Gibt es schon Feedback von {c}?", "Is there any feedback from {c} yet?"),
    ("Q", "Wie viele Tickets sind noch im Backlog?", "How many tickets are still in the backlog?"),
    ("Q", "Sollen wir das Thema im nächsten Sprint Review ansprechen?", "Should we bring up the topic in the next sprint review?"),
    ("Q", "Ist die Version {ver} schon auf Staging?", "Is version {ver} already on staging?"),
    ("Q", "Weiß jemand, warum die CI-Pipeline rot ist?", "Does anyone know why the CI pipeline is red?"),
    ("Q", "Kannst du mir das Lastenheft noch mal schicken?", "Can you send me the requirements specification again?"),
    ("Q", "Brauchen wir dafür die Zustimmung vom Betriebsrat?", "Do we need the approval of the works council for that?"),
    ("C", "Das ist eigentlich ein Quick Win, wir sollten das einfach shippen.", "That's actually a quick win, we should just ship it."),
    ("C", "Lass uns das offline klären, das ist ein bisschen out of scope.", "Let's clarify that offline, that's a bit out of scope."),
    ("C", "Der Use Case von {c} ist ziemlich ähnlich.", "The use case of {c} is pretty similar."),
    ("C", "Kannst du das Ticket kurz updaten und mich dann taggen?", "Can you quickly update the ticket and then tag me?"),
    ("C", "Wir haben am {day} ein Meeting mit den Stakeholdern von {c}.", "We have a meeting with the stakeholders of {c} on {day}."),
    ("C", "Das Feature ist nice to have, aber kein Blocker.", "The feature is nice to have, but not a blocker."),
    ("C", "Ich würde sagen, wir machen erst mal einen Proof of Concept.", "I would say we do a proof of concept first."),
    ("C", "Der Workaround ist okay, aber long term brauchen wir einen richtigen Fix.", "The workaround is okay, but long term we need a proper fix."),
    ("E", "Sorry, could you repeat the last point about the Nimbus Gateway?", None),
    ("E", "From our side, the integration with {c} looks good.", None),
    ("E", "I think we should wait for the security review before the release.", None),
    ("E", "Let me share my screen, I will show you the dashboard.", None),
    ("E", "Can we move this to the next sprint?", None),
    ("E", "The numbers from last week were about {pct} higher.", None),
    ("E", "I will follow up with {p} after the call.", None),
    ("E", "Quick question, is the staging environment down right now?", None),
    ("V", "Ich glaube, dass wir das Release erst nach der Abnahme durch {c} wirklich freigeben können.",
     "I think that we can only really release it after the sign-off by {c}."),
    ("V", "Wenn die Migration auf Kubernetes bis {day} nicht abgeschlossen ist, müssen wir den Termin mit {c} noch einmal verschieben.",
     "If the migration to Kubernetes is not completed by {day}, we will have to postpone the appointment with {c} once again."),
    ("V", "{p} hat gesagt, dass das Nimbus Gateway bei mehr als {n} gleichzeitigen Verbindungen manchmal abstürzt.",
     "{p} said that the Nimbus Gateway sometimes crashes with more than {n} simultaneous connections."),
    ("V", "Weil die Daten aus dem alten System nicht vollständig übernommen wurden, konnten wir die Auswertung für das letzte Quartal noch nicht fertigstellen.",
     "Because the data from the old system was not completely transferred, we have not yet been able to finish the evaluation for the last quarter."),
    ("V", "Ich weiß nicht, ob wir das Budget von {money} für dieses Projekt tatsächlich bekommen werden.",
     "I don't know whether we will actually get the budget of {money} for this project."),
    ("V", "Sobald der Betriebsrat zugestimmt hat, können wir die neue Zeiterfassung für alle Mitarbeitenden einführen.",
     "As soon as the works council has agreed, we can introduce the new time tracking for all employees."),
    ("V", "Es wäre gut, wenn du die Kommentare zum Lastenheft bis {day} in die Wissensdatenbank eintragen könntest.",
     "It would be good if you could enter the comments on the requirements specification into the knowledge base by {day}."),
    ("V", "Wir haben festgestellt, dass der Fehler nur auftritt, wenn sich der Nutzer über Single Sign-on anmeldet.",
     "We found that the error only occurs when the user logs in via single sign-on."),
    ("V", "Da {c} den Vertrag erst am {date} unterschreiben wird, sollten wir mit der Umsetzung vorher nicht anfangen.",
     "Since {c} will only sign the contract on {date}, we should not start the implementation before then."),
    ("V", "Ob wir Lumora im nächsten Quartal auch in Österreich anbieten, hängt davon ab, wie die Zahlen im Pilotprojekt aussehen.",
     "Whether we also offer Lumora in Austria next quarter depends on what the figures in the pilot project look like."),
    ("B", "Ja.", "Yes."), ("B", "Genau.", "Exactly."), ("B", "Okay.", "Okay."), ("B", "Mhm, stimmt.", "Mhm, that's right."),
    ("B", "Richtig.", "Right."), ("B", "Ja, genau.", "Yes, exactly."), ("B", "Super, danke.", "Great, thanks."),
    ("B", "Alles klar.", "All right."), ("B", "Gute Frage.", "Good question."), ("B", "Nein.", "No."),
    ("B", "Moment.", "One moment."), ("B", "Danke dir.", "Thank you."),
    ("M", "Kurz zum Stand bei {c}: Wir haben letzte Woche die Schnittstelle zum Nimbus Gateway fertiggestellt, die ersten Tests auf Staging sind gut gelaufen, und {p} schreibt gerade die Dokumentation für die Wissensdatenbank. Offen ist noch die Abnahme, die wir hoffentlich bis zum {date} bekommen, damit wir das Deployment nicht noch einmal verschieben müssen.",
     "A quick update on {c}: last week we finished the interface to the Nimbus Gateway, the first tests on staging went well, and {p} is currently writing the documentation for the knowledge base. The sign-off is still open, which we hopefully get by {date}, so that we don't have to postpone the deployment again."),
    ("M", "Ich fasse noch mal zusammen, was wir heute besprochen haben. Erstens verschieben wir das Release von Lumora auf {day}. Zweitens kümmert sich {p} um den Hotfix für die CI-Pipeline. Und drittens klären wir mit dem Betriebsrat, ob wir die neue Zeiterfassung schon im nächsten Monat einführen können.",
     "Let me summarize what we discussed today. First, we are moving the Lumora release to {day}. Second, {p} is taking care of the hotfix for the CI pipeline. And third, we will check with the works council whether we can introduce the new time tracking next month."),
    ("M", "Also, das Problem ist eigentlich nicht die Performance an sich, sondern dass wir bei jeder Anfrage die komplette Konfiguration neu laden, und das dauert bei großen Kunden wie {c} teilweise mehr als {n} Sekunden. Wenn wir das cachen, sollten wir die Antwortzeit ungefähr halbieren können.",
     "So the problem is not actually the performance itself, but that we reload the complete configuration with every request, and with large customers like {c} that sometimes takes more than {n} seconds. If we cache that, we should be able to roughly halve the response time."),
    ("M", "Bei den Kosten sieht es so aus: Für die Server zahlen wir im Moment ungefähr {money} im Monat, dazu kommen noch die Lizenzen für Quellsync. Wenn wir auf Kubernetes umsteigen, rechnen wir mit einer Ersparnis von etwa {pct}, aber dafür brauchen wir am Anfang zwei Wochen zusätzliche Arbeit.",
     "As for the costs: at the moment we pay about {money} a month for the servers, plus the licences for Quellsync. If we switch to Kubernetes, we expect savings of about {pct}, but for that we need two weeks of extra work at the beginning."),
    ("M", "Ich war gestern beim Workshop mit {c}, und ehrlich gesagt war die Stimmung besser als erwartet. Sie haben viele Fragen zum Datenschutz gestellt, vor allem dazu, wo die Daten gespeichert werden und wer darauf Zugriff hat. Ich habe zugesagt, dass wir ihnen bis {day} eine schriftliche Übersicht schicken.",
     "I was at the workshop with {c} yesterday, and honestly the mood was better than expected. They asked a lot of questions about data protection, especially about where the data is stored and who has access to it. I promised that we would send them a written overview by {day}."),
    ("ME", "Okay, so from the London side, we have finished the first round of user interviews. Most people liked the new dashboard, but several of them said the export function is hard to find. We would like to fix that before the release, so it would help if your team could look at the designs this week.", None),
    ("M", "Noch ein Punkt zum Onboarding: Die neuen Kolleginnen und Kollegen bekommen ab dem {date} automatisch einen Zugang zur Wissensdatenbank und zum Backlog. {p} hat dafür eine kurze Checkliste geschrieben, die wir im nächsten Sprint Review gemeinsam durchgehen, damit nichts vergessen wird.",
     "One more point on onboarding: from {date}, new colleagues will automatically get access to the knowledge base and the backlog. {p} has written a short checklist for this, which we will go through together in the next sprint review so that nothing is forgotten."),
    ("M", "Was die Sicherheit angeht, haben wir im letzten Penetrationstest insgesamt {n} Schwachstellen gefunden, davon waren zwei kritisch. Beide sind inzwischen behoben, und der Fix ist mit Version {ver} auf Staging. Den Bericht für {c} schicke ich euch heute Nachmittag.",
     "Regarding security, we found a total of {n} vulnerabilities in the last penetration test, two of which were critical. Both have since been fixed, and the fix is on staging with version {ver}. I will send you the report for {c} this afternoon."),
    ("N", "Wir haben im September {big} Euro Umsatz gemacht.", "We made {big} euros in revenue in September."),
    ("N", "Die Fehlerquote liegt bei {dec} Prozent.", "The error rate is {dec} percent."),
    ("N", "Das Meeting mit {c} ist am {date} um {h} Uhr.", "The meeting with {c} is on {date} at {h}."),
    ("N", "Wir rechnen mit {n} neuen Kunden bis Ende {year}.", "We expect {n} new customers by the end of {year}."),
    ("N", "Ticket {ticket} ist erledigt, Ticket {ticket2} noch nicht.", "Ticket {ticket} is done, ticket {ticket2} not yet."),
    ("N", "Die Antwortzeit ist von {n2} auf {n} Millisekunden gefallen.", "The response time has dropped from {n2} to {n} milliseconds."),
]
WEIGHTS = {"S": 30, "Q": 11, "C": 10, "E": 8, "V": 13, "B": 14, "M": 5, "ME": 1, "N": 8}

# ------------------------------------------------------------------ voices
# Windows voices (System.Speech) and their macOS 'say' stand-ins. factor > 1 = resampled faster
# (higher pitch); rate is the TTS rate (-10..10 on Windows) chosen so the speed stays natural.
VOICES = {
    "de-a": {"win": "Microsoft Hedda Desktop", "mac": "Anna", "lang": "de", "rate": 0, "factor": 1.0},
    "de-b": {"win": "Microsoft Hedda Desktop", "mac": "Anna", "lang": "de", "rate": 1, "factor": 0.87},
    "de-c": {"win": "Microsoft Hedda Desktop", "mac": "Anna", "lang": "de", "rate": -1, "factor": 1.13},
    "en-a": {"win": "Microsoft Zira Desktop", "mac": "Samantha", "lang": "en", "rate": 0, "factor": 1.0},
    "en-b": {"win": "Microsoft Hazel Desktop", "mac": "Daniel", "lang": "en", "rate": 0, "factor": 1.0},
}

# Sessions: which voices speak and what happens to the audio. Durations scale with --minutes.
SESSIONS = [
    {"id": "syn-s1", "share": 0.25, "voices": ["de-a", "de-b", "en-a"], "snr_db": None, "opus_kbps": None,
     "level_var": False, "note": "clean"},
    {"id": "syn-s2", "share": 0.25, "voices": ["de-a", "de-c", "en-b"], "snr_db": 25, "opus_kbps": None,
     "level_var": True, "note": "light noise, level variation"},
    {"id": "syn-s3", "share": 0.25, "voices": ["de-b", "de-c", "en-a"], "snr_db": 15, "opus_kbps": 24,
     "level_var": True, "note": "noisy, Opus 24 kbps round trip"},
    {"id": "syn-s4", "share": 0.25, "voices": ["de-a", "de-b", "de-c", "en-b"], "snr_db": 20, "opus_kbps": 16,
     "level_var": True, "note": "Opus 16 kbps, four speakers"},
]


def fill(rng, text, en, lang="de"):
    """Fill {slots} with fictional values; returns (text, english). English lines get English values."""
    day = rng.choice(DAYS)
    month = rng.choice(MONTHS)
    d = rng.randint(1, 28)
    n = rng.randint(2, 40)
    pct = rng.choice([5, 8, 12, 15, 20, 25, 30, 40])
    big = rng.choice([1200, 3400, 12400, 48000, 250000])
    money = rng.choice([5, 8, 12, 20, 45])
    dec = (rng.randint(0, 4), rng.randint(1, 9))
    people = rng.sample(PEOPLE, 2)
    vals = {
        "p": (people[0], people[0]), "p2": (people[1], people[1]),
        "c": (c := rng.choice(COMPANIES), c), "prod": (pr := rng.choice(PRODUCTS), pr),
        "day": day, "date": (f"{d}. {month[0]}", f"{month[1]} {d}"),
        "n": (str(n), str(n)), "n2": (str(n * 10 + rng.randint(1, 9)),) * 2,
        "pct": (f"{pct} Prozent", f"{pct} percent"),
        "big": (f"{big:,}".replace(",", "."), f"{big:,}"),
        "money": (f"{money}.000 Euro", f"{money},000 euros"),
        "ver": (v := f"{rng.randint(2, 6)}.{rng.randint(0, 9)}", v),
        "h": (str(h := rng.choice([9, 10, 11, 14, 15, 16])), f"{h if h <= 12 else h - 12} {'am' if h < 12 else 'pm'}"),
        "year": (str(rng.choice([2026, 2027])),) * 2,
        "dec": (f"{dec[0]},{dec[1]}", f"{dec[0]}.{dec[1]}"),
        "ticket": (str(rng.randint(1100, 4999)),) * 2, "ticket2": (str(rng.randint(5000, 9999)),) * 2,
    }
    for key, (de_val, en_val) in vals.items():
        text = text.replace("{" + key + "}", en_val if lang == "en" else de_val)
        en = en.replace("{" + key + "}", en_val)
    return text, en


def terms_in(text):
    """The fictional terms spoken in a text (whole words, any case)."""
    return [t for t in TERMS if re.search(rf"(?<!\w){re.escape(t['de'])}(?!\w)", text, re.I)]


# ------------------------------------------------------------------ text to speech
_PS_SCRIPT = r"""
param([string]$jobs)
Add-Type -AssemblyName System.Speech
$utf8 = New-Object System.Text.UTF8Encoding($false)
$list = [System.IO.File]::ReadAllText($jobs, $utf8) | ConvertFrom-Json
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$global:ev = New-Object System.Collections.ArrayList
$s.add_SpeakProgress({ param($o, $e) [void]$global:ev.Add(("{0}`t{1}`t{2}" -f [int]$e.AudioPosition.TotalMilliseconds, $e.CharacterPosition, $e.CharacterCount)) })
foreach ($j in $list) {
  $global:ev.Clear()
  $s.SelectVoice($j.voice)
  $s.Rate = [int]$j.rate
  $s.SetOutputToWaveFile($j.out, $fmt)
  $s.Speak([string]$j.text)
  $s.SetOutputToNull()
  [System.IO.File]::WriteAllLines($j.out + ".events.tsv", [string[]]$global:ev.ToArray(), $utf8)
}
$s.Dispose()
"""


def tts_backend():
    if sys.platform == "win32":
        return "win"
    if sys.platform == "darwin" and shutil.which("say"):
        return "mac"
    raise SystemExit("No text-to-speech found: needs Windows (System.Speech) or macOS (say).")


def synthesize(jobs, cache):
    """jobs: [{'key', 'voice', 'text'}]. Writes cache/<key>.wav (+ events); skips files already there."""
    backend = tts_backend()
    todo = [j for j in jobs if not (cache / f"{j['key']}.wav").exists()]
    if not todo:
        return
    print(f"Synthesizing {len(todo)} lines with the {backend} voices ...")
    if backend == "win":
        with tempfile.TemporaryDirectory() as tmp:
            script, job_file = Path(tmp) / "tts.ps1", Path(tmp) / "jobs.json"
            script.write_text(_PS_SCRIPT, encoding="utf-8")
            job_file.write_text(json.dumps([{
                "voice": VOICES[j["voice"]]["win"], "rate": VOICES[j["voice"]]["rate"], "text": j["text"],
                "out": str(cache / f"{j['key']}.wav")} for j in todo], ensure_ascii=False), encoding="utf-8")
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-File", str(script), str(job_file)], check=True)
        return
    for j in todo:   # macOS: 'say' rate is words per minute (~175 default)
        v = VOICES[j["voice"]]
        out = cache / f"{j['key']}.wav"
        subprocess.run(["say", "-v", v["mac"], "-r", str(175 + 15 * v["rate"]), "-o", str(out),
                        "--file-format=WAVE", "--data-format=LEI16@16000", j["text"]], check=True)


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16-bit PCM")
        raw = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        if w.getnchannels() > 1:
            raw = raw.reshape(-1, w.getnchannels()).mean(axis=1)
        if w.getframerate() != SR:
            raw = resample(raw, w.getframerate() / SR)
        return raw


def write_wav(path, audio):
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def resample(audio, factor):
    """Play `audio` `factor` times faster (and higher) by linear interpolation."""
    if factor == 1.0:
        return audio
    n = int(len(audio) / factor)
    return np.interp(np.arange(n) * factor, np.arange(len(audio)), audio).astype(np.float32)


def frame_rms(audio, hop=160):
    n = len(audio) // hop
    return np.sqrt(np.mean(audio[:n * hop].reshape(n, hop) ** 2, axis=1)) if n else np.zeros(0)


def word_timings(text, events, audio, factor):
    """[(word, start_s, end_s)] in the synthesized clip. Starts come from the TTS progress events
    (or are spread by text length); each end is the last loud 10 ms frame before the next start."""
    words = [(m.group(0), m.start()) for m in re.finditer(r"[\w][\w'.,:-]*\w|\w", text)]
    dur = len(audio) / SR
    starts = []
    if events:
        for _, pos in words:
            hit = [ms for ms, cpos, ccount in events if cpos <= pos < cpos + max(ccount, 1)]
            starts.append(hit[0] / 1000 / factor if hit else None)
    if not events or all(s is None for s in starts):
        total = sum(len(w) + 1 for w, _ in words) or 1
        acc, starts = 0, []
        for w, _ in words:
            starts.append(0.05 + (dur - 0.1) * acc / total)
            acc += len(w) + 1
    for i, s in enumerate(starts):   # fill gaps from neighbours, keep them increasing
        if s is None:
            starts[i] = starts[i - 1] + 0.05 if i else 0.0
        if i and starts[i] < starts[i - 1]:
            starts[i] = starts[i - 1]
    rms = frame_rms(audio)
    loud = rms > max(0.02 * (rms.max() if len(rms) else 0), 1e-4)
    out = []
    for i, (w, _) in enumerate(words):
        s = starts[i]
        e = starts[i + 1] if i + 1 < len(words) else dur
        fr = np.nonzero(loud[int(s * 100):max(int(e * 100), int(s * 100) + 1)])[0]
        end = s + (fr[-1] + 1) / 100 if len(fr) else min(e, s + 0.1)
        out.append((w, round(s, 3), round(max(end, s + 0.02), 3)))
    return out


def load_line(cache, key, text, voice):
    """(audio trimmed to speech, word timings relative to the trimmed audio)."""
    v = VOICES[voice]
    raw = read_wav(cache / f"{key}.wav")
    ev_path = cache / f"{key}.wav.events.tsv"
    events = []
    if ev_path.exists():
        for line in ev_path.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                events.append(tuple(int(p) for p in parts))
    audio = resample(raw, v["factor"])
    words = word_timings(text, events, audio, v["factor"])
    rms = frame_rms(audio)
    loud = np.nonzero(rms > 0.01 * (rms.max() if len(rms) else 1))[0]
    if not len(loud):
        return audio, words
    a, b = max(0, loud[0] - 2) * 160, min(len(audio), (loud[-1] + 3) * 160)
    shift = a / SR
    words = [(w, round(max(0.0, s - shift), 3), round(max(0.0, e - shift), 3)) for w, s, e in words]
    return audio[a:b], words


# ------------------------------------------------------------------ audio conditions
def pink_noise(rng, n):
    white = rng.standard_normal(n)
    spec = np.fft.rfft(white)
    f = np.arange(len(spec))
    spec[1:] /= np.sqrt(f[1:])
    spec[0] = 0
    out = np.fft.irfft(spec, n)
    return (out / (np.sqrt(np.mean(out ** 2)) + 1e-9)).astype(np.float32)


def add_noise(rng, audio, speech_rms, snr_db):
    n = len(audio)
    noise = 0.7 * pink_noise(rng, n) + 0.3 * rng.standard_normal(n).astype(np.float32)
    noise += 0.2 * np.sin(2 * np.pi * 50 * np.arange(n) / SR).astype(np.float32)   # mains hum
    noise /= np.sqrt(np.mean(noise ** 2)) + 1e-9
    return audio + noise * speech_rms / (10 ** (snr_db / 20))


def opus_round_trip(audio, kbps):
    """Encode to Opus and back with PyAV; returns (audio, note). Skips (returns the input) if PyAV fails."""
    try:
        import av
        buf = io.BytesIO()
        out = av.open(buf, "w", format="ogg")
        st = out.add_stream("libopus", rate=SR)
        st.bit_rate = kbps * 1000
        st.layout = "mono"
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        for i in range(0, len(pcm), SR):
            frame = av.AudioFrame.from_ndarray(pcm[i:i + SR].reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = SR
            for pkt in st.encode(frame):
                out.mux(pkt)
        for pkt in st.encode(None):
            out.mux(pkt)
        out.close()
        buf.seek(0)
        inp = av.open(buf, "r")
        res = av.AudioResampler(format="s16", layout="mono", rate=SR)
        chunks = []
        for frame in inp.decode(audio=0):
            for f in res.resample(frame):
                chunks.append(f.to_ndarray().reshape(-1))
        for f in res.resample(None):
            chunks.append(f.to_ndarray().reshape(-1))
        inp.close()
        dec = np.concatenate(chunks).astype(np.float32) / 32768
    except Exception as e:   # PyAV missing or blocked: keep the clean audio and say so
        return audio, f"opus skipped: {type(e).__name__}: {e}"
    lag = _lag(audio, dec)
    dec = dec[lag:] if lag >= 0 else np.concatenate([np.zeros(-lag, np.float32), dec])
    dec = np.concatenate([dec, np.zeros(max(0, len(audio) - len(dec)), np.float32)])[:len(audio)]
    return dec, f"opus {kbps} kbps round trip (aligned, lag {lag} samples)"


def _lag(a, b, window=SR * 20, max_lag=4000):
    """Samples by which b is late compared with a (cross-correlation on the first 20 s)."""
    a, b = a[:window], b[:window + max_lag]
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    corr = np.fft.irfft(np.fft.rfft(b, n) * np.conj(np.fft.rfft(a, n)), n)
    lags = np.r_[corr[:max_lag + 1], corr[-max_lag:]]
    idx = int(np.argmax(lags))
    return idx if idx <= max_lag else idx - len(lags)


# ------------------------------------------------------------------ sessions
def pick_pause(rng, prev_cat, cat):
    if cat == "B" or prev_cat == "B":
        return rng.uniform(0.2, 0.8)
    r = rng.random()
    if r < 0.2:
        return rng.uniform(0.2, 0.45)   # same breath: the app may join these
    if r < 0.7:
        return rng.uniform(0.45, 1.3)
    return rng.uniform(1.3, 3.0)


def plan_session(rng, sess, seconds):
    """Script lines for one session until ~seconds of speech+pauses (estimated at 14 chars/s)."""
    cats = [c for c, w in WEIGHTS.items() for _ in range(w)]
    de_voices = [v for v in sess["voices"] if VOICES[v]["lang"] == "de"]
    en_voices = [v for v in sess["voices"] if VOICES[v]["lang"] == "en"]
    lines, t, prev_cat, last_voice = [], 0.0, None, None
    while t < seconds:
        cat = rng.choice(cats)
        if cat == prev_cat == "B" or (cat in ("M", "ME") and prev_cat in ("M", "ME")):
            continue
        tpl = rng.choice([x for x in TEMPLATES if x[0] == cat])
        lang = "en" if cat in ("E", "ME") else "de"
        text, en = fill(rng, tpl[1], tpl[2] or tpl[1], lang)
        pool = en_voices if lang == "en" else de_voices
        others = [v for v in pool if v != last_voice] or pool
        voice = rng.choice(others)
        pause = pick_pause(rng, prev_cat, cat)
        lines.append({"cat": cat, "lang": lang, "text": text, "en": en, "voice": voice, "pause_before": pause})
        t += pause + len(text) / 14
        prev_cat, last_voice = cat, voice
    return lines


def line_key(voice, text):
    return hashlib.sha1(f"{voice}|{VOICES[voice]['rate']}|{text}".encode("utf-8")).hexdigest()[:16]


def build_session(rng, sess, lines, cache, out_dir):
    pieces, utts, t = [], [], 1.0   # one second of lead-in silence
    pieces.append(np.zeros(SR, np.float32))
    soft_every = 7
    for i, ln in enumerate(lines):
        audio, words = load_line(cache, line_key(ln["voice"], ln["text"]), ln["text"], ln["voice"])
        tags = [ln["cat"]]
        gain_db = rng.uniform(-10, 0) if sess["level_var"] else 0.0
        if sess["level_var"] and rng.random() < 0.06:   # near the app's silence threshold
            gain_db = -22.0
            tags.append("quiet")
        audio = audio * (10 ** (gain_db / 20))
        if i % soft_every == 3:   # soft start: the first 300 ms fade in
            ramp = min(len(audio), int(0.3 * SR))
            audio = audio.copy()
            audio[:ramp] *= np.linspace(0.05, 1.0, ramp, dtype=np.float32)
            tags.append("soft_start")
        dur = len(audio) / SR
        if dur > 12.0:
            tags.append("long")
        if i:
            gap = np.zeros(int(ln["pause_before"] * SR), np.float32)
            pieces.append(gap)
            t += len(gap) / SR
        start = t
        pieces.append(audio.astype(np.float32))
        t += dur
        utts.append({
            "id": f"{sess['id']}-u{i:03d}", "start": round(start, 3), "end": round(t, 3),
            "speaker": ln["voice"], "lang": ln["lang"], "text": ln["text"], "en": ln["en"],
            "terms": [x["de"] for x in terms_in(ln["text"])],
            "en_terms": [x["en"] for x in terms_in(ln["text"])],
            "tags": tags, "gain_db": round(gain_db, 1),
            "words": [[w, round(start + s, 3), round(start + e, 3)] for w, s, e in words],
        })
    pieces.append(np.zeros(3 * SR, np.float32))   # trailing silence so the last line can finish
    audio = np.concatenate(pieces)
    notes = [sess["note"]]
    if sess["snr_db"] is not None:
        voiced = np.concatenate([audio[int(u["start"] * SR):int(u["end"] * SR)] for u in utts])
        audio = add_noise(np.random.default_rng(rng.randint(0, 2 ** 31)), audio,
                          float(np.sqrt(np.mean(voiced ** 2))), sess["snr_db"])
    if sess["opus_kbps"]:
        audio, note = opus_round_trip(audio, sess["opus_kbps"])
        notes.append(note)
    peak = float(np.max(np.abs(audio)))
    if peak > 0.98:
        audio = audio * (0.98 / peak)
    path = out_dir / f"{sess['id']}.wav"
    write_wav(path, audio)
    entry = {
        "id": sess["id"], "file": path.relative_to(EVAL).as_posix(), "sha256": sha256(path),
        "duration": round(len(audio) / SR, 2), "sr": SR, "voices": sess["voices"],
        "conditions": {"snr_db": sess["snr_db"], "opus_kbps": sess["opus_kbps"],
                       "level_variation": sess["level_var"], "notes": notes},
        "utterances": utts,
    }
    (out_dir / f"{sess['id']}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1), encoding="utf-8")
    return entry


# ------------------------------------------------------------------ speaker clips (wave 2 reuse)
SPEAKER_SCRIPTS = [
    ["de-a", "de-b", "de-c"],
    ["de-b", "de-c", "de-a"],
    ["de-a", "de-c", "en-a"],
]


def plan_speaker_clip(rng, voices, seconds=100):
    lines, t, i = [], 0.0, 0
    cats = ["S", "S", "Q", "V", "C", "B", "N"]
    while t < seconds:
        voice = voices[i % len(voices)] if rng.random() < 0.8 else rng.choice(voices)
        lang = VOICES[voice]["lang"]
        pool = [x for x in TEMPLATES if (x[0] == "E") == (lang == "en") and (lang == "en" or x[0] in cats)]
        tpl = rng.choice(pool)
        text, en = fill(rng, tpl[1], tpl[2] or tpl[1], lang)
        pause = rng.uniform(0.2, 1.5)
        lines.append({"cat": tpl[0], "lang": lang, "text": text, "en": en, "voice": voice, "pause_before": pause})
        t += pause + len(text) / 14
        i += 1
    return lines


# ------------------------------------------------------------------ manifest
def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_manifest():
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {"version": 1, "terms": TERMS, "sets": {}}


def save_manifest(man):
    MANIFEST.write_text(json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def strip_words(entry):
    """Manifest copy of a session: utterance references without per-word timings (those stay in the
    sidecar JSON next to the audio, because they depend on the TTS engine version)."""
    return {**entry, "utterances": [{k: v for k, v in u.items() if k != "words"} for u in entry["utterances"]]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--minutes", type=float, default=40.0, help="total length of the meeting sessions")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--no-speakers", action="store_true", help="skip the three-voice speaker clips")
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)
    cache = DATA / "tts-cache"
    cache.mkdir(parents=True, exist_ok=True)
    plans = [(s, plan_session(rng, s, args.minutes * 60 * s["share"])) for s in SESSIONS]
    spk_plans = [] if args.no_speakers else [
        ({"id": f"spk-{i + 1}", "voices": v, "snr_db": None, "opus_kbps": None, "level_var": False,
          "note": "three voices, clean"}, plan_speaker_clip(rng, v)) for i, v in enumerate(SPEAKER_SCRIPTS)]
    jobs = {line_key(ln["voice"], ln["text"]): {"key": line_key(ln["voice"], ln["text"]), "voice": ln["voice"],
                                                  "text": ln["text"]}
            for _, lines in plans + spk_plans for ln in lines}
    synthesize(list(jobs.values()), cache)

    man = load_manifest()
    man["terms"] = TERMS
    sessions = [build_session(rng, s, lines, cache, DATA / "synthetic") for s, lines in plans]
    man["sets"]["synthetic"] = {
        "generator": "eval/make_synthetic.py", "seed": args.seed, "minutes": args.minutes,
        "tts": tts_backend(), "sessions": [strip_words(e) for e in sessions],
    }
    if spk_plans:
        clips = [build_session(rng, s, lines, cache, DATA / "speakers") for s, lines in spk_plans]
        man["sets"]["speakers"] = {
            "generator": "eval/make_synthetic.py", "seed": args.seed,
            "note": "three-voice clips for speaker labelling; utterances carry the speaker",
            "sessions": [strip_words(e) for e in clips]}
    save_manifest(man)
    total = sum(e["duration"] for e in sessions)
    n = sum(len(e["utterances"]) for e in sessions)
    print(f"Wrote {len(sessions)} sessions, {total / 60:.1f} min, {n} utterances -> {DATA / 'synthetic'}")
    for e in sessions:
        print(f"  {e['id']}: {e['duration'] / 60:.1f} min, {len(e['utterances'])} lines, {'; '.join(e['conditions']['notes'])}")
    print(f"Manifest: {MANIFEST}")


if __name__ == "__main__":
    main()
