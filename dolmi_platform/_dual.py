"""SPK-5 (beta): capture the microphone and the speaker at once, shared by windows.py and mac.py.

Each source runs its own platform stream into a private queue; mix() adds them 100 ms at a time into
the worker's audio queue (the worker sees one ordinary stream) and records each source's energy for
that block in `log` (speakers.SourceLog), so the diarizer can tell the user's own lines ("You").
"""
import queue
import threading
import time

STEP = 1600          # 100 ms at 16 kHz: the capture chunk size
LAG_BLOCKS = 3       # a source that is this many blocks behind is treated as silent (it stalled)


def _rms(a):
    import numpy as np
    return float(np.sqrt(np.mean(a * a))) if len(a) else 0.0


def mix(mic_q, spk_q, out_q, stop, log=None, on_level=None, clock=time.time, failed=lambda: None):
    """Mix the two queues into out_q until stop is set; raises the first source error from failed()."""
    import numpy as np
    bufs = [np.zeros(0, np.float32), np.zeros(0, np.float32)]
    while not stop.is_set():
        err = failed()
        if err is not None:
            raise err
        got = False
        for i, q in enumerate((mic_q, spk_q)):
            while True:
                try:
                    bufs[i] = np.concatenate([bufs[i], np.asarray(q.get_nowait(), np.float32)])
                    got = True
                except queue.Empty:
                    break
        while min(map(len, bufs)) >= STEP or max(map(len, bufs)) >= LAG_BLOCKS * STEP:
            blocks = []
            for i in range(2):
                b, bufs[i] = bufs[i][:STEP], bufs[i][STEP:]
                blocks.append(np.pad(b, (0, STEP - len(b))))
            mic, spk = blocks
            out = np.clip(mic + spk, -1.0, 1.0).astype(np.float32)
            if log is not None:
                log.add(clock(), _rms(mic), _rms(spk))
            out_q.put(out)
            if on_level:
                on_level(_rms(out))
        if not got:
            stop.wait(0.02)


def dual(mic_fn, spk_fn, out_q, stop, mic_device=None, speaker_device=None, on_level=None, log=None):
    """Run mic_fn and spk_fn (the platform's mic_stream / loopback_stream) at once and mix them."""
    inner, errors = threading.Event(), []
    mic_q, spk_q = queue.Queue(), queue.Queue()

    def run(fn, q, device):
        try:
            fn(q, inner, device)
        except Exception as e:                 # surfaced by mix() so the app shows "Can't record audio"
            errors.append(e)

    threads = [threading.Thread(target=run, args=(mic_fn, mic_q, mic_device), daemon=True, name="dolmi-mic"),
               threading.Thread(target=run, args=(spk_fn, spk_q, speaker_device), daemon=True, name="dolmi-spk")]
    for t in threads:
        t.start()
    try:
        mix(mic_q, spk_q, out_q, stop, log=log, on_level=on_level,
            failed=lambda: errors[0] if errors else None)
    finally:
        inner.set()
        for t in threads:
            t.join(2)
