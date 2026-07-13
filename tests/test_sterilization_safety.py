"""Sterilization physical-safety regression tests (C1/H1/M1 + runtime race/state).

Drives the REAL KlipperArayuzu production entry points (_start_uv/_start_hepa,
_stop_uv/_stop_hepa, closeEvent) and asserts safe behavior. Only the network
transport, dialogs and threads are faked — NO real HTTP is ever issued and NO
real sleep is used, so no real UV/HEPA/fan/heater/motor can be triggered.

Covered:
  * M1  — START timer only starts on confirmed delivery; START sent once.
  * C1/H1 — closeEvent attempts STOP for active UV/HEPA (+ idle_timeout backstop).
  * Close race — START landing after closeEvent's STOP is followed by a STOP.
  * Runtime race — user pressing Stop while a START is still in flight: the late
    START must NOT (re)start the timer; the last effective macro must be STOP.
  * Request state — desired-state + per-device monotonic epoch reject stale
    callbacks; a STOP-inflight guard prevents duplicate concurrent STOP workers.
  * STOP-UI — STOP delivery failure never shows a clean idle "Start" (state
    unknown); retry re-enabled.
  * No duplicate source definitions of critical methods (AST).

OFFSCREEN logic/state test, NOT a live-GUI or real-device test. "confirmed" =
"last confirmed command DELIVERY (HTTP 200)", NOT physical sensor feedback.

Run: python tests/test_sterilization_safety.py
"""
import os, re, ast, time, types
from types import SimpleNamespace
from _util import Checker
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox

import ui.main_window as MW
from ui.main_window import KlipperArayuzu

_MW_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "main_window.py")
_KLIPPER_TXT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "klipper.txt")


class _FakeReqExc(Exception):
    pass


class _FakeResp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


class _FakeRequests:
    """Records every POST across ALL transport paths (they all reach requests.post).

    Two distinct ledgers, and the difference is the whole point of the ambiguous-
    delivery tests:

      * ``posts``     — what the CLIENT sent (what our code believes it did).
      * ``effective`` — what the SERVER actually executed (physical reality). A macro
        lands here even when the client never learns about it.

    Modes ("mode" = default, "script_modes" = per-macro override):
      ok                     -> executed + HTTP 200            (client knows)
      raise                  -> NOT executed + RequestException (client knows it failed)
      http                   -> NOT executed + HTTP 500         (client knows it failed)
      executed_then_timeout  -> EXECUTED but the response is lost to a read timeout.
                                Client sees ok=False while the output is physically ON.
                                This is the case a naive "not ok -> nothing happened"
                                assumption gets fatally wrong.
    """
    class exceptions:
        RequestException = _FakeReqExc

    def __init__(self):
        self.mode = "ok"
        self.script_modes = {}    # macro -> mode override
        self.posts = []           # client-side ledger
        self.effective = []       # server-side ledger (physical truth)
        self.gets = []

    def _mode_for(self, script):
        return self.script_modes.get(script, self.mode)

    def post(self, url, json=None, files=None, data=None, timeout=None):
        script = (json or {}).get("script")
        self.posts.append({"url": url, "script": script, "timeout": timeout})
        mode = self._mode_for(script)
        if mode == "executed_then_timeout":
            if script:
                self.effective.append(script)     # output IS on / off — client won't know
            raise self.exceptions.RequestException(
                "read timeout AFTER the server executed the macro (fake)")
        if mode == "raise":
            raise self.exceptions.RequestException("offline (fake)")
        if mode == "http":
            return _FakeResp(500, {"error": {"message": "reddedildi (fake)"}})
        if script:
            self.effective.append(script)
        return _FakeResp(200, {"result": "ok"})

    def get(self, url, timeout=None):
        self.gets.append(url)
        if self.mode == "raise":
            raise self.exceptions.RequestException("offline (fake)")
        return _FakeResp(200, {"result": {"status": {}}})


class _SyncThread:
    """Runs the target inline (deterministic; no real thread, no sleep)."""
    def __init__(self, target=None, daemon=None, name=None, args=(), kwargs=None):
        self._t, self._a, self._k = target, args, kwargs or {}

    def start(self):
        if self._t:
            self._t(*self._a, **self._k)

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return False


class _DeferredThread:
    """Queues targets so a worker can be left 'in flight' until run_all()."""
    pending = []

    def __init__(self, target=None, daemon=None, name=None, args=(), kwargs=None):
        self._t, self._a, self._k = target, args, kwargs or {}

    def start(self):
        _DeferredThread.pending.append((self._t, self._a, self._k))

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return bool(_DeferredThread.pending)

    @classmethod
    def run_all(cls):
        while cls.pending:
            t, a, k = cls.pending.pop(0)
            if t:
                t(*a, **k)


def _scripts(fake, name):
    return [p for p in fake.posts if p.get("script") == name]


def _macro_order(fake, names):
    return [p["script"] for p in fake.posts if p.get("script") in names]


def _eff_order(fake, names):
    """Macros the SERVER actually executed, in order. The last one is what the
    hardware is left doing — the only ordering that matters for physical safety."""
    return [m for m in fake.effective if m in names]


def _active(t):
    return t is not None and t.isActive()


def _confirmed(w, which):
    return getattr(w, f"_{which}_output_confirmed_on", None)


def _mk_window():
    fake = _FakeRequests()
    MW.requests = fake
    w = KlipperArayuzu()
    if getattr(w, "_status_timer", None) is not None:
        w._status_timer.stop()
    w._banners = []
    w._show_banner = lambda msg, kind="error": w._banners.append((kind, msg))
    w._fake = fake
    return w, fake


def _force_running(w, which, secs=600):
    """Put a row into 'running' WITHOUT going through START delivery."""
    if which == "uv":
        if w.uv_timer is None:
            w.uv_timer = QTimer(w)
            w.uv_timer.timeout.connect(w._uv_tick)
        w.uv_kalan_saniye = secs
        setattr(w, "_uv_start_inflight", False)
        setattr(w, "_uv_stop_inflight", False)
        setattr(w, "_uv_desired_on", True)
        setattr(w, "_uv_output_confirmed_on", True)
        w.uv_timer.start(100000)
    else:
        if w.hepa_timer is None:
            w.hepa_timer = QTimer(w)
            w.hepa_timer.timeout.connect(w._hepa_tick)
        w.hepa_kalan_saniye = secs
        setattr(w, "_hepa_start_inflight", False)
        setattr(w, "_hepa_stop_inflight", False)
        setattr(w, "_hepa_desired_on", True)
        setattr(w, "_hepa_output_confirmed_on", True)
        w.hepa_timer.start(100000)


def _fake_event():
    ev = SimpleNamespace(_accepted=False)
    ev.accept = lambda: setattr(ev, "_accepted", True)
    ev.ignore = lambda: None
    return ev


def _sync():
    old = MW.threading
    MW.threading = SimpleNamespace(Thread=_SyncThread)
    return old


def _deferred():
    _DeferredThread.pending.clear()
    old = MW.threading
    MW.threading = SimpleNamespace(Thread=_DeferredThread)
    return old


def _parse_sections(path):
    sections, cur, buf = {}, None, []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\[(.+?)\]\s*$", line)
            if m:
                if cur is not None:
                    sections[cur] = "".join(buf)
                cur, buf = m.group(1).strip(), []
            elif cur is not None:
                buf.append(line)
        if cur is not None:
            sections[cur] = "".join(buf)
    return sections


def _method_def_counts(path, names):
    tree = ast.parse(open(path, encoding="utf-8").read())
    counts = {n: 0 for n in names}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in counts:
            counts[node.name] += 1
    return counts


def run():
    c = Checker()
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    _rt = time
    MW.time = SimpleNamespace(sleep=lambda *a, **k: None, monotonic=_rt.monotonic,
                              time=_rt.time, perf_counter=_rt.perf_counter)

    # ===================== S1/S2 — START success ============================
    for dev, macro, start, timer_attr in (("uv", "START_UV", "_start_uv", "uv_timer"),
                                           ("hepa", "START_HEPA", "_start_hepa", "hepa_timer")):
        w, fake = _mk_window(); fake.mode = "ok"
        _old = _sync()
        try:
            getattr(w, start)()
        finally:
            MW.threading = _old
        c.chk(f"S {macro} sent EXACTLY once", len(_scripts(fake, macro)) == 1, len(_scripts(fake, macro)))
        c.chk(f"S {dev} timer running after confirmed START", _active(getattr(w, timer_attr)))
        c.chk(f"S {dev} confirmed-delivery = ON", _confirmed(w, dev) is True)

    # ===================== S3/S4 — START fail -> no timer [M1] ==============
    # NOTE: a failed START is NOT proof the output stayed off (the response may have
    # been lost after the macro ran), so the row must NOT fall back to a clean idle
    # "Start" here: mode="raise" fails the compensating STOP too, leaving the state
    # genuinely unknown. Timer/banner assertions are unchanged from v3.
    for dev, start, macro_x, timer_attr, btn in (
            ("uv", "_start_uv", "STOP_UV", "uv_timer", "uv_start_btn"),
            ("hepa", "_start_hepa", "STOP_HEPA", "hepa_timer", "hepa_start_btn")):
        w, fake = _mk_window(); fake.mode = "raise"
        _old = _sync()
        try:
            getattr(w, start)()
        finally:
            MW.threading = _old
        c.chk(f"S3/4 {dev} timer NOT running on failed START [M1]", not _active(getattr(w, timer_attr)))
        c.chk(f"S3/4 {dev} banner shown", len(w._banners) >= 1)
        c.chk(f"S3/4 {dev} compensating {macro_x} attempted (delivery unproven)",
              len(_scripts(fake, macro_x)) == 1, len(_scripts(fake, macro_x)))
        c.chk(f"S3/4 {dev} UI NOT a clean 'Start' while state is unknown",
              getattr(w, btn).text() != "Start", getattr(w, btn).text())

    # ============ U1/U2 — AMBIGUOUS START: executed, response lost =========
    # The server RAN the macro (output physically ON) but the reply died in a read
    # timeout, so the client sees ok=False. Treating that as "it never ran" leaves
    # UV/HEPA silently energized. A compensating STOP must go out, and the LAST
    # EFFECTIVE macro (server-side) must be the STOP.
    for dev, start, macro_s, macro_x, timer_attr, btn in (
            ("uv", "_start_uv", "START_UV", "STOP_UV", "uv_timer", "uv_start_btn"),
            ("hepa", "_start_hepa", "START_HEPA", "STOP_HEPA", "hepa_timer", "hepa_start_btn")):
        w, fake = _mk_window()
        fake.script_modes = {macro_s: "executed_then_timeout"}   # STOP_* still gets 200
        _old = _sync()
        try:
            getattr(w, start)()
        finally:
            MW.threading = _old
        eff = _eff_order(fake, (macro_s, macro_x))
        c.chk(f"U[{dev}] ambiguous START -> compensating {macro_x} sent",
              len(_scripts(fake, macro_x)) == 1, len(_scripts(fake, macro_x)))
        c.chk(f"U[{dev}] LAST EFFECTIVE macro is {macro_x} (output left OFF)",
              eff and eff[-1] == macro_x, eff)
        c.chk(f"U[{dev}] timer NOT started", not _active(getattr(w, timer_attr)))
        c.chk(f"U[{dev}] UI never shows 'Running...'", getattr(w, btn).text() != "Running...")
        c.chk(f"U[{dev}] desired_on False", getattr(w, f"_{dev}_desired_on") is False)
        c.chk(f"U[{dev}] confirmed False (compensating STOP delivered)",
              _confirmed(w, dev) is False)
        c.chk(f"U[{dev}] safe idle 'Start' once output is confirmed OFF",
              getattr(w, btn).text() == "Start", getattr(w, btn).text())
        c.chk(f"U[{dev}] user IS told the START failed", len(w._banners) >= 1)

    # ===== U3/U4 — ambiguous START + compensating STOP ALSO undelivered ====
    for dev, start, stop, macro_s, macro_x, timer_attr, btn, lbl, sbtn in (
            ("uv", "_start_uv", "_stop_uv", "START_UV", "STOP_UV", "uv_timer",
             "uv_start_btn", "uv_kalan_sure_lbl", "uv_stop_btn"),
            ("hepa", "_start_hepa", "_stop_hepa", "START_HEPA", "STOP_HEPA", "hepa_timer",
             "hepa_start_btn", "hepa_kalan_sure_lbl", "hepa_stop_btn")):
        w, fake = _mk_window()
        fake.script_modes = {macro_s: "executed_then_timeout", macro_x: "raise"}
        _old = _sync()
        try:
            getattr(w, start)()
        finally:
            MW.threading = _old
        c.chk(f"U[{dev}] compensating {macro_x} attempted", len(_scripts(fake, macro_x)) == 1)
        c.chk(f"U[{dev}] timer NOT started", not _active(getattr(w, timer_attr)))
        c.chk(f"U[{dev}] button shows 'STOP failed'", getattr(w, btn).text() == "STOP failed",
              getattr(w, btn).text())
        c.chk(f"U[{dev}] label shows 'STATE?'", getattr(w, lbl).text() == "STATE?",
              getattr(w, lbl).text())
        c.chk(f"U[{dev}] Start disabled (output may be ON)", not getattr(w, btn).isEnabled())
        c.chk(f"U[{dev}] desired_on False", getattr(w, f"_{dev}_desired_on") is False)
        c.chk(f"U[{dev}] confirmed kept CONSERVATIVELY ON/unknown", _confirmed(w, dev) is True)
        c.chk(f"U[{dev}] error banner shown", len(w._banners) >= 1)
        c.chk(f"U[{dev}] Stop retry available", getattr(w, sbtn).isEnabled())
        fake.script_modes[macro_x] = "ok"          # retry: STOP now gets through
        _old = _sync()
        try:
            getattr(w, stop)()
        finally:
            MW.threading = _old
        c.chk(f"U[{dev}] retry STOP -> confirmed OFF", _confirmed(w, dev) is False)
        c.chk(f"U[{dev}] retry STOP -> idle 'Start'", getattr(w, btn).text() == "Start")
        c.chk(f"U[{dev}] retry STOP -> Start enabled", getattr(w, btn).isEnabled())

    # ===== U5 — stale callback born from an ambiguous START drives nothing =
    w, fake = _mk_window()
    w._uv_command_epoch = 11
    w._on_uv_start_finished(5, False, "ambiguous (stale token)")
    c.chk("U5 stale ambiguous-START callback starts no timer", not _active(w.uv_timer))
    c.chk("U5 stale ambiguous-START callback shows no 'Running...'",
          w.uv_start_btn.text() != "Running...")
    w._on_uv_start_finished(5, True, "late success (stale token)")
    c.chk("U5 stale late-success START callback starts no timer either",
          not _active(w.uv_timer))

    # ===== U6 — user hits Stop WHILE the compensating STOP is in flight ====
    w, fake = _mk_window()
    fake.script_modes = {"START_UV": "executed_then_timeout"}
    _fired = {"n": 0}
    _real_post = fake.post

    def _hooked(url, json=None, files=None, data=None, timeout=None):
        if (json or {}).get("script") == "STOP_UV" and _fired["n"] == 0:
            _fired["n"] = 1
            w._stop_uv()          # user presses Stop mid-compensation
        return _real_post(url, json=json, files=files, data=data, timeout=timeout)
    fake.post = _hooked
    _old = _sync()
    try:
        w._start_uv()
    finally:
        MW.threading = _old
    ueff = _eff_order(fake, ("START_UV", "STOP_UV"))
    c.chk("U6 last effective macro is STOP_UV", ueff and ueff[-1] == "STOP_UV", ueff)
    c.chk("U6 timer not started", not _active(w.uv_timer))
    c.chk("U6 no 'Running...' UI", w.uv_start_btn.text() != "Running...")
    c.chk("U6 desired_on False", w._uv_desired_on is False)
    c.chk("U6 confirmed False", _confirmed(w, "uv") is False)
    c.chk("U6 no stop-inflight leak", w._uv_stop_inflight is False)

    # ===================== R1/R2 [A/B] — runtime START pending -> user Stop =
    for dev, start, stop, macro_s, macro_x, timer_attr, btn in (
            ("uv", "_start_uv", "_stop_uv", "START_UV", "STOP_UV", "uv_timer", "uv_start_btn"),
            ("hepa", "_start_hepa", "_stop_hepa", "START_HEPA", "STOP_HEPA", "hepa_timer", "hepa_start_btn")):
        w, fake = _mk_window(); fake.mode = "ok"
        real_pmb = w._post_moonraker_blocking

        def racey(endpoint, payload=None, timeout=1.5, _ms=macro_s, _stop=stop, _win=w, _real=real_pmb):
            if (payload or {}).get("script") == _ms:
                getattr(_win, _stop)()                    # user presses Stop mid-START
                return _real(endpoint, payload, timeout)  # START lands AFTER the STOP
            return _real(endpoint, payload, timeout)
        w._post_moonraker_blocking = racey
        _old = _sync()
        try:
            getattr(w, start)()
        finally:
            MW.threading = _old
        order = _macro_order(fake, (macro_s, macro_x))
        c.chk(f"R[{dev}] last effective macro is {macro_x} [runtime race]",
              order and order[-1] == macro_x, order)
        c.chk(f"R[{dev}] late START did NOT start timer", not _active(getattr(w, timer_attr)))
        c.chk(f"R[{dev}] UI not left 'Running...'", getattr(w, btn).text() != "Running...")
        c.chk(f"R[{dev}] desired_on == False after Stop", getattr(w, f"_{dev}_desired_on", None) is False)
        c.chk(f"R[{dev}] confirmed == False at end", _confirmed(w, dev) is False)

    # ===================== R3/R4 [C/D] — double Stop -> one STOP worker =====
    for dev, macro_x, stop in (("uv", "STOP_UV", "_stop_uv"), ("hepa", "STOP_HEPA", "_stop_hepa")):
        w, fake = _mk_window(); fake.mode = "ok"
        _force_running(w, dev)
        _old = _deferred()
        try:
            getattr(w, stop)()      # STOP worker DEFERRED (in flight)
            getattr(w, stop)()      # rapid second Stop -> guard must suppress
            _DeferredThread.run_all()
        finally:
            MW.threading = _old
        c.chk(f"R[{dev}] double Stop -> exactly ONE {macro_x}",
              len(_scripts(fake, macro_x)) == 1, len(_scripts(fake, macro_x)))

    # ===================== R5 [I] — timer timeout + user Stop same turn =====
    w, fake = _mk_window(); fake.mode = "ok"
    _force_running(w, "uv", secs=1)
    _old = _deferred()
    try:
        w._uv_tick()        # 1 -> 0 -> auto _stop_uv (deferred STOP worker)
        w._stop_uv()        # user Stop same turn -> guard suppresses
        _DeferredThread.run_all()
    finally:
        MW.threading = _old
    c.chk("R[I] timeout + user Stop -> exactly ONE STOP_UV", len(_scripts(fake, "STOP_UV")) == 1,
          len(_scripts(fake, "STOP_UV")))

    # ===================== G/H — STOP fail UI + retry success ===============
    w, fake = _mk_window(); fake.mode = "raise"
    _force_running(w, "uv")
    _old = _sync()
    try:
        w._stop_uv()
    finally:
        MW.threading = _old
    c.chk("G STOP_UV attempted", len(_scripts(fake, "STOP_UV")) >= 1)
    c.chk("G UI NOT clean 'Start' on STOP failure [item3]", w.uv_start_btn.text() != "Start")
    c.chk("G Start button disabled (output may be ON)", not w.uv_start_btn.isEnabled())
    c.chk("G Stop button re-enabled for retry", w.uv_stop_btn.isEnabled())
    c.chk("G confirmed stays ON/unknown on STOP failure", _confirmed(w, "uv") is True)
    c.chk("G stop-inflight cleared after failure", getattr(w, "_uv_stop_inflight", None) is False)
    c.chk("G error banner shown", len(w._banners) >= 1)
    # retry success
    w._fake.mode = "ok"
    _old = _sync()
    try:
        w._stop_uv()
    finally:
        MW.threading = _old
    c.chk("H retry STOP success -> confirmed OFF", _confirmed(w, "uv") is False)
    c.chk("H retry -> UI idle 'Start'", w.uv_start_btn.text() == "Start")
    c.chk("H retry -> Start enabled", w.uv_start_btn.isEnabled())
    c.chk("H retry -> stop-inflight False", getattr(w, "_uv_stop_inflight", None) is False)

    # ===================== ORD1/ORD2 [E/F] — stale callback ordering ========
    w, fake = _mk_window()
    try:
        w._uv_command_epoch = 7
        w._uv_output_confirmed_on = True
        w._on_uv_stop_finished(7, True, "")            # current STOP success
        c.chk("E current STOP success -> confirmed OFF", _confirmed(w, "uv") is False)
        c.chk("E current STOP success -> UI idle", w.uv_start_btn.text() == "Start")
        w._on_uv_stop_finished(3, False, "late fail")  # STALE failure arrives late
        c.chk("E stale STOP failure does NOT clobber idle [ordering]",
              w.uv_start_btn.text() == "Start")
        c.chk("E stale STOP failure does NOT flip confirmed back ON",
              _confirmed(w, "uv") is False)
    except TypeError:
        c.chk("E handler is token-aware (int,bool,str)", False, "old 2-arg signature")

    w, fake = _mk_window()
    try:
        w._uv_command_epoch = 9
        w._uv_desired_on = False                       # user has Stopped since
        w._on_uv_start_finished(4, True, "stale")      # OLD START success, stale token
        c.chk("F stale START success does NOT start timer [ordering]", not _active(w.uv_timer))
        c.chk("F stale START success does NOT show Running", w.uv_start_btn.text() != "Running...")
    except TypeError:
        c.chk("F handler is token-aware (int,bool,str)", False, "old 2-arg signature")

    # ===================== C1/H1/both — closeEvent stops active outputs =====
    w, fake = _mk_window(); fake.mode = "ok"; _force_running(w, "uv")
    ev = _fake_event(); w.closeEvent(ev)
    c.chk("C1 closeEvent STOP_UV once", len(_scripts(fake, "STOP_UV")) == 1)
    c.chk("C1 UV timer stopped", not _active(w.uv_timer))
    c.chk("C1 event accepted", ev._accepted)

    w, fake = _mk_window(); fake.mode = "ok"; _force_running(w, "hepa")
    ev = _fake_event(); w.closeEvent(ev)
    c.chk("H1 closeEvent STOP_HEPA once", len(_scripts(fake, "STOP_HEPA")) == 1)
    c.chk("H1 HEPA timer stopped", not _active(w.hepa_timer))

    w, fake = _mk_window(); fake.mode = "ok"; _force_running(w, "uv"); _force_running(w, "hepa")
    ev = _fake_event(); w.closeEvent(ev)
    c.chk("CL both STOP_UV once", len(_scripts(fake, "STOP_UV")) == 1)
    c.chk("CL both STOP_HEPA once", len(_scripts(fake, "STOP_HEPA")) == 1)

    # ===================== CL race backstop (START pending at close) ========
    w, fake = _mk_window(); fake.mode = "ok"
    ev = _fake_event(); real_pmb = w._post_moonraker_blocking

    def racey_close(endpoint, payload=None, timeout=1.5):
        if (payload or {}).get("script") == "START_UV":
            w.closeEvent(ev)
            return real_pmb(endpoint, payload, timeout)
        return real_pmb(endpoint, payload, timeout)
    w._post_moonraker_blocking = racey_close
    _old = _sync()
    try:
        w._start_uv()
    finally:
        MW.threading = _old
    uvo = _macro_order(fake, ("START_UV", "STOP_UV"))
    c.chk("CL close-race last macro is STOP_UV", uvo and uvo[-1] == "STOP_UV", uvo)
    c.chk("CL close-race timer not started", not _active(w.uv_timer))

    # ===================== CL[J] — close while runtime STOP inflight ========
    w, fake = _mk_window(); fake.mode = "ok"; _force_running(w, "uv")
    _old = _deferred()
    try:
        w._stop_uv()                 # runtime STOP worker DEFERRED (inflight)
        ev = _fake_event(); w.closeEvent(ev)
        _DeferredThread.run_all()    # stale runtime STOP result arrives during teardown
    finally:
        MW.threading = _old
    jo = _macro_order(fake, ("START_UV", "STOP_UV"))
    c.chk("J close during runtime STOP: event accepted", ev._accepted)
    c.chk("J last effective macro STOP_UV", jo and jo[-1] == "STOP_UV", jo)
    c.chk("J no START_UV in order", "START_UV" not in jo)

    # ===================== CL offline / idempotent / spurious / confirmed ===
    w, fake = _mk_window(); fake.mode = "raise"; _force_running(w, "uv")
    ev = _fake_event(); t0 = time.monotonic(); w.closeEvent(ev); dt = time.monotonic() - t0
    c.chk("CL offline close accepted", ev._accepted)
    c.chk("CL offline STOP_UV attempted", len(_scripts(fake, "STOP_UV")) == 1)
    posts = _scripts(fake, "STOP_UV")
    c.chk("CL close-STOP bounded timeout <=1.5s",
          bool(posts) and posts[0]["timeout"] is not None and posts[0]["timeout"] <= 1.5)
    c.chk("CL offline close not blocked long (<3s)", dt < 3.0, f"{dt:.3f}s")

    w, fake = _mk_window(); fake.mode = "ok"; _force_running(w, "uv")
    w.closeEvent(_fake_event()); w.closeEvent(_fake_event())
    c.chk("CL closeEvent twice -> STOP_UV once", len(_scripts(fake, "STOP_UV")) == 1)

    w, fake = _mk_window(); fake.mode = "ok"
    ev = _fake_event(); w.closeEvent(ev)
    c.chk("CL nothing active -> no STOP_UV", len(_scripts(fake, "STOP_UV")) == 0)
    c.chk("CL nothing active -> no STOP_HEPA", len(_scripts(fake, "STOP_HEPA")) == 0)

    w, fake = _mk_window(); fake.mode = "ok"
    setattr(w, "_uv_output_confirmed_on", True)     # confirmed ON, no timer, not inflight
    ev = _fake_event(); w.closeEvent(ev)
    c.chk("CL confirmed-on (no timer) -> close STILL sends STOP_UV",
          len(_scripts(fake, "STOP_UV")) == 1, len(_scripts(fake, "STOP_UV")))

    # ===================== AST — no duplicate critical defs =================
    crit = ["_start_uv", "_stop_uv", "_on_uv_start_finished", "_on_uv_stop_finished",
            "_uv_reset_idle_ui", "_start_hepa", "_stop_hepa", "_on_hepa_start_finished",
            "_on_hepa_stop_finished", "_hepa_reset_idle_ui", "_begin_sterilization_start",
            "_begin_sterilization_stop", "_safe_stop_sterilization_on_close",
            "_deliver_stop_on_close", "_post_moonraker_blocking"]
    counts = _method_def_counts(_MW_PATH, crit)
    for n in crit:
        c.chk(f"AST {n} defined exactly once", counts[n] == 1, counts[n])

    # ===================== CFG — idle_timeout backstop ======================
    sec = _parse_sections(_KLIPPER_TXT)
    idle = sec.get("idle_timeout", "")
    c.chk("CFG STOP macros defined",
          all(f"gcode_macro {m}" in sec for m in ("START_UV", "STOP_UV", "START_HEPA", "STOP_HEPA")))
    c.chk("CFG idle_timeout calls STOP_UV", re.search(r"(?m)^\s*STOP_UV\s*$", idle) is not None)
    c.chk("CFG idle_timeout calls STOP_HEPA [H1 backstop]",
          re.search(r"(?m)^\s*STOP_HEPA\s*$", idle) is not None)
    c.chk("CFG uv_led default 0", re.search(r"value:\s*0", sec.get("output_pin uv_led", "")) is not None)
    c.chk("CFG hepa_fan default 0", re.search(r"value:\s*0", sec.get("output_pin hepa_fan", "")) is not None)

    c.report_and_exit("STERILIZATION SAFETY v3 — runtime race + request state (offscreen)")


if __name__ == "__main__":
    run()
