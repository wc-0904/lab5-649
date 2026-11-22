"""Pulse per second on the Pi, scheduled against absolute deadlines.

The STM32 nodes drive their edge from a TIM5 output compare, so no instruction
runs at the edge and the measured spread between them is about 20 microseconds.
The Pi cannot match that. It has no hardware comparator reachable from Python,
so the edge is produced by a thread that sleeps to an absolute deadline and then
writes a GPIO, and whatever the kernel adds between the wakeup and that write
lands directly on the edge.

Three things keep that as small as it can reasonably be:

- ``clock_nanosleep`` with ``TIMER_ABSTIME`` rather than a relative ``sleep``.
  A relative sleep accumulates the time spent computing the next deadline into
  the deadline itself; an absolute one cannot drift, and a late wakeup is
  absorbed rather than carried forward.
- ``CLOCK_REALTIME``, the same clock the cockpit stamps into REF, so the edge
  and the reference message cannot disagree about when a second begins.
- ``SCHED_FIFO`` on this thread alone, so an ordinary task cannot preempt the
  wakeup. Requires privilege; without it the thread still runs and the jitter is
  simply worse, which is reported rather than hidden.

Measuring this is the point, not decorating it. The thread records the error
between the deadline it asked for and the clock reading immediately after the
GPIO write, and :meth:`Pps.stats` reports it. That number is the honest answer
to why the uplink node cannot be a time reference of the same quality as the
microcontrollers it synchronizes.
"""
import ctypes
import ctypes.util
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
                 pulse_us=PULSE_US):
        """Prepare the pulse thread without starting it.

        :param pins: Object with set(pin, level), normally cockpit's Pins.
        :param pin: Index into that object's pin table.
        :param enabled: False to make every method a no-op.
        :param priority: SCHED_FIFO priority to request.
        :param period_us: Pulse period; only the tests use anything but a second.
        :param pulse_us: How long the pulse stays high.
        """
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
        self.errors_us = []

    def _sleep_until(self, deadline_us):
        """Sleep to an absolute deadline on CLOCK_REALTIME.

        :param deadline_us: Deadline in microseconds since the epoch.
        :return: True when clock_nanosleep was used, False on the fallback.
        """
        if self.libc is None:
            remaining = deadline_us - time.clock_gettime_ns(time.CLOCK_REALTIME) // 1000
            if remaining > 0:
                time.sleep(remaining / 1e6)
            return False
        target = _Timespec(tv_sec=deadline_us // 1000000,
                           tv_nsec=(deadline_us % 1000000) * 1000)
        # A signal can return EINTR early, so finish the deadline before going on.
        while self.libc.clock_nanosleep(CLOCK_REALTIME, TIMER_ABSTIME,
                                        ctypes.byref(target), None) != 0:
            if self.stop.is_set():
                break
        return True

    def _request_realtime(self):
        """Ask for SCHED_FIFO on the calling thread.

        :return: True when the policy was granted.
        """
        if self.libc is None:
            return False
        param = _SchedParam(sched_priority=self.priority)
        # Thread id zero means the calling thread.
        return self.libc.sched_setscheduler(0, SCHED_FIFO, ctypes.byref(param)) == 0

    def _run(self):
        """Drive the pulse until stopped."""
        self.realtime = self._request_realtime()
        while not self.stop.is_set():
            now_us = time.clock_gettime_ns(time.CLOCK_REALTIME) // 1000
            deadline = (now_us // self.period_us + 1) * self.period_us
            self._sleep_until(deadline)
            if self.stop.is_set():
                break
            self.pins.set(self.pin, True)
            # Read the clock straight after the write: the gap between this and
            # the deadline is the edge error, and it is the whole measurement.
            actual = time.clock_gettime_ns(time.CLOCK_REALTIME) // 1000
            with self.lock:
                self.edges += 1
                self.errors_us.append(actual - deadline)
            self._sleep_until(deadline + self.pulse_us)
            self.pins.set(self.pin, False)

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
