"""Software PPS following the same global timeline used for reference frames.

Absolute sleeps handle the final local wait; bounded sleeps refresh the mapping
when a follower receives new references. Linux scheduling and GPIO-write latency
still affect the edge and must be measured on the target. Real-time scheduling
is optional, and missing libc functions fall back to ordinary sleeping.
"""
import ctypes
import ctypes.util
import errno
from collections import deque
import os
import threading
import time

#: Microseconds in one second, the pulse period.
PERIOD_US = 1000000
#: How long the pulse stays high, microseconds. Only the rising edge is
#: measured; this just has to be visible on a scope and far from the next rise.
PULSE_US = 10000
#: POSIX clock id for CLOCK_REALTIME.
CLOCK_REALTIME = 0
#: Flag selecting an absolute rather than a relative deadline.
TIMER_ABSTIME = 1
#: Scheduling policy for the pulse thread.
SCHED_FIFO = 1


class _Timespec(ctypes.Structure):
    """POSIX struct timespec."""
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]


class _SchedParam(ctypes.Structure):
    """POSIX struct sched_param, which holds only the priority."""
    _fields_ = [("sched_priority", ctypes.c_int)]


def _libc():
    """Open libc for the two calls Python does not expose.

    :return: The loaded library, or None when it cannot be found.
    """
    name = ctypes.util.find_library("c")
    return ctypes.CDLL(name, use_errno=True) if name else None


class Pps:
    """Emit one rising edge per whole second of the master clock."""

    def __init__(self, pins, pin, enabled=True, priority=80, period_us=PERIOD_US,
                 pulse_us=PULSE_US, clock_us=None, report=None):
        """Prepare the pulse thread without starting it.

        :param pins: Object with set(pin, level), normally cockpit's Pins.
        :param pin: Index into that object's pin table.
        :param enabled: False to make every method a no-op.
        :param priority: SCHED_FIFO priority to request.
        :param period_us: Pulse period; only the tests use anything but a second.
        :param pulse_us: How long the pulse stays high.
        """
        self.clock_us = clock_us or (lambda: time.time_ns() // 1000)
        self.report = report
        self.pins = pins
        self.pin = pin
        self.period_us = period_us
        self.pulse_us = pulse_us
        self.enabled = enabled
        self.priority = priority
        self.libc = _libc() if enabled else None
        self.thread = None
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.realtime = False
        self.edges = 0
        self.errors_us = deque(maxlen=10000)

    def _sleep_until(self, deadline_us):
        """Sleep to an absolute deadline on CLOCK_REALTIME.

        :param deadline_us: Deadline in microseconds since the epoch.
        :return: True when clock_nanosleep was used, False on the fallback.
        """
        if self.libc is None or not hasattr(self.libc, "clock_nanosleep"):
            remaining = deadline_us - time.clock_gettime_ns(time.CLOCK_REALTIME) // 1000
            if remaining > 0:
                time.sleep(remaining / 1e6)
            return False
        target = _Timespec(tv_sec=deadline_us // 1000000,
                           tv_nsec=(deadline_us % 1000000) * 1000)
        # A signal can return EINTR early, so finish the deadline before going on.
        while not self.stop.is_set():
            rc = self.libc.clock_nanosleep(CLOCK_REALTIME, TIMER_ABSTIME,
                                           ctypes.byref(target), None)
            if rc == 0:
                return True
            if rc != errno.EINTR:
                remaining = deadline_us - time.time_ns() // 1000
                if remaining > 0:
                    self.stop.wait(remaining / 1e6)
                return False
        return False

    def _request_realtime(self):
        """Ask for SCHED_FIFO on the calling thread.

        :return: True when the policy was granted.
        """
        if self.libc is None or not hasattr(self.libc, "sched_setscheduler"):
            return False
        param = _SchedParam(sched_priority=self.priority)
        # Thread id zero means the calling thread.
        return self.libc.sched_setscheduler(0, SCHED_FIFO, ctypes.byref(param)) == 0

    def _run(self):
        """Drive the pulse until stopped."""
        self.realtime = self._request_realtime()
        last_target = 0
        while not self.stop.is_set():
            now_us = self.clock_us()
            deadline = max(last_target + self.period_us,
                           (now_us // self.period_us + 1) * self.period_us)
            # Refresh the global-to-local mapping until near the edge: a
            # follower's newest REF can refine its phase while we wait.
            while not self.stop.is_set():
                remaining = deadline - self.clock_us()
                if remaining <= 0:
                    break
                if remaining > 2000:
                    self.stop.wait(min((remaining - 1000) / 1e6, .01))
                else:
                    self._sleep_until(time.time_ns() // 1000 + remaining)
            if self.stop.is_set():
                break
            self.pins.set(self.pin, True)
            actual = self.clock_us()
            local = time.perf_counter_ns() // 1000
            with self.lock:
                self.edges += 1
                self.errors_us.append(actual - deadline)
            last_target = deadline
            # Pulse width is local elapsed time, independent of corrections.
            self.stop.wait(self.pulse_us / 1e6)
            self.pins.set(self.pin, False)
            if self.report is not None:
                self.report(actual, local, actual - deadline)

    def start(self):
        """Begin emitting edges."""
        if not self.enabled or self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name="pps", daemon=True)
        self.thread.start()

    def close(self):
        """Stop the thread and drop the pin."""
        if self.thread is None:
            return
        self.stop.set()
        self.thread.join(timeout=2.0)
        self.thread = None
        self.pins.set(self.pin, False)

    def stats(self):
        """Summarise the edge error since start.

        :return: (count, mean us, worst us, realtime policy granted).
        """
        with self.lock:
            samples = list(self.errors_us)
            realtime = self.realtime
        if not samples:
            return 0, 0.0, 0, realtime
        return (len(samples), sum(samples) / len(samples),
                max(samples, key=abs), realtime)
