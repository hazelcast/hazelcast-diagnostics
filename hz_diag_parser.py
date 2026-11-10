#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hz_diag_parser.py -- read a directory of Hazelcast diagnostics logs (several members, many files
each) and build one interactive HTML report.

It reads:
  HazelcastInstance          cluster size, master and member list, as seen by each member
  MemberAdded/MemberRemoved  membership changes (also Lifecycle, connections, migrations)
  Metric[[...]]=value        every metric: heap, os/runtime, gc, partitions, wan, executors, tcp
                             (per thread / pipeline too), operation queues, threads, invocations,
                             maps (per map), everything else
  OperationsProfiler         latency of the operations each member ran
  InvocationProfiler         latency of the invocations each member sent
  OperationHeartbeat         late operation heartbeats (and MemberHeartbeats)
  SlowOperations             the slow operation detector: slow invocations, their stack traces and details
  Invocations                the invocation sampler: the invocations pending at each sample, by operation, and the
                             ones pending longer than its slow threshold, with their details
  MapStoreLatency            how long each map's MapStore / MapLoader methods took (QueueStoreLatency and the like too)

Tabs: Overview, Latency, Slow operations, Invocations, Operations, Threads (stuck IO and operation threads, per-thread metrics),
Network, Transfer (bytes in and out of each member, per peer), WAN (wan.* metrics per publisher, WAN traffic, WAN
operations), Data structures (maps, queues, topics, caches, multimaps, replicated maps, executors... one kind at a
time), MapStore (the latency of the maps' MapStore / MapLoader calls), System, GC (from the GC logs), Cluster, CP Subsystem (raft.* metrics per CP group, CP objects, CP operations),
Configuration, Other. What a tab shows (operation, WAN publisher, CP group, kind, metric, pauses) is picked in a row of the bar
that stays at the top of the page.

Latency: calls per latency group, average and max, when operations were slow, per operation and member. Every
profiler bucket from 1.02 ms up is kept, so the report sets the groups: the fastest (< 4.1 ms by default, or
1.02 ms ... 65.5 ms), the slowest (>= 524 ms by default, or 131 ms ... 8.39 s; the buckets are powers of 2 us,
so 1.05 s and 2.1 s stand for 1 s and 2 s) and the slow threshold (131 ms by default); an operation list next to
Find picks one operation.

Invocations: from the invocation sampler (hazelcast.diagnostics.invocation.sample.period.seconds): the invocations
each member was waiting on at each sample, by operation (History and SlowHistory count them since the member
started: the growth from one dump to the next is the number at that sample), and every invocation it listed as
pending longer than its slow threshold (5 s by default), once: operation, data structure, partition (and the
partition thread on the target), target, how long it was seen pending, past its call timeout or not.

Transfer: how many bytes each member received and sent, per peer: each other member, each client host, WAN targets
and other clusters, from the counters of each connection (tcp.connection.in.bytesRead / out.bytesWritten, named
local->remote) matched across the members; the member totals from the IO threads. Measure (toolbar, or Shift-drag)
selects a span on any chart and shows what went through it: bytes for the byte counters, counts for the other
counters, average and max for the sizes.

Stuck threads: a thread that stays idle while its queue stays above 0, at two metrics dumps in a row at least.
For an IO thread: tasks waiting (taskQueueSize > 0) and no I/O since the dump before (idleTimeMillis); for an
operation thread: operations waiting and none completed (completedTotalCount unchanged). The console summary and
the Overview say how many.

Saturated executors: an executor whose pool is full (poolSize >= maximumPoolSize) while tasks wait in its queue
(queueSize > 0), at two metrics dumps in a row at least (executor.internal.* and the like). Shown on the
Operations tab and the Overview.

Member logs: when the members' own logs are in the directory too (hazelcast.log, an application's log with the
Hazelcast lines in it, console output with or without colours, rotated or .gz; java.util.logging, log4j, log4j2,
logback, a date after a level or a prefix, JSON lines, the time of day with the date in the file name), they get
their own tabs (Source: Member logs): Overview (warnings and errors, notable lines: OutOfMemoryError, partitions
lost, WAN replication problems, thread starvation / clock leap, clock jumps, suspected members, quorum failures,
split brain, slow and delayed operations...), Health monitor (the HealthMonitor lines charted), Exceptions (every
exception, grouped by class and root cause), Migrations (the master's migration plans, their progress and how they
ended, lost partitions, failed migrations and promotions) and CP Subsystem (groups, Raft roles and terms, elections,
sessions, persistence). A log is recognised by its Hazelcast lines ([host]:port [cluster] [version]) wherever they
are: a partial log, without the member's start or shutdown, works too. A directory with member logs only works too.

Configuration: the BuildInfo, SystemProperties and ConfigProperties sections (written at the top of each diagnostics
file): Hazelcast and Java versions, OS, heap and GC from the JVM arguments, every system and Hazelcast property side
by side for the members, what differs between them and what changed at a restart.

GC logs: GC logs in the directory too (JDK 9+ -Xlog:gc*, best with the time decoration, e.g.
-Xlog:gc*:file=gc.log:time,uptime; JDK 8 -XX:+PrintGCDetails -XX:+PrintGCDateStamps; rotated files) get a GC tab: every
pause (kind, cause, heap before and after), the GCs caused by humongous allocations (and the humongous regions when
gc+heap is logged), full GCs and evacuation failures, the heap after GC and the time in pauses. A GC log goes with the
member whose files are in the same folder; else the one whose host name is in its file name or folder names; else the
one in the nearest folder, then the one whose lifetime started with that JVM. A GC log that matches no member is
listed apart, not as a member. Lines with the JVM uptime only (the -Xlog default) are placed with the JVM start of
their member's lifetime: the dump time minus runtime.uptime in the diagnostics, checked against gc.minorCount. With
GC logs alone, the time axis is then the JVM uptime, one line per JVM.

Usage
  python3 hz_diag_parser.py <input_dir> [<output_dir>] [options]
  python3 hz_diag_parser.py diag/ out/ --member 10.0.0.1:5701 --from "27-10-2026 15:00" --to "27-10-2026 16:00"

Writes <output_dir>/report.html: one self-contained file (data embedded and compressed) that works
offline in a current browser. Needs Python 3.6+ and nothing else.

Resolution: --points max (the default) keeps every metric dump and every profiler dump; the charts
merge neighbouring points only while there are more points than pixels, so zooming in shows them
all. --points N makes a smaller file with about N points across the time range.
"""

import argparse
import array
import base64
import bisect
import calendar
import datetime
import gzip
import html
import json
import math
import operator
import os
import re
import statistics
import sys
import time

__version__ = "2.12.1"

# --------------------------------------------------------------------------- line formats
# "27-10-2026 15:21:16 1793114476412 Name[..."   (the epoch millis are optional)
TS_RE = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{4}) (\d{1,2}):(\d{2}):(\d{2})(?: (\d{10,16}))? (.*)$")
# diagnostics-10.0.0.1_5701-1793114476412-000.log (5.x) or diagnostics-10.0.0.1#5701-....log (older)
FILENAME_RE = re.compile(
    r"^.*diagnostics-(?P<host>.+?)[#_](?P<port>\d{1,5})-(?P<start>\d{10,16})(?:-(?P<idx>\d+))?\.log",
    re.IGNORECASE)
THIS_ADDRESS_RE = re.compile(r"thisAddress=\[([^\]]+)\]:(\d{1,5})")
ADDR_RE = re.compile(r"\[([^\]]+)\]:(\d{1,5})")
BUCKET_RE = re.compile(r"^(\d+)\.\.(\d+)\s*(ns|us|µs|ms|s)?$")
UNIT_US = {None: 1.0, "us": 1.0, "µs": 1.0, "ns": 0.001, "ms": 1000.0, "s": 1000000.0}
KNOWN_SECTION_RE = re.compile(r"(?:Metric\[\[|BuildInfo\[|SystemProperties\[|ConfigProperties\[|"
                              r"HazelcastInstance\[|OperationsProfiler\[|InvocationProfiler\[|Lifecycle\[|"
                              r"\w*(?:Store|Loader|Writer)Latency\[)")
# the store latency plugin (hazelcast.diagnostics.storeLatency.period.seconds): MapStoreLatency, QueueStoreLatency...
STORE_SECTION_RE = re.compile(r"^\w*(?:Store|Loader|Writer)Latency$")

PROFILERS = {"OperationsProfiler": "O", "OperationProfiler": "O",
             "InvocationProfiler": "I", "InvocationsProfiler": "I"}
# The latency buckets are powers of 2 microseconds; a bucket is kept as the exponent e of its upper bound
# (2^e - 1 us). Buckets from FINE_LO up (calls of 1.024 ms and more) are kept one by one for every interval, so
# the report can group them as the reader chooses; the faster calls are only counted together.
FINE_LO = 11
EVENT_SECTIONS = ("Lifecycle", "MemberAdded", "MemberRemoved", "ConnectionAdded", "ConnectionRemoved",
                  "ClusterVersionChanged", "MigrationCompleted", "MigrationFailed", "MigrationState")
MAX_EVENTS_PER_KIND = 2000          # per member and kind, in the report
NICE_STEPS = [s * 1000 for s in (1, 2, 5, 10, 15, 20, 30, 60, 120, 180, 300, 600, 900, 1200, 1800, 3600,
                                 7200, 10800, 21600, 43200, 86400)]
NAN = float("nan")


class UsageError(Exception):
    pass


def warn(msg):
    sys.stderr.write("warning: %s\n" % msg)


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def human_us(us):
    """A duration in microseconds, in the unit that reads best."""
    if us < 1000:
        return "%d us" % us
    if us < 1e6:
        return "%.3g ms" % (us / 1000.0)
    if us < 60e6:
        return "%.3g s" % (us / 1e6)
    return human_dur(us / 1e6)


def human_dur(sec):
    sec = int(round(sec))
    if sec < 60:
        return "%d s" % sec
    m, s = divmod(sec, 60)
    if m < 60:
        return "%dm %02ds" % (m, s)
    h, m = divmod(m, 60)
    if h < 24:
        return "%dh %02dm" % (h, m)
    d, h = divmod(h, 24)
    return "%dd %02dh" % (d, h)


def fmt_offset(minutes):
    return "UTC%s%02d:%02d" % ("-" if minutes < 0 else "+", abs(minutes) // 60, abs(minutes) % 60)


def fmt_time(ms):
    return time.strftime("%d-%m-%Y %H:%M:%S", time.gmtime(ms / 1000.0)) if ms is not None else ""


def fmt_uptime(ms):
    """A JVM uptime: 3d 06:06:54, or 01:03:49 in the first day."""
    sec = int(ms // 1000)
    d, sec = divmod(sec, 86400)
    return ("%dd " % d if d else "") + "%02d:%02d:%02d" % (sec // 3600, sec // 60 % 60, sec % 60)


def step_name(ms):
    if ms % 3600000 == 0:
        return "%d h" % (ms // 3600000)
    if ms % 60000 == 0:
        return "%d min" % (ms // 60000)
    return "%g s" % (ms / 1000.0)


def points_arg(text):
    if text.strip().lower() in ("max", "full", "all"):
        return None
    try:
        n = int(text)
    except ValueError:
        n = 0
    if n < 20:
        raise argparse.ArgumentTypeError("use 'max' or at least 20 points")
    return n


def nice_step(ms):
    for s in NICE_STEPS:
        if s >= ms:
            return s
    return int(math.ceil(ms / 86400000.0)) * 86400000


def to_int(text):
    text = text.strip().replace(",", "")
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text))
        except ValueError:
            return 0


def to_float(text):
    try:
        return float(text.strip().replace(",", ""))
    except ValueError:
        return None


_BUCKET_CACHE = {}


def bucket_hi(key):
    """'512..1023us' -> the bucket's upper bound in us (1023); None if not a bucket."""
    b = _BUCKET_CACHE.get(key, -1)
    if b == -1:
        m = BUCKET_RE.match(key.strip())
        b = int(int(m.group(2)) * UNIT_US[m.group(3)]) if m else None
        _BUCKET_CACHE[key] = b
    return b


def norm_host(host):
    return host.strip().strip("[]").lower().replace("_", ":")


def member_label(host, port):
    h = host.strip().strip("[]")
    if ":" in h:
        h = "[%s]" % h
    return "%s:%d" % (h, port)


def addr_label(text):
    a = ADDR_RE.search(text)
    return member_label(a.group(1), int(a.group(2))) if a else text.strip()


def parse_member_arg(text):
    s = text.strip()
    m = re.match(r"^\[(.+)\](?:[:#_](\d{1,5}))?$", s)
    if m:
        return norm_host(m.group(1)), int(m.group(2)) if m.group(2) else None
    if s.count(":") > 1:
        return norm_host(s), None
    m = re.match(r"^(.+?)[:#_](\d{1,5})$", s)
    if m:
        return norm_host(m.group(1)), int(m.group(2))
    return norm_host(s), None


def open_text(path):
    if path.lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def printed_ms(m):
    """The date-time printed on a line (a TS_RE match), as millis, taken as if it were UTC."""
    d, mo, y, hh, mi, ss = (int(x) for x in m.groups()[:6])
    return calendar.timegm((y, mo, d, hh, mi, ss, 0, 0, 0)) * 1000


def parse_when(text, ref_ms, end=False):
    """'27-10-2026 15:00[:00]', '2026-10-27 15:00', '2026-10-27T15:00', '15:00' (day of ref_ms),
    or a date alone. Returns display millis."""
    s = text.strip()
    for fmt in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return calendar.timegm(datetime.datetime.strptime(s, fmt).timetuple()) * 1000
        except ValueError:
            pass
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            ms = calendar.timegm(datetime.datetime.strptime(s, fmt).timetuple()) * 1000
            return ms + 86400000 - 1 if end else ms
        except ValueError:
            pass
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
        day = (ref_ms // 86400000) * 86400000
        return day + (t.hour * 3600 + t.minute * 60 + t.second) * 1000
    raise UsageError("cannot read the time %r; use e.g. '27-10-2026 15:00' or '2026-10-27 15:00:00'" % text)


# --------------------------------------------------------------------------- files
class LogFile(object):
    def __init__(self, path, root):
        self.path = path
        self.name = os.path.basename(path)
        rel = os.path.relpath(os.path.dirname(os.path.abspath(path)), root)
        self.reldir = "." if rel in ("", ".") else rel.replace(os.sep, "/")
        try:
            self.size = os.path.getsize(path)
        except OSError:
            self.size = 0
        self.host = self.port = self.start_ms = self.index = None
        self.first_printed = self.first_epoch = None
        self.last_printed = self.last_epoch = None
        self.looks_diag = False
        self.log_fmt = self.log_addr = None        # a member log: its format and the member address in its lines
        self.log_cluster = self.log_version = ""
        m = FILENAME_RE.search(self.name)
        if m:
            self.host, self.port = m.group("host"), int(m.group("port"))
            self.start_ms = int(m.group("start"))
            self.index = int(m.group("idx")) if m.group("idx") is not None else None
            self.looks_diag = True

    @property
    def member(self):
        return None if self.host is None else member_label(self.host, self.port)

    @property
    def first_abs(self):
        return self.first_epoch if self.first_epoch is not None else self.first_printed

    def peek(self, max_bytes=16 * 1024 * 1024):
        """First timestamp; whether the file has diagnostics sections; the address if the name has none."""
        read = 0
        want_addr = self.host is None
        try:
            with open_text(self.path) as f:
                for line in f:
                    read += len(line)
                    if line[:1] not in (" ", "\t"):
                        if self.first_printed is None:
                            m = TS_RE.match(line) or (TS_RE.search(line) if "[" in line else None)
                            if m:
                                self.first_printed = printed_ms(m)
                                self.first_epoch = int(m.group(7)) if m.group(7) else None
                        if not self.looks_diag and KNOWN_SECTION_RE.search(line) and TS_RE.search(line):
                            self.looks_diag = True
                    elif want_addr and "thisAddress=" in line:
                        a = THIS_ADDRESS_RE.search(line)
                        if a:
                            self.host, self.port = a.group(1), int(a.group(2))
                            want_addr = False
                    if self.first_printed is not None and self.looks_diag and not want_addr:
                        break
                    if read > max_bytes or (not self.looks_diag and read > 256 * 1024):
                        break
        except (OSError, EOFError) as e:
            warn("cannot read %s: %s" % (self.path, e))
        if self.looks_diag and not self.path.lower().endswith(".gz"):
            self.peek_end()

    def peek_end(self, max_bytes=512 * 1024):
        try:
            with open(self.path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - max_bytes))
                data = f.read().decode("utf-8", "replace")
        except OSError:
            return
        for line in reversed(data.splitlines()):
            if line[:1] in (" ", "\t") or "[" not in line:
                continue
            m = TS_RE.match(line) or TS_RE.search(line)
            if m:
                self.last_printed = printed_ms(m)
                self.last_epoch = int(m.group(7)) if m.group(7) else None
                return

    def sort_key(self):
        first = self.first_abs
        return (first is None, first or 0, self.index if self.index is not None else -1, self.name)


def discover(root):
    """Files under root: .log, .txt, .out, .gz or no extension, also rolled names like x.log.1 or x.log.2026-10-27."""
    out = []
    for d, dirs, names in os.walk(root):
        dirs.sort()
        for nm in sorted(names):
            low = nm.lower()
            ext = os.path.splitext(low)[1]
            if low.startswith("._") or low == ".ds_store":
                continue
            if ext in (".log", ".txt", ".out", ".gz", "") or re.search(r"\.(?:log|out)[.-]", low):
                out.append(os.path.join(d, nm))
    return out


# --------------------------------------------------------------------------- in-memory model
class MSeries(object):
    """One metric series of one member lifetime, indexed by metric dump number. A series that
    never changes is kept as (first index, count, value) only."""
    __slots__ = ("first", "count", "cval", "vals")

    def __init__(self, idx, v):
        self.first = idx
        self.count = 1
        self.cval = v
        self.vals = None

    def add(self, idx, v):
        vals = self.vals
        if vals is None:
            nxt = self.first + self.count
            if idx == nxt and v == self.cval:
                self.count += 1
                return
            if idx < nxt:
                return
            vals = self.vals = array.array("d", [self.cval]) * self.count
        nxt = self.first + len(vals)
        if idx < nxt:
            return
        if idx > nxt:
            vals.extend([NAN] * (idx - nxt))
        vals.append(v)

    def length(self):
        return self.count if self.vals is None else len(self.vals)


class ProfRow(object):
    """Every profiler interval in which one operation ran, for one lifetime: end time and length of
    the interval, calls, total time, max; and for the intervals with calls of FINE_LO or slower, the
    calls per bucket: interval index, first bucket, number of buckets, then the counts (flat)."""
    __slots__ = ("t", "dt", "n", "tot", "mx", "me", "fk", "fe", "fl", "fc")

    def __init__(self):
        self.t = array.array("d")
        self.dt = array.array("d")
        self.n = array.array("d")
        self.tot = array.array("d")
        self.mx = array.array("d")
        self.me = array.array("b")          # 1: the max is exact, 0: an upper bound
        self.fk = array.array("i")
        self.fe = array.array("b")
        self.fl = array.array("b")
        self.fc = array.array("d")

    def add(self, t, dt, dn, dtot, fine, mv, me):
        """fine: [(bucket exponent, calls)] by exponent, calls > 0."""
        self.t.append(t)
        self.dt.append(dt)
        self.n.append(dn)
        self.tot.append(dtot)
        self.mx.append(mv)
        self.me.append(1 if me else 0)
        if fine:
            e0 = fine[0][0]
            run = [0.0] * (fine[-1][0] - e0 + 1)
            for e, c in fine:
                run[e - e0] += c
            self.fk.append(len(self.t) - 1)
            self.fe.append(e0)
            self.fl.append(len(run))
            self.fc.extend(run)

    def fine_lists(self):
        """[(bucket exponent, calls)] of every interval, [] for those with no slow-enough call."""
        out = [()] * len(self.t)
        pos = 0
        for k, e0, ln in zip(self.fk, self.fe, self.fl):
            out[k] = [(e0 + i, c) for i, c in enumerate(self.fc[pos:pos + ln]) if c]
            pos += ln
        return out

    def events(self):
        return zip(self.t, self.dt, self.n, self.tot, self.fine_lists(), self.mx, self.me)


class Unit(object):
    """Everything read for one member lifetime (or one folder of files without an address)."""

    def __init__(self, key, label, files):
        self.key = key
        self.label = label
        self.files = files
        self.dirs = []
        for f in files:
            if f.reldir not in self.dirs:
                self.dirs.append(f.reldir)
        self.addr_dirs = set(self.dirs)
        self.mtimes = []            # display time of each metric dump
        self.cur_mt = None
        self.series = {}            # metric attributes text -> MSeries
        self.cluster = []           # (t, size, is_master, members, cluster_id)
        self.events = []            # (t, kind, text, who, size)
        self.hb = []                # (t, kind, peer, no_heartbeat_ms, deviation_pct)
        self.pstreams = {}          # profiler -> [last t, previous ops]
        self.prof = {}              # (profiler, op) -> ProfRow
        self.ptimes = []            # display times of profiler dumps
        self.stot = {}              # store latency section -> [last t, last ops, carried over resets, dump times]
        self.slowops = []           # (t, [[operation, invocations counted, stack lines, [[start, duration, details]]]])
        self.invd = []              # invocation sampler dumps: (t, list cut at the print limit, History, SlowHistory, in the window)
        self.invs = {}              # every invocation it listed as slow, once: key -> InvRec
        self.props = {}             # BuildInfo / SystemProperties / ConfigProperties -> {key: value}, the last seen
        self.props_t = None         # when they were first seen
        self.prop_changes = []      # (t, section, key, value before, value after) within the lifetime
        self.cover = []
        self.member = None

    @property
    def start(self):
        return self.cover[0][0] if self.cover else None


class Ctx(object):
    def __init__(self, use_epoch, offset_ms, lo, hi):
        self.use_epoch = use_epoch
        self.offset = offset_ms
        self.lo = lo
        self.hi = hi
        self.counters = {"truncated": 0, "duplicate": 0, "non_numeric": 0, "out_of_order": 0}

    def tdisp(self, m):
        ep = m.group(7)
        if self.use_epoch and ep:
            return int(ep) + self.offset
        return printed_ms(m)

    def inside(self, t):
        return (self.lo is None or t >= self.lo) and (self.hi is None or t <= self.hi)


# --------------------------------------------------------------------------- section handlers
# A handler gets the indented lines of its section. feed() returns True once the section closed;
# finish() is called instead when the next top-level line (or the end of the file) comes first.

def _split_closes(line):
    s = line.strip()
    body = s.rstrip("]")
    return body, len(s) - len(body)


class ProfilerSection(object):
    __slots__ = ("u", "prof", "t", "ctx", "ops", "depth", "op", "snap", "in_lat")

    def __init__(self, u, prof, t, ctx):
        self.u, self.prof, self.t, self.ctx = u, prof, t, ctx
        self.ops = {}
        self.depth = 1
        self.op = None
        self.snap = None
        self.in_lat = False

    def feed(self, line):
        body, closes = _split_closes(line)
        if body.endswith("["):
            self.depth += 1
            if self.depth == 2:
                self.op = body[:-1].strip()
                self.snap = [0, 0, 0, []]           # count, total, max, [(bucket upper bound in us, calls)]
            elif self.depth == 3:
                self.in_lat = body[:-1].strip() == "latency-distribution"
        elif body and self.snap is not None:
            k, eq, v = body.partition("=")
            if eq:
                if self.depth == 3:
                    if self.in_lat:
                        hi = bucket_hi(k)
                        if hi is not None:
                            self.snap[3].append((hi, to_int(v)))
                elif self.depth == 2:
                    if k == "count":
                        self.snap[0] = to_int(v)
                    elif k.startswith("totalTime"):
                        self.snap[1] = to_int(v)
                    elif k.startswith("max"):
                        self.snap[2] = to_int(v)
        while closes:
            closes -= 1
            self.depth -= 1
            if self.depth == 2:
                self.in_lat = False
            elif self.depth == 1:
                if self.snap is not None:
                    self.ops[self.op] = self.snap
                self.snap = None
            elif self.depth <= 0:
                feed_profiler(self.u, self.prof, self.t, self.ops, self.ctx)
                return True
        return False

    def finish(self):
        self.ctx.counters["truncated"] += 1     # cut short (crash, copy of a file being written): skipped


def store_op(svc, ds, method):
    """The key of a store method: section (MapStore, QueueStore...), data structure, method."""
    return svc + "\u0001" + ds + "\u0001" + method


class StoreLatencySection(object):
    """MapStoreLatency[ (and QueueStoreLatency and the like): per data structure and per method of its store or
    loader (load, loadAll, loadAllKeys, store, storeAll, delete, deleteAll) the counters of a profiler: count,
    total and max time, latency buckets; cumulative since the member started, or since the last reset when
    hazelcast.diagnostics.storeLatency.reset.period.seconds is set."""
    __slots__ = ("u", "svc", "t", "ctx", "ops", "seen", "depth", "ds", "meth", "snap", "in_lat")

    def __init__(self, u, svc, t, ctx):
        self.u, self.svc, self.t, self.ctx = u, svc, t, ctx
        self.ops = {}
        self.seen = set()                       # every data structure listed, called or not ("orders[]")
        self.depth = 1
        self.ds = self.meth = None
        self.snap = None
        self.in_lat = False

    def feed(self, line):
        body, closes = _split_closes(line)
        if body.endswith("["):
            self.depth += 1
            name = body[:-1].strip()
            if self.depth == 2:
                self.ds = name
                self.seen.add(name)
            elif self.depth == 3:
                self.meth = name
                self.snap = [0, 0, 0, []]           # count, total, max, [(bucket upper bound in us, calls)]
            elif self.depth == 4:
                self.in_lat = name == "latency-distribution"
        elif body and self.snap is not None:
            k, eq, v = body.partition("=")
            if eq:
                if self.depth == 4:
                    if self.in_lat:
                        hi = bucket_hi(k)
                        if hi is not None:
                            self.snap[3].append((hi, to_int(v)))
                elif self.depth == 3:
                    if k == "count":
                        self.snap[0] = to_int(v)
                    elif k.startswith("totalTime"):
                        self.snap[1] = to_int(v)
                    elif k.startswith("max"):
                        self.snap[2] = to_int(v)
        while closes:
            closes -= 1
            self.depth -= 1
            if self.depth == 3:
                self.in_lat = False
            elif self.depth == 2:
                if self.snap is not None and self.ds is not None:
                    self.ops[store_op(self.svc, self.ds, self.meth)] = self.snap
                self.snap = None
            elif self.depth <= 0:
                feed_store(self.u, self.svc, self.t, self.ops, self.ctx, self.seen)
                return True
        return False

    def finish(self):
        self.ctx.counters["truncated"] += 1


def store_reset(prev, ops):
    """Whether the store latency counters were reset after the dump before (storeLatency.reset.period.seconds
    resets them all at once): a count, a total, a max or a bucket of some method is lower than it was."""
    for name, s in ops.items():
        p = prev.get(name)
        if p is None:
            continue
        if s[0] < p[0] or s[1] < p[1] or s[2] < p[2]:
            return True
        sb = dict(s[3])
        for hi, c in p[3]:
            if sb.get(hi, 0) < c:
                return True
    return False


def add_snap(a, b):
    """Two store counters added: [count, total, max, {bucket exponent: calls}]."""
    out = [0, 0, 0, {}] if a is None else [a[0], a[1], a[2], dict(a[3])]
    out[0] += b[0]
    out[1] += b[1]
    out[2] = max(out[2], b[2])
    for k, c in (b[3].items() if isinstance(b[3], dict) else ((hi.bit_length(), c) for hi, c in b[3])):
        out[3][k] = out[3].get(k, 0) + c
    return out


def feed_store(u, svc, t, ops, ctx, seen=()):
    """A store latency dump: its growth over the dump before goes with the profilers (as profiler "S"); its counters
    are kept for the totals (what was counted before a reset is carried over)."""
    feed_profiler(u, "S", t, ops, ctx, stream="S:" + svc)
    st = u.stot.get(svc)
    if st is None:
        st = u.stot[svc] = [None, {}, {}, [], set()]
    if (st[0] is not None and t <= st[0]) or not ctx.inside(t):
        return
    st[4].update(seen)
    prev = st[1]
    reset = store_reset(prev, ops)
    for name, p in prev.items():
        if reset or name not in ops:        # reset after the dump before (or gone): keep what it had counted
            st[2][name] = add_snap(st[2].get(name), p)
    st[0], st[1] = t, ops
    st[3].append(t)


class SlowOpsSection(object):
    """SlowOperations[ (the slow operation detector, every 60 s by default): for each stack trace an operation was
    seen in, how many slow invocations the detector counted, the stack trace and the invocations it still keeps
    (start, duration so far, operation details). Read by indentation, as the details can hold brackets; the
    section ends at the next top-level line."""
    __slots__ = ("u", "t", "ctx", "base", "logs", "cur", "mode", "inv")

    def __init__(self, u, t, ctx):
        self.u, self.t, self.ctx = u, t, ctx
        self.base = None
        self.logs = []
        self.cur = None
        self.mode = None
        self.inv = None

    def feed(self, line):
        s = line.rstrip("\r\n")
        body = s.lstrip(" ")
        text = body.lstrip("\t").rstrip()
        if not text:
            return False
        ind = len(s) - len(body)
        if self.base is None:
            self.base = ind
        lvl = (ind - self.base + 4) // 8            # 0: an operation, 1: its entries, 2: stack lines / invocations
        cur = self.cur
        if lvl <= 0:
            name = text.rstrip("]")
            self.cur = [name[:-1].strip() if name.endswith("[") else name.strip(), 0, [], []]
            self.logs.append(self.cur)
            self.mode = self.inv = None
        elif cur is None:
            pass
        elif lvl == 1:
            v = text.rstrip("]")
            if v.startswith("invocations="):
                cur[1] = to_int(v[12:])
            elif v.startswith("stackTrace["):
                self.mode = "s"
            elif v.startswith("slowInvocations["):
                self.mode = "i"
        elif self.mode == "s":
            if len(cur[2]) < 200:
                cur[2].append(text.rstrip("]"))     # a stack frame never ends with ]
        elif self.mode == "i":
            k, eq, v = text.partition("=")
            if eq:
                if k == "startedAt":
                    self.inv = [to_int(v.rstrip("]")), None, "", None]
                    cur[3].append(self.inv)
                elif self.inv is not None:
                    if k == "duration(ms)":
                        self.inv[1] = to_int(v.rstrip("]"))
                    elif k == "operationDetails":
                        self.inv[2] = v.rstrip("]")[:600]
                    elif k == "started(date-time)":
                        self.inv[3] = v.rstrip("]").strip()
        return False

    def finish(self):
        ctx = self.ctx
        for lg in self.logs:
            invs = []
            for ep, dur, det, printed in lg[3]:
                if dur is None or not ep:
                    continue
                if ctx.use_epoch:
                    start = ep + ctx.offset
                else:                                   # no epoch on the lines: the printed start, to the second
                    m = TS_RE.match((printed or "") + " x")
                    if m is None:
                        continue
                    start = printed_ms(m) + ep % 1000
                invs.append((start, dur, det))
            lg[3] = invs
        self.u.slowops.append((self.t, self.logs))


# the invocation sampler (hazelcast.diagnostics.invocation.sample.period.seconds): Invocations[ Pending[ Invocation{op=
# com.hazelcast.map.impl.operation.GetOperation{serviceName='hz:impl:mapService', identityHash=1785082044, partitionId=125,
# ..., callId=188105516, ..., name=orders}, tryCount=250, ..., invokeCount=1, callTimeoutMillis=60000, firstInvocationTimeMs=
# 1793583006766, ..., lastHeartbeatMillis=..., targetAddress=[10.0.0.1]:5701, targetMember=Member [10.0.0.1]:5701 - <uuid>
# this, ..., pendingResponse={VOID}, backupsAcksExpected=-1, backupsAcksReceived=0, connection=null} duration=63116 ms ...]
# History[ <operation> samples=N ...] SlowHistory[ <operation> samples=N ...]]
INV_OP_RE = re.compile(r"^Invocation\{op=([\w.$]+)")
INV_NUM_RE = re.compile(r"[{ ,](identityHash|partitionId|replicaIndex|callId|invokeCount|callTimeoutMillis|firstInvocationTimeMs|"
                        r"lastHeartbeatMillis|backupsAcksExpected|backupsAcksReceived)=(-?\d+)")
INV_NAME_RE = re.compile(r", name=([^,}]*)")
INV_TARGET_RE = re.compile(r", target(?:Address)?=\[([^\]]*)\]:(\d+)")
INV_LOCAL_RE = re.compile(r", targetMember=Member \[[^\]]*\]:\d+ - [\w-]+ this[,}]")
INV_RESP_RE = re.compile(r", pendingResponse=\{(.*?)\}, backupsAcks")
INV_TAIL_RE = re.compile(r", connection=(.*)\} duration=(\d+) ms$")
INV_FACTORY_RE = re.compile(r", operationFactory=([\w.$]+)(?:\{opFactory=([\w.$]+))?")
INV_BACKUP_RE = re.compile(r", backupOp=([\w.$]+)")
MAX_INV_RAW = 5000                      # per lifetime: the invocations whose whole text is kept (for the details)


def inv_desc(cls, text):
    """The operation of an invocation as the sampler's History names it: the class, but for a partition iterating
    operation its factory, and for a backup the backed up operation."""
    if cls.endswith(".PartitionIteratingOperation"):
        f = INV_FACTORY_RE.search(text)
        if f:
            return "PartitionIteratingOperation(" + (f.group(2) if f.group(1).endswith("OperationFactoryWrapper") and f.group(2) else f.group(1)) + ")"
    elif cls.endswith(".Backup"):
        b = INV_BACKUP_RE.search(text)
        if b:
            return "Backup(" + b.group(1) + ")"
    return cls


class InvRec(object):
    """An invocation the sampler listed as slow, over the dumps it was listed in."""
    __slots__ = ("desc", "name", "part", "target", "local", "start", "dur", "first", "last", "n", "timeout", "invokes",
                 "bexp", "brecv", "resp", "conn", "hb", "raw")


class InvocationsSection(object):
    """Invocations[ (the invocation sampler, every hazelcast.diagnostics.invocation.sample.period.seconds): Pending[ the
    invocations pending for longer than hazelcast.diagnostics.invocation.slow.threshold.seconds (5 s), at most
    hazelcast.diagnostics.invocation.slow.max.count - 1 of them], History[ per operation, the invocations pending at the
    samples so far], SlowHistory[ the same for the slow ones]]. Read by indentation; ends at the next top-level line."""
    __slots__ = ("u", "t", "ctx", "inside", "base", "mode", "trunc", "hist", "slow", "pend")

    def __init__(self, u, t, ctx, inside):
        self.u, self.t, self.ctx, self.inside = u, t, ctx, inside
        self.base = None
        self.mode = None
        self.trunc = False
        self.hist, self.slow = {}, {}
        self.pend = []

    def feed(self, line):
        s = line.rstrip("\r\n")
        body = s.lstrip(" \t")
        text = body.rstrip()
        if not text:
            return False
        ind = len(s) - len(body)
        if self.base is None:
            self.base = ind
        if ind < self.base + 4:                 # Pending[ / History[ / SlowHistory[ (an empty one ends on the same line)
            head = text.split("[", 1)[0]
            self.mode = head if head in ("Pending", "History", "SlowHistory") else None
            return False
        v = text.rstrip("]").rstrip()
        if self.mode == "Pending":
            if v.startswith("Invocation{"):
                if self.inside:
                    self.pend.append(v)
            elif v.startswith("max number of invocations"):
                self.trunc = True
        elif self.mode is not None:
            k = v.rfind(" samples=")
            n = to_int(v[k + 9:]) if k > 0 else None
            if n is not None:
                (self.hist if self.mode == "History" else self.slow)[v[:k]] = n
        return False

    def finish(self):
        u, t = self.u, self.t
        if u.invd and t <= u.invd[-1][0]:
            self.ctx.counters["duplicate"] += 1
            return                              # the same dump read twice (a copied file)
        u.invd.append((t, self.trunc, self.hist, self.slow, self.inside))
        for text in self.pend:
            inv_listed(u, t, text)


def inv_listed(u, t, text):
    """One invocation of a Pending list: a new record, or the one it was seen as at an earlier dump (same operation
    object and first invocation time; its call id changes when it is retried)."""
    m = INV_OP_RE.match(text)
    tail = INV_TAIL_RE.search(text)
    if not m or not tail:
        return
    f = {}
    for k, v in INV_NUM_RE.findall(text):
        f.setdefault(k, int(v))
    dur = int(tail.group(2))
    desc = inv_desc(m.group(1), text)
    first = f.get("firstInvocationTimeMs")
    key = (desc, f.get("identityHash", f.get("callId")), first if first is not None else (t - dur) // 1000)
    r = u.invs.get(key)
    if r is None:
        r = u.invs[key] = InvRec()
        nm = INV_NAME_RE.search(text)
        tg = INV_TARGET_RE.search(text)
        r.desc, r.name, r.part = desc, (nm.group(1).strip() if nm else ""), f.get("partitionId", -1)
        r.target = member_label(tg.group(1), int(tg.group(2))) if tg else ""
        r.local = INV_LOCAL_RE.search(text) is not None
        r.start, r.dur, r.first, r.n, r.raw = t - dur, dur, t, 0, None
    r.dur = max(r.dur, dur)
    r.last = t
    r.n += 1
    r.timeout, r.invokes = f.get("callTimeoutMillis"), f.get("invokeCount")
    r.bexp, r.brecv = f.get("backupsAcksExpected"), f.get("backupsAcksReceived")
    rp = INV_RESP_RE.search(text)
    r.resp = rp.group(1)[:80] if rp else ""
    r.conn = tail.group(1)[:200]
    hb = f.get("lastHeartbeatMillis")
    r.hb = (first + dur - hb) if hb and first else None     # how long since the target's last heartbeat for it
    if r.raw is not None or len(u.invs) <= MAX_INV_RAW:
        r.raw = text[:1500]


PROP_SECTIONS = ("BuildInfo", "SystemProperties", "ConfigProperties")
MAX_PROP_VALUE = 20000                  # characters of a value kept (java.class.path can be long)


class PropsSection(object):
    """BuildInfo[ / SystemProperties[ / ConfigProperties[ key=value ...] (written at the top of each diagnostics
    file): the last ones seen per lifetime, and what changed within it."""
    __slots__ = ("u", "t", "name", "lines")

    def __init__(self, u, t, name):
        self.u, self.t, self.name = u, t, name
        self.lines = []

    def feed(self, line):
        s = line.strip("\r\n")
        if s.strip():
            self.lines.append(s.strip())
        return False

    def finish(self):
        if not self.lines:
            return
        if self.lines[-1].endswith("]"):
            self.lines[-1] = self.lines[-1][:-1]           # the section's own bracket
        d = {}
        for x in self.lines:
            k, eq, v = x.partition("=")
            if eq and k.strip():
                d[k.strip()] = v[:MAX_PROP_VALUE]
        u = self.u
        old = u.props.get(self.name)
        if old is not None and old != d:
            for k in sorted(set(old) | set(d)):
                if old.get(k) != d.get(k) and len(u.prop_changes) < 500:
                    u.prop_changes.append((self.t, self.name, k, old.get(k), d.get(k)))
        u.props[self.name] = d
        if u.props_t is None:
            u.props_t = self.t


class InstanceSection(object):
    """HazelcastInstance[ thisAddress=, clusterId=, clusterSize=, isMaster=, Members[ ... ]]"""
    __slots__ = ("u", "t", "ctx", "depth", "info", "members")

    def __init__(self, u, t, ctx):
        self.u, self.t, self.ctx = u, t, ctx
        self.depth = 1
        self.info = {}
        self.members = []

    def feed(self, line):
        body, closes = _split_closes(line)
        if body.endswith("["):
            self.depth += 1
        elif body:
            if self.depth == 2:                 # an entry of Members[ (the last one ends with ]])
                a = ADDR_RE.match(body)
                if a:
                    self.members.append(member_label(a.group(1), int(a.group(2))))
            else:
                k, eq, v = body.partition("=")
                if eq:
                    self.info[k.strip()] = v.strip()
        self.depth -= closes
        if self.depth <= 0:
            i = self.info
            size = to_int(i["clusterSize"]) if "clusterSize" in i else (len(self.members) or None)
            self.u.cluster.append((self.t, size, i.get("isMaster") == "true", tuple(self.members),
                                   i.get("clusterId", "")))
            return True
        return False

    def finish(self):
        self.ctx.counters["truncated"] += 1


class HeartbeatSection(object):
    """OperationHeartbeat[ / MemberHeartbeats[ member[10.0.0.2]:5701[ deviation(%)= noHeartbeat(ms)= ...]]
    (written only when some heartbeat is late)."""
    __slots__ = ("u", "t", "ctx", "kind", "depth", "peer", "vals")

    def __init__(self, u, t, ctx, kind):
        self.u, self.t, self.ctx, self.kind = u, t, ctx, kind
        self.depth = 1
        self.peer = None
        self.vals = {}

    def feed(self, line):
        body, closes = _split_closes(line)
        if body.endswith("["):
            self.depth += 1
            if self.depth == 2:
                self.peer = addr_label(body[:-1])
                self.vals = {}
        elif body and self.depth == 2:
            k, eq, v = body.partition("=")
            if eq:
                self.vals[k.strip()] = v
        while closes:
            closes -= 1
            self.depth -= 1
            if self.depth == 1 and self.peer is not None:
                nohb = to_float(self.vals.get("noHeartbeat(ms)", ""))
                dev = to_float(self.vals.get("deviation(%)", ""))
                self.u.hb.append((self.t, self.kind, self.peer, nohb, dev))
                self.peer = None
            elif self.depth <= 0:
                return True
        return False

    def finish(self):
        pass                                    # the peers read so far are kept


class EventSection(object):
    """System log events. Entries can end with ']' themselves (a connection's toString, exception
    messages), so the depth comes from the indentation and the section ends at the next top-level line."""
    __slots__ = ("u", "t", "kind", "base", "pend", "entries", "kv", "members")

    def __init__(self, u, t, kind):
        self.u, self.t, self.kind = u, t, kind
        self.base = None
        self.pend = None
        self.entries = []
        self.kv = {}
        self.members = []

    def feed(self, line):
        s = line.rstrip()
        body = s.lstrip()
        ind = len(s) - len(body)
        if self.base is None:
            self.base = ind
        depth = 1 + max(0, ind - self.base) // 8
        if self.pend is not None:
            self.take(self.pend[0], self.pend[1], max(0, self.pend[0] - depth))
        self.pend = (depth, body)
        return False

    def take(self, depth, body, closes):
        if closes and body.endswith("]" * closes):
            body = body[:-closes]
        if body.endswith("["):
            return
        if depth == 1:
            k, eq, v = body.partition("=")
            if eq and k and " " not in k and "[" not in k:
                self.kv[k] = v.strip()
            else:
                self.entries.append(body)
        elif depth == 2 and self.kind in ("MemberAdded", "MemberRemoved"):
            a = ADDR_RE.match(body)              # [10.0.0.1]:5701:this:master
            if a:
                self.members.append(member_label(a.group(1), int(a.group(2))))

    def finish(self):
        if self.pend is not None:
            self.take(self.pend[0], self.pend[1], self.pend[0])
            self.pend = None
        who, size = self.summary_parts()
        self.u.events.append((self.t, self.kind, self.summary(), who, size))

    def summary_parts(self):
        if self.kind in ("MemberAdded", "MemberRemoved"):
            return addr_label(self.kv.get("member", "?")), len(self.members)
        return "", None

    def summary(self):
        k, kv = self.kind, self.kv
        if k in ("MemberAdded", "MemberRemoved"):
            n = len(self.members)
            return "%s %s; %d member%s after" % (addr_label(kv.get("member", "?")),
                                                  "joined" if k == "MemberAdded" else "left",
                                                  n, "" if n == 1 else "s")
        if k == "Lifecycle":
            return self.entries[0] if self.entries else ""
        if k in ("ConnectionAdded", "ConnectionRemoved"):
            text = self.entries[0] if self.entries else ""
            extra = [kv[x] for x in ("type", "closeReason") if kv.get(x)]
            return (text[:220] + ("  ·  " + " · ".join(extra) if extra else "")).strip()
        if k in ("MigrationCompleted", "MigrationFailed"):
            return "partition %s replica %s: %s → %s" % (kv.get("partitionId", "?"), kv.get("replicaIndex", "?"),
                                                           addr_label(kv.get("source", "?")),
                                                           addr_label(kv.get("destination", "?")))
        if k == "MigrationState":
            return "planned %s, completed %s, remaining %s" % (kv.get("plannedMigrations", "?"),
                                                               kv.get("completedMigrations", "?"),
                                                               kv.get("remainingMigrations", "?"))
        return (self.entries[0] if self.entries else "")[:220]


ZERO_SNAP = (0, 0, 0, ())


def bucket_growth(cb, pb):
    """Between two dumps of one operation, from their [(upper bound, calls)] buckets: the calls per bucket
    from FINE_LO up, [(exponent, calls)] by exponent, and the upper bound (us) of the highest bucket that
    grew (None if none did). None when a bucket went down (a restart)."""
    fine, top = [], -1
    if len(cb) == len(pb):
        for (hi, c), (h2, q) in zip(cb, pb):
            if hi != h2:
                break                       # the bucket layout differs: compare by bucket below
            if c != q:
                if c < q:
                    return None
                if hi > top:
                    top = hi
                e = hi.bit_length()
                if e >= FINE_LO:
                    fine.append((e, c - q))
        else:
            fine.sort()
            return fine, (top if top >= 0 else None)
        fine, top = [], -1
    prev = dict(pb)
    for hi, c in cb:
        q = prev.pop(hi, 0)
        if c != q:
            if c < q:
                return None
            if hi > top:
                top = hi
            e = hi.bit_length()
            if e >= FINE_LO:
                fine.append((e, c - q))
    if any(prev.values()):
        return None                         # a bucket that had calls is gone
    fine.sort()
    return fine, (top if top >= 0 else None)


def feed_profiler(u, prof, t, ops, ctx, stream=None):
    """Diff a profiler dump with the previous dump of the same lifetime; bin the deltas.
    The counters are cumulative since the member started, so the first dump is only a baseline. The store latency
    counters ("S") can be reset after a dump: counters lower than at the dump before are all new."""
    key = stream or prof
    st = u.pstreams.get(key)
    if st is None:
        st = u.pstreams[key] = [None, None]
    if st[0] is not None and t <= st[0]:
        ctx.counters["duplicate"] += 1
        return
    u.ptimes.append(t)
    if st[0] is None:
        st[0], st[1] = t, ops
        return
    prev, prev_t = st[1], st[0]
    st[0], st[1] = t, ops
    if not ctx.inside(t):
        return
    dt = t - prev_t
    if prof == "S" and store_reset(prev, ops):
        prev = {}                           # reset after the dump before: everything counted is new
    for name, s in ops.items():
        p = prev.get(name, ZERO_SNAP)
        dn = s[0] - p[0]
        if dn <= 0 or s[1] < p[1]:
            continue                        # nothing ran, or the counters went down (a restart)
        grown = bucket_growth(s[3], p[3])
        if grown is None:
            continue
        fine, hi = grown
        if s[2] > p[2]:
            mv, me = s[2], True             # the logged max is all-time: it went up, so it is this interval's
        else:                               # otherwise: at most the top of the highest bucket that grew
            mv, me = (s[2] if hi is None else (min(hi, s[2]) if s[2] else hi)), False
        row = u.prof.get((prof, name))
        if row is None:
            row = u.prof[(prof, name)] = ProfRow()
        row.add(t, dt, dn, s[1] - p[1], fine, mv, me)


def parse_file(lf, u, ctx):
    sec = None
    cnt = ctx.counters
    with open_text(lf.path) as f:
        for line in f:
            c0 = line[:1]
            if c0 == " " or c0 == "\t":
                if sec is not None and sec.feed(line):
                    sec = None
                continue
            if sec is not None:
                if not line.strip():
                    continue
                sec.finish()
                sec = None
            m = TS_RE.match(line)
            if m is None:
                if "[" not in line:
                    continue
                m = TS_RE.search(line)
                if m is None:
                    continue
            body = m.group(8)
            if body.startswith("Metric[["):
                # Metric[[name=orders,unit=count,metric=map.getCount]=123]  -- the hot path
                j = body.find("]=", 8)
                if j < 0:
                    continue
                t = ctx.tdisp(m)
                if t != u.cur_mt:
                    if u.cur_mt is not None and t < u.cur_mt:
                        cnt["out_of_order"] += 1        # a copy of older data: already read
                        continue
                    if not ctx.inside(t):
                        continue
                    u.cur_mt = t
                    u.mtimes.append(t)
                vs = body[j + 2:].rstrip()
                if vs.endswith("]"):
                    vs = vs[:-1]
                try:
                    v = float(vs)
                except ValueError:
                    cnt["non_numeric"] += 1             # "NA" or an exception text
                    continue
                attrs = body[8:j]
                s = u.series.get(attrs)
                if s is None:
                    u.series[attrs] = MSeries(len(u.mtimes) - 1, v)
                else:
                    s.add(len(u.mtimes) - 1, v)
                continue
            k = body.find("[")
            if k <= 0:
                continue
            name = body[:k]
            if name in PROFILERS:
                handler = 1
            elif name == "HazelcastInstance":
                handler = 2
            elif name == "OperationHeartbeat" or name == "MemberHeartbeats":
                handler = 3
            elif name in EVENT_SECTIONS:
                handler = 4
            elif name == "SlowOperations":
                handler = 5
            elif STORE_SECTION_RE.match(name):
                handler = 6
            elif name == "Invocations":
                handler = 7
            elif name in PROP_SECTIONS:
                handler = 8
            else:
                continue
            t = ctx.tdisp(m)
            rest = body[k + 1:].strip()
            if handler == 1:
                if rest.rstrip("]"):
                    continue
                if rest:                                # "OperationsProfiler[]": nothing ran yet
                    feed_profiler(u, PROFILERS[name], t, {}, ctx)
                    continue
                sec = ProfilerSection(u, PROFILERS[name], t, ctx)
                continue
            if handler == 6:
                svc = name[:-len("Latency")]
                if rest.rstrip("]"):
                    continue
                if rest:                                # "MapStoreLatency[]": no store called yet
                    feed_store(u, svc, t, {}, ctx)
                    continue
                sec = StoreLatencySection(u, svc, t, ctx)
                continue
            if handler == 8:                            # the build and the properties: whatever the time window
                sec = PropsSection(u, t, name)
                if rest:                                # "BuildInfo[]", or an entry on the same line
                    if rest.rstrip("]"):
                        sec.feed(rest)
                    if rest.endswith("]"):
                        sec.finish()
                        sec = None
                continue
            if handler == 7:                            # read outside --from/--to too: the dump before is the baseline
                sec = InvocationsSection(u, t, ctx, ctx.inside(t))
                if rest.rstrip("]") == "" and rest:
                    sec.finish()
                    sec = None
                continue
            if not ctx.inside(t):
                continue
            if handler == 2:
                sec = InstanceSection(u, t, ctx)
            elif handler == 3:
                sec = HeartbeatSection(u, t, ctx, "O" if name == "OperationHeartbeat" else "M")
            elif handler == 5:
                sec = SlowOpsSection(u, t, ctx)
            else:
                sec = EventSection(u, t, name)
            if rest:
                if rest.rstrip("]") == "":
                    if handler >= 4:
                        sec.finish()
                    sec = None
                elif sec.feed(" " * 26 + rest):
                    sec = None
    if sec is not None:
        sec.finish()


# --------------------------------------------------------------------------- members
def coverage(times, gap_floor=60000):
    """Spans of time with dumps (a gap longer than 3 dump periods, and at least a minute, splits)."""
    ts = sorted(times)
    if not ts:
        return []
    diffs = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    limit = max(gap_floor, 3 * (statistics.median(diffs) if diffs else 5000))
    spans = []
    cur = [ts[0], ts[0]]
    for t in ts[1:]:
        if t - cur[1] > limit:
            spans.append(cur)
            cur = [t, t]
        else:
            cur[1] = t
    spans.append(cur)
    return spans


def overlaps(sa, sb):
    for a0, a1 in sa:
        for b0, b1 in sb:
            if a0 <= b1 and b0 <= a1:
                return True
    return False


class Member(object):
    def __init__(self, key, label, units):
        self.key = key
        self.label = label
        self.units = units
        self.dirs = []
        for u in units:
            for d in u.dirs:
                if d not in self.dirs:
                    self.dirs.append(d)

    @property
    def files(self):
        return [f for u in self.units for f in u.files]

    @property
    def cover(self):
        spans = sorted(c for u in self.units for c in u.cover)
        out = []
        for a, b in spans:
            if out and a <= out[-1][1]:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        return out


def build_units(files, wanted):
    """One unit per member lifetime (JVM run): the address plus the start time in the file names,
    wherever the files sit. A renamed file (no start time) joins the lifetime that was running when
    it begins. Files without an address are grouped by directory."""
    by_addr = {}
    addr_order = []
    no_addr = {}
    for lf in files:
        if lf.host is None:
            no_addr.setdefault(lf.reldir, []).append(lf)
            continue
        key = (norm_host(lf.host), lf.port)
        if key not in by_addr:
            by_addr[key] = {}
            addr_order.append(key)
        by_addr[key].setdefault(lf.start_ms, []).append(lf)
    units = []
    addr_dirs = dict((k, set(f.reldir for fs in v.values() for f in fs)) for k, v in by_addr.items())
    for key in addr_order:
        lts = by_addr[key]
        orphans = lts.pop(None, [])
        for lf in orphans:
            t = lf.first_abs
            cands = [s for s in lts if s is not None and t is not None and s <= t + 60000]
            same_dir = [s for s in cands if any(f.reldir == lf.reldir for f in lts[s])]
            pick = max(same_dir or cands) if cands else None
            lts.setdefault(pick, []).append(lf)
        for start in sorted(lts, key=lambda s: (s is None, s or 0)):
            fs = dedupe(sorted(lts[start], key=lambda f: f.sort_key()))
            u = Unit(("addr",) + key, fs[0].member, fs)
            u.addr_dirs = addr_dirs[key]
            units.append(u)
    for d in sorted(no_addr):
        fs = dedupe(sorted(no_addr[d], key=lambda f: f.sort_key()))
        units.append(Unit(("dir", d), "files in %s" % d, fs))
    if wanted:
        keep = []
        for u in units:
            for w in wanted:
                host, port = parse_member_arg(w)
                ws = w.strip().strip("/")
                if u.key[0] == "addr" and u.key[1] == host and (port is None or u.key[2] == port):
                    keep.append(u)
                    break
                if u.key[0] == "dir" and ws and ws in u.key[1]:
                    keep.append(u)
                    break
                if any(ws == d.split("/")[-1] for d in u.dirs):
                    keep.append(u)
                    break
        if not keep:
            labels = sorted(set(u.label for u in units))
            raise UsageError("no files for %s. Members found: %s" % (", ".join(wanted), ", ".join(labels) or "none"))
        units = keep
    return units


def dedupe(files):
    """The same file copied twice (same name, size and first line) is read once."""
    seen = set()
    out = []
    for f in files:
        k = (f.name, f.size, f.first_abs)
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out


def finish_members(units):
    """After parsing: the lifetimes of one address make one member, unless their times overlap
    (other machines reusing the address): those are labelled by directory."""
    for u in units:
        u.cover = coverage(u.mtimes + u.ptimes + [c[0] for c in u.cluster] + [d[0] for d in u.invd if d[4]])
    by_key = {}
    order = []
    for u in units:
        if u.key not in by_key:
            by_key[u.key] = []
            order.append(u.key)
        by_key[u.key].append(u)
    members = []
    for key in order:
        us = sorted(by_key[key], key=lambda u: u.start or 0)
        if key[0] != "addr":
            members.extend(Member(u.key, u.label, [u]) for u in us)
            continue
        clusters = []
        for u in us:
            free = [c for c in clusters if not any(overlaps(x.cover, u.cover) for x in c)]
            same_dir = [c for c in free if set(d for x in c for d in x.dirs) & set(u.dirs)]
            target = (same_dir or free or [None])[0]
            if target is None:
                clusters.append([u])
            else:
                target.append(u)
        for c in clusters:
            m = Member(key, c[0].label, c)
            if len(clusters) > 1 or set(m.dirs) != c[0].addr_dirs:
                m.label = "%s · %s" % (m.label, "+".join(m.dirs) or ".")
            members.append(m)
    members.sort(key=lambda m: m.label)
    for i, m in enumerate(members):
        for u in m.units:
            u.member = i
    return members


# --------------------------------------------------------------------------- metrics: shapes
# Counters vs gauges: the logs don't say, so by the name's words (camelCase) first, then by the data
# (a counter never goes down within a member lifetime).
GAUGE_WORDS = frozenset("size pending running used free max min committed current percent percentage load cost "
                        "capacity ratio avg average idle uptime active state health enabled parked processors "
                        "available remaining peak queued duration std dev deviation term missing value version round".split())
LIVE_WORDS = frozenset("connection live active endpoint client member node session".split())
TIMESTAMP_TAIL = frozenset("start end nanos millis time ms".split())
MIN_AGG_WORDS = frozenset("available remaining".split())
GAUGE_TAIL = frozenset("entry thread listener partition client member node descriptor classes groups backup "
                       "connection socket endpoint index item lock".split())
MAX_AGG_WORDS = frozenset("percent percentage max min avg average ratio load latency idle age time state id "
                          "health term delay millis nanos ms duration".split())
CAMEL_RE = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def name_words(name):
    return [w.lower() for w in CAMEL_RE.findall(name.rsplit(".", 1)[-1])]


def gauge_by_name(name):
    w = name_words(name)
    if not w:
        return False
    if w == ["count"]:              # tcp.connection.count is a gauge, raft.group.snapshotBuild.count a counter
        return bool(LIVE_WORDS.intersection(name_words(name.rsplit(".", 1)[0]))) if "." in name else True
    if GAUGE_WORDS.intersection(w):
        return True
    if w[0] == "last" and w[-1] not in ("id", "index", "applied"):    # lastCheckedPartitionCount, ...
        return True
    return len(w) >= 2 and w[-1] in ("count", "size") and w[-2] in GAUGE_TAIL and w[0] != "total"


def timestamp_by_name(name):
    """wan.sync.syncStartNanos, wan.ackDelayLastEnd...: points in time, not amounts."""
    w = name_words(name)
    return len(w) > 1 and ("start" in w or "end" in w) and w[-1] in TIMESTAMP_TAIL


def unit_by_name(name):
    """For a metric logged without unit=...: tcp.bytesSend.WAN and the like are bytes."""
    for seg in name.split("."):
        if "bytes" in (w.lower() for w in CAMEL_RE.findall(seg)):
            return "bytes"
    return None


TAG_SKIP = ("unit", "metric")


def parse_attrs(text):
    """'name=orders,unit=count,metric=map.getCount' -> (metric, unit, [(tag, value), ...])"""
    parts = []
    for p in text.split(","):
        k, eq, v = p.partition("=")
        if eq and k and " " not in k:
            parts.append([k, v])
        elif parts:
            parts[-1][1] += "," + p              # a comma inside a value
    metric = unit = ""
    tags = []
    for k, v in parts:
        if k == "metric":
            metric = v
        elif k == "unit":
            unit = v
        else:
            tags.append((k, v))
    return metric, unit, tags


WAN_NAME_RE = re.compile(r"(^|\.)wan(\.|$)", re.IGNORECASE)


THREAD_PREFIXES = ("tcp.inputThread.", "tcp.outputThread.", "operation.thread.", "threads.")


# data structures (the Data structures tab): maps, multimaps, replicated maps, caches, queues, topics, lists, sets,
# executors (IExecutorService, scheduled, durable; not the internal pools, executor.internal.*), flake ID generators...
DS_PREFIXES = ("map.", "multiMap.", "replicatedMap.", "cache.", "queue.", "topic.", "reliableTopic.", "list.", "set.",
               "scheduledExecutor.", "durableExecutor.", "flakeIdGenerator.", "pnCounter.", "cardinalityEstimator.",
               "ringbuffer.", "vectorCollection.")


def is_ds(name):
    if name.startswith(DS_PREFIXES):
        return True
    return name.startswith("executor.") and name.count(".") == 1        # executor.completed, not executor.internal.*


def category(name):
    if is_ds(name):
        return "maps"
    if WAN_NAME_RE.search(name):                # wan.*, tcp.bytesSend.WAN, ...
        return "wan"
    if name.startswith(("raft.", "cp.")):
        return "cp"
    if name.startswith(THREAD_PREFIXES):        # per IO thread and per operation thread
        return "threads"
    if name.startswith(("tcp.", "nio.", "networking.")):
        return "network"
    if name.startswith(("operation.", "executor.", "event.", "invocation", "operations.")):
        return "operations"
    if name.startswith(("os.", "runtime.", "memory.", "gc.", "thread.", "classloading.", "file.", "process.",
                        "jvm.")):
        return "system"
    if name.startswith(("partitions.", "cluster.", "client.", "migration.", "split-brain", "membership", "hd.",
                        "persistence")):
        return "cluster"
    return "other"


class MetricInfo(object):
    __slots__ = ("name", "unit", "tagkeys", "series", "kind", "agg", "across", "cat")

    def __init__(self, name, unit):
        self.name = name
        self.unit = unit or unit_by_name(name)
        self.tagkeys = None
        self.series = []        # (unit, entity label, map key, MSeries)
        self.kind = "g"
        self.agg = "sum"            # over the items of one member: sum, max or min
        self.across = "sum"         # over members (By name): CP members all report the same Raft groups -> max
        self.cat = category(name)


def series_stats(ms):
    """(constant, lo, hi, monotonic) over the valid values of a series."""
    if ms.vals is None:
        return True, ms.cval, ms.cval, True
    valid = [x for x in ms.vals if x == x]
    if not valid:
        return True, None, None, True
    lo, hi = min(valid), max(valid)
    if lo == hi:
        return True, lo, hi, True
    mono = all(map(operator.le, valid, valid[1:]))
    return False, lo, hi, mono


def cp_tags(tags):
    """CP objects and sessions are tagged id=name@group, name=, group= (sessions: qualifiedSessionId=, sessionId=):
    keep the id, and the tags that say something more (the session that owns a lock)."""
    oid = next(v for k, v in tags if k == "id")
    parts = set(oid.split("@"))
    has_q = any(k == "qualifiedSessionId" for k, _v in tags)
    out = []
    for k, v in tags:
        if k in ("name", "group") and v in parts:
            continue
        if k == "qualifiedSessionId" and v == oid:
            continue
        if k == "sessionId" and (v in parts or has_q):
            continue
        out.append((k, v))
    return out


def collect_metrics(units):
    metrics = {}
    cache = {}
    for u in units:
        for attrs, ms in u.series.items():
            p = cache.get(attrs)
            if p is None:
                name, unit, tags = parse_attrs(attrs)
                if name.startswith("cp.") and any(k == "id" for k, v in tags):
                    tags = cp_tags(tags)
                label = " · ".join(v for k, v in tags) if tags else ""
                mapkey = next((v for k, v in tags if k == "name"), label)
                p = cache[attrs] = (name, unit, tuple(k for k, v in tags), label, mapkey)
            name, unit, keys, label, mapkey = p
            if not name:
                continue
            mi = metrics.get(name)
            if mi is None:
                mi = metrics[name] = MetricInfo(name, unit)
            if keys and (mi.tagkeys is None or len(keys) > len(mi.tagkeys)):
                mi.tagkeys = keys
            mi.series.append((u, label, mapkey, ms))
    for mi in metrics.values():
        if mi.tagkeys is None:
            mi.tagkeys = ()
    return metrics


def classify(mi, stats):
    """Counter ('c', shown as a rate), gauge ('g') or timestamp ('t', not charted)."""
    his = [s[2] for s in stats if s[2] is not None]
    if (mi.unit == "ms" and his and min(his) > 1e12 and max(his) < 1e14) or timestamp_by_name(mi.name):
        return "t"
    if mi.unit == "percent" or gauge_by_name(mi.name):
        return "g"
    moving = [s for s in stats if not s[0]]
    if moving and all(s[3] for s in moving):
        return "c"
    return "g"


# --------------------------------------------------------------------------- metrics: time grid
class MGrid(object):
    def __init__(self, t0, step, n):
        self.t0, self.step, self.n = t0, step, n


def unit_bins(u, t0, step):
    return [int((t - t0) // step) for t in u.mtimes]


def period_of(u):
    mt = u.mtimes
    if len(mt) < 2:
        return 60000.0
    d = [b - a for a, b in zip(mt, mt[1:]) if b > a]
    return float(statistics.median(d)) if d else 60000.0


def gauge_bins(vals, first, ub, mt, hold, nb):
    """Per-bin max of a gauge, holding the last value across bins without a dump (no gap)."""
    out = [None] * nb
    pb = pv = pt = None
    for k, x in enumerate(vals):
        if x != x:
            continue
        i = first + k
        b = ub[i]
        if b < 0 or b >= nb:
            continue
        c = out[b]
        if c is None or x > c:
            out[b] = x
        if pb is not None and b > pb + 1 and mt[i] - pt <= hold:
            for j in range(pb + 1, b):
                out[j] = pv
        pb, pv, pt = b, x, mt[i]
    return out


def const_gauge_bins(cval, first, count, ub, nb, filled):
    """A constant gauge from dump `first` for `count` dumps: the unit's filled bins in that range."""
    out = [None] * nb
    if count <= 0:
        return out
    b0, b1 = max(0, ub[first]), min(nb - 1, ub[first + count - 1])
    for b in range(b0, b1 + 1):
        if filled[b]:
            out[b] = cval
    return out


def spread(inc, cov, a, b, dv, t0, step, nb):
    """Spread the increase dv over [a, b] onto bins (rate = inc / cov)."""
    ba = int((a - t0) // step)
    bb = int((b - t0) // step)
    dt = b - a
    if ba == bb:
        if 0 <= ba < nb:
            inc[ba] += dv
            cov[ba] += dt
        return
    x = a
    for k in range(ba, bb + 1):
        edge = t0 + (k + 1) * step
        y = b if edge > b else edge
        part = y - x
        if part > 0 and 0 <= k < nb:
            inc[k] += dv * part / dt
            cov[k] += part
        x = y


def counter_bins(vals, first, mt, gap, t0, step, nb, inc=None, cov=None):
    """Increase per bin of a counter (pairs of consecutive dumps; resets and gaps are skipped)."""
    if inc is None:
        inc = [0.0] * nb
        cov = [0.0] * nb
    px = NAN
    pt = None
    for k, x in enumerate(vals):
        t = mt[first + k]
        if x == x and px == px:
            dv = x - px
            dt = t - pt
            if dv >= 0 and 0 < dt <= gap:
                spread(inc, cov, pt, t, dv, t0, step, nb)
        px, pt = x, t
    return inc, cov


def zero_rate_cover(first, count, mt, gap, t0, step, nb, cov):
    """A constant counter: zero increase, but the time it covers counts (rate 0, not 'no data')."""
    for i in range(first + 1, first + count):
        a, b = mt[i - 1], mt[i]
        if 0 < b - a <= gap:
            ba = int((a - t0) // step)
            bb = int((b - t0) // step)
            if ba == bb:
                if 0 <= ba < nb:
                    cov[ba] += b - a
            else:
                x = a
                for k in range(ba, bb + 1):
                    edge = t0 + (k + 1) * step
                    y = b if edge > b else edge
                    if y > x and 0 <= k < nb:
                        cov[k] += y - x
                    x = y


def rates(inc, cov):
    return [(i * 1000.0 / c) if c > 0 else None for i, c in zip(inc, cov)]


def rnd(x):
    """Compact JSON numbers: exact integers, 4 significant digits otherwise."""
    if x is None:
        return None
    if x == int(x) and abs(x) < 9e15:
        return int(x)
    y = float("%.4g" % x)
    return int(y) if y == int(y) and abs(y) < 9e15 else y


def trim(vals):
    """(offset, values) without the leading and trailing empty bins; None if all empty."""
    i, j = 0, len(vals)
    while i < j and vals[i] is None:
        i += 1
    while j > i and vals[j - 1] is None:
        j -= 1
    if i == j:
        return None
    return i, [rnd(x) for x in vals[i:j]]


# --------------------------------------------------------------------------- metrics: export
def build_grid(units, points):
    """One time grid for every metric. points None ('max'): one bin per metric dump, the finest the
    data has; otherwise about `points` bins across the time range."""
    ts = [t for u in units if u.mtimes for t in (u.mtimes[0], u.mtimes[-1])]
    if not ts:
        return None, 60000.0
    lo, hi = min(ts), max(ts)
    periods = [period_of(u) for u in units if len(u.mtimes) > 1]
    period = statistics.median(periods) if periods else 60000.0
    if points is None:
        step = max(1000, int(round(period / 1000.0)) * 1000)
    else:
        step = nice_step(max(period * 0.95, (hi - lo) / float(points), 1000))
    t0 = (lo // step) * step
    n = int((hi - t0) // step) + 1
    return MGrid(t0, step, n), period


def export_metrics(units, members, grid, period, top_n, budget):
    """Everything the charts need from the metrics, on one time grid shared by all members."""
    metrics = collect_metrics(units)
    nb = grid.n
    ub = {}
    filled = {}
    gaps = {}
    for u in units:
        if not u.mtimes:
            continue
        b = unit_bins(u, grid.t0, grid.step)
        ub[id(u)] = b
        p = period_of(u)
        gaps[id(u)] = max(3 * p, 90000.0)
        f = [False] * nb
        for i, x in enumerate(b):
            if 0 <= x < nb:
                f[x] = True
            if i and 0 <= b[i - 1] < x - 1 and u.mtimes[i] - u.mtimes[i - 1] <= gaps[id(u)]:
                for j in range(b[i - 1] + 1, min(x, nb)):
                    f[j] = True
        filled[id(u)] = f

    out_metrics, static, flat, timestamps = [], [], [], []
    plans = []
    for name in sorted(metrics):
        mi = metrics[name]
        stats = [series_stats(s[3]) for s in mi.series]
        mi.kind = classify(mi, stats)
        if not mi.unit and name.lower().endswith("cpuload"):
            his = [st[2] for st in stats if st[2] is not None]
            if his and max(his) > 1.0:
                mi.unit = "percent"         # os.processCpuLoad without unit=... (Hazelcast 6): already a percentage
        words = set(name_words(name))
        if mi.kind != "g":
            mi.agg = "sum"
        elif mi.unit == "percent" or MAX_AGG_WORDS.intersection(words):
            mi.agg = "max"
        elif MIN_AGG_WORDS.intersection(words):
            mi.agg = "min"
        else:
            mi.agg = "max" if mi.cat == "cp" else "sum"
        mi.across = ("min" if mi.agg == "min" else "max") if mi.cat == "cp" else mi.agg
        if mi.kind == "t":
            timestamps.append(name)
            continue
        if not mi.tagkeys:
            if all(s[0] for s in stats):
                vals = {}
                for s, st in zip(mi.series, stats):
                    if st[1] is not None:
                        vals.setdefault(s[0].member, st[2])
                static.append({"n": name, "u": mi.unit, "c": mi.cat,
                               "v": [[m, rnd(v)] for m, v in sorted(vals.items())]})
                continue
            plans.append((mi, stats, None, None))
            continue
        # entity metric: which entities changed? data structures (maps, queues...): one is kept when its values
        # are not all the same, on all members together (the cleaning rule); other entities: when they moved at all.
        is_map = mi.cat == "maps"
        changed = {}
        firstv = {}
        for (_u, label, mk, _ms), st in zip(mi.series, stats):
            key = mk if is_map else label
            if key not in changed:
                changed[key] = False
            if changed[key] or st[1] is None:
                continue
            if not st[0]:
                changed[key] = True
            elif is_map:
                if key not in firstv:
                    firstv[key] = st[1]
                elif firstv[key] != st[1]:
                    changed[key] = True
        moving = [k for k, c in changed.items() if c]
        hidden = sorted(k for k, c in changed.items() if not c)
        if not moving:
            vs = set(st[1] for st in stats if st[1] is not None)
            flat.append({"n": name, "u": mi.unit, "c": mi.cat, "s": len(mi.series),
                         "v": rnd(vs.pop()) if len(vs) == 1 else None})
            continue
        plans.append((mi, stats, set(moving), hidden))

    # Payload budget: member-level series at full resolution, entity series coarser if needed.
    n_member = sum(len(set(s[0].member for s in p[0].series)) for p in plans) * nb
    ent_est = 0
    for mi, _stats, moving, _hidden in plans:
        if moving:
            ent_est += min(top_n, len(moving)) * len(set(s[0].member for s in mi.series)) * nb
    left = max(budget - n_member, budget // 4)
    fct = max(1, int(math.ceil(ent_est / float(left)))) if ent_est > left else 1

    for mi, stats, moving, hidden in plans:
        rec = export_one(mi, stats, moving, hidden, grid, ub, filled, gaps, top_n, fct)
        if rec:
            out_metrics.append(rec)
    return {"metrics": out_metrics, "static": static, "flat": flat, "timestamps": timestamps,
            "efactor": fct}


def export_one(mi, stats, moving, hidden, grid, ub, filled, gaps, top_n, fct):
    nb, t0, step = grid.n, grid.t0, grid.step
    kind, agg = mi.kind, mi.agg
    is_map = mi.cat == "maps"                   # a data structure: the cleaning rule by its name
    # 1. member-level series: the metric itself, or for entity metrics the sum (or max) over all entities
    by_unit = {}
    for (u, label, mk, ms), st in zip(mi.series, stats):
        if id(u) in ub:
            by_unit.setdefault(id(u), (u, []))[1].append((label, mk, ms, st))
    if kind == "g":
        member_vals = {}
        for uid, (u, items) in by_unit.items():
            n = len(u.mtimes)
            if len(items) == 1 and not mi.tagkeys:
                label, mk, ms, st = items[0]
                if ms.vals is None:
                    vals = const_gauge_bins(ms.cval, ms.first, ms.count, ub[uid], nb, filled[uid])
                else:
                    vals = gauge_bins(ms.vals, ms.first, ub[uid], u.mtimes, gaps[uid], nb)
            else:
                # per-dump aggregate over the entities, then binned like one gauge
                tot = [0.0] * (n + 1)
                cnt = [0] * (n + 1)
                mx = [None] * n
                for _label, _mk, ms, _st in items:
                    if ms.vals is None:
                        if agg == "sum":
                            tot[ms.first] += ms.cval
                            tot[ms.first + ms.count] -= ms.cval
                            cnt[ms.first] += 1
                            cnt[ms.first + ms.count] -= 1
                        else:
                            for i in range(ms.first, ms.first + ms.count):
                                if mx[i] is None or (ms.cval > mx[i] if agg == "max" else ms.cval < mx[i]):
                                    mx[i] = ms.cval
                    else:
                        f0 = ms.first
                        for k, x in enumerate(ms.vals):
                            if x == x:
                                i = f0 + k
                                if agg == "sum":
                                    tot[i] += x
                                    tot[i + 1] -= x
                                    cnt[i] += 1
                                    cnt[i + 1] -= 1
                                elif mx[i] is None or (x > mx[i] if agg == "max" else x < mx[i]):
                                    mx[i] = x
                if agg == "sum":
                    run, c = 0.0, 0
                    per = [NAN] * n
                    for i in range(n):
                        run += tot[i]
                        c += cnt[i]
                        if c > 0:
                            per[i] = run
                else:
                    per = [NAN if x is None else x for x in mx]
                vals = gauge_bins(per, 0, ub[uid], u.mtimes, gaps[uid], nb)
            cur = member_vals.get(u.member)
            if cur is None:
                member_vals[u.member] = vals
            else:
                for b, x in enumerate(vals):
                    if x is not None and (cur[b] is None or x > cur[b]):
                        cur[b] = x
        mlist = []
        for m in sorted(member_vals):
            tr = trim(member_vals[m])
            if tr:
                mlist.append([m, tr[0], tr[1]])
    else:
        member_ic = {}
        for uid, (u, items) in by_unit.items():
            ic = member_ic.get(u.member)
            if ic is None:
                ic = member_ic[u.member] = ([0.0] * nb, [0.0] * nb)
            inc, cov = ic
            gap = gaps[uid]
            mt = u.mtimes
            if len(items) == 1 and not mi.tagkeys:
                label, mk, ms, st = items[0]
                if ms.vals is None:
                    zero_rate_cover(ms.first, ms.count, mt, gap, t0, step, nb, cov)
                else:
                    counter_bins(ms.vals, ms.first, mt, gap, t0, step, nb, inc, cov)
                continue
            n = len(mt)
            delta = [0.0] * n
            seen = [0] * (n + 1)
            for _label, _mk, ms, _st in items:
                if ms.vals is None:
                    if ms.count > 1:
                        seen[ms.first + 1] += 1
                        seen[ms.first + ms.count] -= 1
                    continue
                f0 = ms.first
                px = NAN
                for k, x in enumerate(ms.vals):
                    if x == x and px == px and x >= px:
                        i = f0 + k
                        delta[i] += x - px
                        seen[i] += 1
                        seen[i + 1] -= 1
                    px = x
            c = 0
            for i in range(1, n):
                c += seen[i]
                if c > 0:
                    a, b = mt[i - 1], mt[i]
                    if 0 < b - a <= gap:
                        spread(inc, cov, a, b, delta[i], t0, step, nb)
        mlist = []
        for m in sorted(member_ic):
            inc, cov = member_ic[m]
            tr = trim(rates(inc, cov))
            if tr:
                mlist.append([m, tr[0], tr[1]])
    rec = {"n": mi.name, "u": mi.unit, "k": kind, "c": mi.cat, "a": agg, "tags": list(mi.tagkeys), "m": mlist}
    if mi.across != agg:
        rec["am"] = mi.across
    if not moving:
        return rec if mlist else None

    # 2. entity series for the most active entities (activity on all members together)
    score = {}
    for (_u, label, mk, _ms), st in zip(mi.series, stats):
        key = mk if is_map else label
        if key not in moving or st[1] is None:
            continue
        if kind == "c":
            s = (st[2] - st[1]) if not st[0] else 0.0
            score[label] = score.get(label, 0.0) + max(0.0, s)
        else:
            s = max(abs(st[1]), abs(st[2]))
            if s > score.get(label, -1.0):
                score[label] = s
    ranked = sorted(score, key=lambda k: (-score[k], k))
    chosen = set(ranked[:top_n])
    fstep = step * fct
    fnb = (nb + fct - 1) // fct
    ents = {}
    for u, label, _mk, ms in mi.series:
        if label not in chosen or id(u) not in ub:
            continue
        uid = id(u)
        slot = ents.setdefault(label, {})
        if kind == "g":
            if fct == 1:
                fub = ub[uid]
                ffill = filled[uid]
            else:
                fub = [b // fct for b in ub[uid]]
                ffill = [any(filled[uid][j * fct:(j + 1) * fct]) for j in range(fnb)]
            if ms.vals is None:
                vals = const_gauge_bins(ms.cval, ms.first, ms.count, fub, fnb, ffill)
            else:
                vals = gauge_bins(ms.vals, ms.first, fub, u.mtimes, gaps[uid], fnb)
            cur = slot.get(u.member)
            if cur is None:
                slot[u.member] = vals
            else:
                for b, x in enumerate(vals):
                    if x is not None and (cur[b] is None or x > cur[b]):
                        cur[b] = x
        else:
            ic = slot.get(u.member)
            if ic is None:
                ic = slot[u.member] = ([0.0] * fnb, [0.0] * fnb)
            if ms.vals is None:
                zero_rate_cover(ms.first, ms.count, u.mtimes, gaps[uid], t0, fstep, fnb, ic[1])
            else:
                counter_bins(ms.vals, ms.first, u.mtimes, gaps[uid], t0, fstep, fnb, ic[0], ic[1])
    elist = []
    for label in ranked[:top_n]:
        slot = ents.get(label)
        if not slot:
            continue
        rows = []
        for m in sorted(slot):
            v = slot[m] if kind == "g" else rates(*slot[m])
            tr = trim(v)
            if tr:
                rows.append([m, tr[0], tr[1]])
        if rows:
            elist.append([label, rows])
    rec["e"] = elist
    rec["ne"] = len(score)
    rec["nh"] = len(hidden)
    if is_map:
        rec["hidden"] = hidden[:500]
    return rec


# --------------------------------------------------------------------------- other exports
def profiler_period(units, store=False):
    """The profilers' dump period (ms), from the intervals seen; store: the store latency dumps'."""
    vals = []
    for u in units:
        for (prof, _op), r in u.prof.items():
            if (prof == "S") == store:
                vals.extend(r.dt[:2000])
        if store:
            for st in u.stot.values():
                vals.extend(b - a for a, b in zip(st[3], st[3][1:]) if b > a)
        if len(vals) > 200000:
            break
    return float(statistics.median(vals)) if vals else (60000.0 if store else 5000.0)


def export_store(members):
    """Per member, data structure and method: the store latency counters since the member started (each lifetime's
    last dump, plus what was counted before each reset), and the dumps seen."""
    ops, opi, tot, dumps, seen = [], {}, [], [], set()
    for mi, m in enumerate(members):
        agg, times = {}, []
        for u in m.units:
            for svc, st in u.stot.items():
                times.extend(st[3])
                seen.update((svc, ds) for ds in st[4])
                cur = dict(st[2])
                for name, snap in st[1].items():
                    cur[name] = add_snap(cur.get(name), snap)
                for name, snap in cur.items():
                    agg[name] = add_snap(agg.get(name), snap)
        for name in sorted(agg):
            c = agg[name]
            if not c[0]:
                continue
            if name not in opi:
                opi[name] = len(ops)
                ops.append(name)
            tot.append([mi, opi[name], c[0], c[1], c[2], [[k, c[3][k]] for k in sorted(c[3])]])
        if times:
            dumps.append([mi, len(times), int(min(times)), int(max(times))])
    called = set(tuple(o.split("\u0001")[:2]) for o in ops)
    idle = sorted(x for x in seen if x not in called)       # a store that was never called
    return {"ops": ops, "tot": tot, "dumps": dumps, "idle": [list(x) for x in idle]}


def split_interval(a, b, step):
    """[(bin, share)]: how the interval (a, b] spreads over bins of `step` ms."""
    if b <= a:
        return [(int(-(-b // step)) - 1, 1.0)]
    k = int(a // step)
    span = b - a
    out = []
    x = a
    while x < b:
        edge = (k + 1) * step
        y = b if b < edge else edge
        if y > x:
            out.append((k, (y - x) / span))
        x = y
        k += 1
    return out


def rebin(events, step):
    """Profiler intervals onto bins of `step` ms: counts and times shared out by overlap, the max to
    the bin the interval ends in. Each bin comes back as one interval inside it."""
    bins = {}
    for t, dt, n, tot, fine, mx, me in events:
        for k, f in split_interval(t - dt, t, step):
            b = bins.get(k)
            if b is None:
                b = bins[k] = [0.0, 0.0, {}, 0.0, 0]
            b[0] += n * f
            b[1] += tot * f
            fb = b[2]
            for e, c in fine:
                fb[e] = fb.get(e, 0.0) + c * f
        k = int(-(-t // step)) - 1
        b = bins.get(k)
        if b is None:
            b = bins[k] = [0.0, 0.0, {}, 0.0, 0]
        if mx > b[3] or (mx == b[3] and me and not b[4]):
            b[3], b[4] = mx, me
    return [((k + 1) * step - 1, step - 1, b[0], b[1], sorted(b[2].items()), b[3], b[4])
            for k, b in sorted(bins.items()) if b[0] > 0]


def num2(x):
    """Counts: exact when whole, else 2 decimals (counts shared out between bins)."""
    if x == int(x):
        return int(x)
    return round(x, 2)


def joined(parts):
    """One lifetime after the other: the interval arrays of one member, profiler and operation."""
    parts = sorted((r for r in parts if len(r.t)), key=lambda r: r.t[0])
    if len(parts) == 1:
        return parts[0]
    out = ProfRow()
    for r in parts:
        base = len(out.t)
        for name in ProfRow.__slots__:
            if name != "fk":
                getattr(out, name).extend(getattr(r, name))
        out.fk.extend(k + base for k in r.fk)
    return out


def row_json(mi, prof, oi, r, pt0):
    """One profiler row as JSON text (written straight away: these can be millions of intervals).
    "s" lists the intervals with calls of FINE_LO or slower: [intervals since the one listed before,
    first bucket exponent, calls in that bucket, in the next one...]."""
    ends = [int(round(x)) for x in r.t]
    gaps = [ends[0] - pt0] + [b - a for a, b in zip(ends, ends[1:])]
    dx = [[k, int(round(d))] for k, d in enumerate(r.dt) if k == 0 or int(round(d)) != gaps[k]]
    slow, prev, pos = [], 0, 0
    fc = r.fc
    for k, e0, ln in zip(r.fk, r.fe, r.fl):
        slow.append([k - prev, e0] + [num2(c) for c in fc[pos:pos + ln]])
        prev = k
        pos += ln
    return json.dumps({"m": mi, "p": prof, "o": oi, "e": gaps, "dx": dx, "n": [num2(x) for x in r.n],
                       "u": [int(round(x)) for x in r.tot],
                       "x": [int(round(m)) * (1 if e else -1) for m, e in zip(r.mx, r.me)], "s": slow},
                      separators=(",", ":"))


def export_profilers(members, step):
    """Per member, profiler and operation: every interval in which it ran (step None), or bins of
    `step` ms. Times are millis from pt0, each relative to the one before. Returns JSON text."""
    found = []
    for mi, m in enumerate(members):
        merged = {}
        for u in m.units:
            for key, r in u.prof.items():
                merged.setdefault(key, []).append(r)
        for (prof, op), parts in sorted(merged.items()):
            r = joined(parts)
            if not len(r.t):
                continue
            if step:
                binned = rebin(r.events(), step)
                if not binned:
                    continue
                r = ProfRow()
                for ev in binned:
                    r.add(*ev)
            found.append((mi, prof, op, r))
    pt0 = int(min(f[3].t[0] for f in found)) if found else 0
    ops, op_idx, texts = [], {}, []
    for mi, prof, op, r in found:
        if op not in op_idx:
            op_idx[op] = len(ops)
            ops.append(op)
        texts.append(row_json(mi, prof, op_idx[op], r, pt0))
    return {"ops": ops, "rows_json": "[" + ",".join(texts) + "]", "pt0": pt0}


SLOW_IDENT_RE = re.compile(r"identityHash=(-?\d+)")
SLOW_PART_RE = re.compile(r"partitionId=(-?\d+)")
SLOW_NAME_RE = re.compile(r"[{ ,]name='?([^,'}]*)'?")
MAX_SLOW_INVOCATIONS = 20000


def export_slowops(members):
    """Every slow invocation the slow operation detector reported, once: the detector lists the ones it keeps at
    every dump (for log.retention.seconds, 1 h by default after they last ran), with the duration so far. Kept: the
    longest duration seen, the dumps it was seen in, and whether it was still running at the member's last dump of
    that lifetime (it ended at most a few seconds before that dump)."""
    ops, opi, stacks, sti = [], {}, [], {}
    inv, counted, dumps = [], {}, []
    for mi, m in enumerate(members):
        times = []
        for u in m.units:
            if not u.slowops:
                continue
            series = sorted(u.slowops, key=lambda x: x[0])
            last_t = series[-1][0]
            seen = {}
            for t, logs in series:
                times.append(t)
                for op, total, stack, invs in logs:
                    st = "\n".join(stack)
                    if op not in opi:
                        opi[op] = len(ops)
                        ops.append(op)
                    if st not in sti:
                        sti[st] = len(stacks)
                        stacks.append(st)
                    key = (mi, opi[op], sti[st])
                    counted[key] = max(counted.get(key, 0), total)
                    for start, dur, det in invs:
                        im = SLOW_IDENT_RE.search(det)
                        k = (op, start, im.group(1) if im else det)
                        r = seen.get(k)
                        if r is None:
                            pm, nm = SLOW_PART_RE.search(det), SLOW_NAME_RE.search(det)
                            r = seen[k] = [mi, opi[op], start, dur, sti[st], t, t, 0, int(pm.group(1)) if pm else None,
                                           nm.group(1) if nm else "", det]
                            inv.append(r)
                        else:
                            r[3] = max(r[3], dur)
                            r[6] = t
                        r[7] = 1 if (t == last_t and t - (start + dur) < 3000) else 0
        if times:
            dumps.append([mi, len(times), min(times), max(times)])
    capped = len(inv) > MAX_SLOW_INVOCATIONS
    if capped:
        inv = sorted(inv, key=lambda r: -r[3])[:MAX_SLOW_INVOCATIONS]
    inv.sort(key=lambda r: (r[2], r[0]))
    return {"ops": ops, "stacks": stacks, "inv": inv, "capped": capped, "dumps": dumps,
            "logs": [[k[0], k[1], k[2], n] for k, n in sorted(counted.items())]}


MAX_INVOCATIONS = 20000                 # invocations listed as slow kept in the report (the longest pending)
MAX_INV_DETAILS = 400                   # of which the whole text is kept for the details


def export_invocations(members):
    """The invocation sampler's dumps. Per member: the invocations pending at each sample by operation (what History
    grew by since the dump before; the first dump of a lifetime is the baseline) and those pending longer than the
    slow threshold (SlowHistory); the totals since each lifetime started; every invocation listed as slow, once, with
    the longest it was seen pending and whether it was still pending at the member's last dump, gone by the next dump,
    or unknown (the next dump's list was cut at the print limit)."""
    tot_h, tot_s = {}, {}
    ser, dumps, recs = [], [], []
    addr = {}
    for mi, m in enumerate(members):
        if m.key[0] == "addr":
            addr[(norm_host(m.key[1]), m.key[2])] = mi
    for mi, m in enumerate(members):
        pts, times, ntrunc, gaps = [], [], 0, []
        for u in m.units:
            ds = u.invd
            if not ds:
                continue
            iv = [b[0] - a[0] for a, b in zip(ds, ds[1:]) if b[0] > a[0]]
            per = sorted(iv)[len(iv) // 2] if iv else 0
            if per:
                gaps.append(per)
            for j, (t, trunc, hist, slow, inside) in enumerate(ds):
                if inside:
                    times.append(t)
                    ntrunc += 1 if trunc else 0
                if j == 0 or not inside:
                    continue
                pt, ph, ps = ds[j - 1][0], ds[j - 1][2], ds[j - 1][3]
                k = max(1, int(round((t - pt) / float(per)))) if per else 1
                dh, dsl = {}, {}
                for src, prev, out in ((hist, ph, dh), (slow, ps, dsl)):
                    for op, n in src.items():
                        d = n - prev.get(op, 0)
                        if d < 0:
                            d = n                       # lower than before: counted again from 0
                        if d > 0:
                            out[op] = d / float(k)
                pts.append((t, k, dh, dsl))
            last = ds[-1]
            for op, n in last[2].items():
                tot_h[(mi, op)] = tot_h.get((mi, op), 0) + n
            for op, n in last[3].items():
                tot_s[(mi, op)] = tot_s.get((mi, op), 0) + n
            dtimes = [d[0] for d in ds]
            for r in u.invs.values():
                if not r.n:
                    continue
                j = bisect.bisect_right(dtimes, r.last)
                state = 2 if j >= len(ds) else (0 if ds[j][1] else 1)
                tm = addr.get(inv_target_key(r.target), -1) if not r.local else mi
                recs.append((mi, r, state, tm))
        if times:
            per = min(gaps) if gaps else 60000
            cov = []
            for t in sorted(times):                     # where the sampler wrote: dumps closer than 2.5 periods
                if cov and t - cov[-1][1] <= max(2.5 * per, 90000):
                    cov[-1][1] = int(t)
                else:
                    cov.append([int(t), int(t)])
            dumps.append([mi, len(times), int(min(times)), int(max(times)), ntrunc, int(min(gaps)) if gaps else 0, cov])
        if pts:
            ser.append((mi, pts))
    # the operations, the slowest first: SlowHistory totals, then History
    names = set(op for _mi, op in tot_h) | set(op for _mi, op in tot_s) | set(r.desc for _mi, r, _s, _t in recs)
    rank = dict((op, (sum(v for (mi, o), v in tot_s.items() if o == op), sum(v for (mi, o), v in tot_h.items() if o == op))) for op in names)
    ops = sorted(names, key=lambda op: (-rank[op][0], -rank[op][1], op))
    oi = dict((op, i) for i, op in enumerate(ops))
    out_ser = []
    for mi, pts in ser:
        pts.sort(key=lambda x: x[0])
        col = {"m": mi, "t": [], "p": {}, "s": {}}
        prev = 0
        anyk = any(k > 1 for _t, k, _h, _s in pts)
        if anyk:
            col["k"] = []
        for i, (t, k, dh, dsl) in enumerate(pts):
            col["t"].append(int(t) - prev)
            prev = int(t)
            if anyk:
                col["k"].append(k)
            for src, dst in ((dh, col["p"]), (dsl, col["s"])):
                for op, v in src.items():
                    dst.setdefault(oi[op], []).extend([i, int(v) if v == int(v) else round(v, 2)])
        col["p"] = [[o, v] for o, v in sorted(col["p"].items())]
        col["s"] = [[o, v] for o, v in sorted(col["s"].items())]
        out_ser.append(col)
    capped = len(recs) > MAX_INVOCATIONS
    recs.sort(key=lambda x: -x[1].dur)
    recs = recs[:MAX_INVOCATIONS]
    raw = []
    inv = []
    for mi, r, state, tm in recs:
        ri = -1
        if r.raw is not None and len(raw) < MAX_INV_DETAILS:
            ri = len(raw)
            raw.append(r.raw)
        inv.append([mi, oi[r.desc], r.name, r.part, r.target, 1 if r.local else 0, tm, int(r.start), r.dur, int(r.first), int(r.last), r.n,
                    r.timeout, r.invokes, r.bexp, r.brecv, r.resp, r.conn, r.hb, state, ri])
    inv.sort(key=lambda x: (x[7], x[0]))
    tot = [[mi, oi[op], n, tot_s.get((mi, op), 0)] for (mi, op), n in sorted(tot_h.items())]
    tot += [[mi, oi[op], 0, n] for (mi, op), n in sorted(tot_s.items()) if (mi, op) not in tot_h]
    return {"ops": ops, "ser": out_ser, "tot": tot, "inv": inv, "raw": raw, "dumps": dumps, "capped": capped}


def inv_totals(col):
    """The times of a member's sampler points and, at each, the invocations pending and the slow ones (all operations)."""
    times, t = [], 0
    for dt in col["t"]:
        t += dt
        times.append(t)
    out = []
    for key in ("p", "s"):
        tot = [0.0] * len(times)
        for _o, flat in col[key]:
            for j in range(0, len(flat), 2):
                tot[flat[j]] += flat[j + 1]
        out.append(list(zip(times, tot)))
    return out


def inv_short(desc):
    """GetOperation, PartitionIteratingOperation(MapGetAllOperationFactory)."""
    if "(" in desc:
        a, _p, b = desc.partition("(")
        return a.rsplit(".", 1)[-1] + "(" + b.rstrip(")").rsplit(".", 1)[-1] + ")"
    return desc.rsplit(".", 1)[-1]


def inv_target_key(label):
    """(host, port) of an address label, as the members are keyed: for the target of an invocation."""
    h, _sep, p = label.rpartition(":")
    return (norm_host(h), int(p)) if p.isdigit() else None


def export_props(members):
    """The build, system properties and Hazelcast properties of each lifetime (values kept once)."""
    keys, kidx, vals, vidx = [], {}, [], {}

    def K(k):
        if k not in kidx:
            kidx[k] = len(keys)
            keys.append(k)
        return kidx[k]

    def V(v):
        if v not in vidx:
            vidx[v] = len(vals)
            vals.append(v)
        return vidx[v]
    lives, rows, changes = [], [], []
    for mi, m in enumerate(members):
        for ui, u in enumerate(m.units):
            if not u.props:
                continue
            lives.append([mi, ui, int(u.props_t) if u.props_t is not None else None, int(u.cover[0][0]) if u.cover else None])
            for sec, d in u.props.items():
                si = PROP_SECTIONS.index(sec)
                for k in sorted(d):
                    rows.append([mi, ui, si, K(k), V(d[k])])
            for t, sec, k, a, b in u.prop_changes:
                changes.append([mi, ui, int(t), PROP_SECTIONS.index(sec), K(k), V(a) if a is not None else -1, V(b) if b is not None else -1])
    return {"secs": list(PROP_SECTIONS), "keys": keys, "vals": vals, "rows": rows, "lives": lives, "changes": changes}


def export_cluster(members):
    lists = []
    lidx = {}
    runs = []
    for mi, m in enumerate(members):
        for u in m.units:
            cur = None
            last_t = None
            for t, size, master, mems, _cid in sorted(u.cluster, key=lambda c: c[0]):
                if t == last_t or size is None:
                    continue                    # the same dump read twice (a copied file), or no size in it
                last_t = t
                key = ", ".join(mems)
                if key not in lidx:
                    lidx[key] = len(lists)
                    lists.append(key)
                st = (size, 1 if master else 0, lidx[key])
                if cur is not None and tuple(cur[3:]) == st and t - cur[2] <= 180000:
                    cur[2] = t
                    continue
                if cur is not None:
                    runs.append(cur)
                cur = [mi, t, t] + list(st)
            if cur is not None:
                runs.append(cur)
    return runs, lists


def export_events(members):
    evs = []
    dropped = {}
    for mi, m in enumerate(members):
        per = {}
        seen = set()
        for u in m.units:
            for t, kind, text, who, size in u.events:
                if (t, kind, text) in seen:
                    continue                    # the same event read twice (a copied file)
                seen.add((t, kind, text))
                n = per.get(kind, 0)
                per[kind] = n + 1
                if n >= MAX_EVENTS_PER_KIND:
                    dropped[kind] = dropped.get(kind, 0) + 1
                    continue
                evs.append([t, mi, kind, text, who, size])
    evs.sort(key=lambda e: (e[0], e[1]))
    hb = []
    for mi, m in enumerate(members):
        seen = set()
        for u in m.units:
            for t, kind, peer, nohb, dev in u.hb:
                if (t, kind, peer) not in seen:
                    seen.add((t, kind, peer))
                    hb.append([t, mi, kind, peer, rnd(nohb), rnd(dev)])
    hb.sort(key=lambda e: (e[0], e[1]))
    return evs, dropped, hb


# --------------------------------------------------------------------------- stuck threads
# At every metrics dump, for every thread of every member lifetime:
#   IO thread (tcp.inputThread / tcp.outputThread): no progress when tasks wait in its queue (taskQueueSize > 0)
#     and it has done no I/O since the previous dump (idleTimeMillis covers the time since that dump);
#   partition / generic operation thread: no progress when operations wait for it (normal + priority pending > 0)
#     and it completed none since the previous dump (completedTotalCount unchanged).
# Consecutive such dumps make an episode, with the dump before them when work was already waiting there. It is
# stuck when work was waiting at two dumps in a row at least: the thread stayed idle while its queue stayed > 0.
STUCK_RE = re.compile(r"(?:^|,)metric=[^,]*\.(?:taskQueueSize|idleTimeMillis|idleTimeMs|completedTaskCount|"
                      r"normalPendingCount|priorityPendingCount|completedTotalCount)(?:,|$)")
STUCK_CONTEXT = 360             # dumps kept before and after an episode, for its charts
STUCK_DETAIL_MAX = 800000       # values over all those charts
EPOCH_LIKE = 1e12               # an idle time this big: the thread never selected anything
# per member, at each dump, the highest over its threads of: the time without I/O of an IO thread that has
# tasks waiting, the time without a completed operation of an operation thread that has operations waiting
STALL_IO = "threads.io.idleWhileTasksWait.max"
STALL_OP = "threads.op.noProgressWhileWaiting.max"


def dense(ms, n):
    """The values of a series for dumps 0..n-1 (NaN where it has none)."""
    out = [NAN] * n
    if ms is None:
        return out
    if ms.vals is None:
        for i in range(max(0, ms.first), min(n, ms.first + ms.count)):
            out[i] = ms.cval
    else:
        a, b = max(0, ms.first), min(n, ms.first + len(ms.vals))
        if b > a:
            out[a:b] = ms.vals[a - ms.first:b - ms.first]
    return out


def unit_threads(u, cache):
    """The threads of one lifetime the stuck check can read: {(family, tags): {metric suffix: MSeries}}."""
    out = {}
    for attrs, ms in u.series.items():
        p = cache.get(attrs)
        if p is None:
            p = False
            if STUCK_RE.search(attrs):
                name, _unit, tags = parse_attrs(attrs)
                fam, _dot, suf = name.rpartition(".")
                if fam and tags:
                    p = (fam, tuple(tags), suf)
            cache[attrs] = p
        if p:
            out.setdefault((p[0], p[1]), {})[p[2]] = ms
    return out


def thread_values(u, d, kind):
    """Per dump of the lifetime: tasks waiting, idle ms (IO threads) and the completed counter."""
    n = len(u.mtimes)
    if kind == "io":
        wait = dense(d["taskQueueSize"], n)
        idle = dense(d.get("idleTimeMillis") or d.get("idleTimeMs"), n)
        done = dense(d["completedTaskCount"], n) if "completedTaskCount" in d else None
    else:
        a, b = dense(d.get("normalPendingCount"), n), dense(d.get("priorityPendingCount"), n)
        wait = [NAN if (x != x and y != y) else (x if x == x else 0.0) + (y if y == y else 0.0) for x, y in zip(a, b)]
        idle = None
        done = dense(d["completedTotalCount"], n)
    return wait, idle, done


def stuck_runs(mt, wait, idle, done, kind, period):
    """The stuck episodes of one thread: [first dump with work waiting, first dump without progress, last dump].
    A dump is without progress when work waits and none was done since the dump before (no I/O for an IO
    thread: idleTimeMillis at least 90% of the time since that dump; no completed operation for an operation
    thread). Consecutive ones make an episode, with the dump before them when work was already waiting there,
    kept when work waited at two dumps in a row at least."""
    runs = []
    for i in range(len(mt)):
        if not wait[i] > 0:                         # 0, or no value
            continue
        if kind == "io":
            gap = mt[i] - mt[i - 1] if i else period
            stuck = idle[i] >= 0.9 * min(gap, period)   # NaN never is
        else:
            stuck = i > 0 and done[i] == done[i - 1]    # NaN never equals
        if not stuck:
            continue
        if runs and runs[-1][2] == i - 1:
            runs[-1][2] = i
        else:
            runs.append([i, i, i])
    out = []
    for r in runs:
        if r[1] > 0 and wait[r[1] - 1] > 0:
            r[0] = r[1] - 1                         # work was already waiting at the dump before
        if r[2] > r[0]:
            out.append(r)
    return out


def thread_window(mt, wait, idle, done, a, b):
    """Chart data for dumps a..b: times, tasks waiting, idle ms, completed per second."""
    rate = []
    for i in range(a, b + 1):
        r = None
        if done is not None and i > 0:
            c, pc, dt = done[i], done[i - 1], mt[i] - mt[i - 1]
            if c == c and pc == pc and c >= pc and dt > 0:
                r = rnd((c - pc) * 1000.0 / dt)
        rate.append(r)
    win = {"t": [int(t) for t in mt[a:b + 1]],
           "w": [rnd(x) if x == x else None for x in wait[a:b + 1]]}
    if idle is not None:
        win["i"] = [rnd(x) if x == x and x < EPOCH_LIKE else None for x in idle[a:b + 1]]
    if done is not None:
        win["r"] = rate
    return win


def stall_values(mt, wait, idle, done, kind, out):
    """Fold one thread into the per-dump highest stall of its lifetime (see STALL_IO / STALL_OP)."""
    since = None        # operation threads: the last dump at which the completed count had moved
    wprev = None        # the dump before the current run of dumps with work waiting
    for i in range(len(mt)):
        w = wait[i]
        if w != w:
            wprev = None
            continue
        v = 0.0
        if kind == "io":
            x = idle[i]
            if w > 0 and x == x:
                v = x if x < EPOCH_LIKE else mt[i] - mt[0]
        else:
            c = done[i]
            moved = c == c and (i == 0 or c != done[i - 1])
            if moved:
                since = mt[i]
            if w > 0:
                if wprev is None:
                    wprev = mt[i - 1] if i else mt[i]
                if not moved and c == c and since is not None:
                    v = mt[i] - max(since, wprev)
            else:
                wprev = None
        cur = out[i]
        if cur != cur or v > cur:
            out[i] = v


def add_series(u, attrs, vals):
    """A derived series on the dumps of a lifetime (NaN: no value at that dump)."""
    ms = None
    for i, v in enumerate(vals):
        if v != v:
            continue
        if ms is None:
            ms = MSeries(i, v)
        else:
            ms.add(i, v)
    if ms is not None:
        u.series[attrs] = ms


def export_stuck(members):
    """Threads checked, stuck episodes and the charts of the stuck threads. Adds the per-member stall series
    (STALL_IO, STALL_OP) to the lifetimes, so they are exported with the other metrics."""
    cache = {}
    fams, fam_idx = [], {}
    threads, tidx = [], {}
    eps = []
    windows = {}                        # thread index -> [(stuck ms, window)]
    for mi, m in enumerate(members):
        units = sorted(m.units, key=lambda x: x.start or 0)
        for ui, u in enumerate(units):
            if not u.mtimes:
                continue
            mt = u.mtimes
            period = period_of(u)
            later = [x.start for x in units[ui + 1:] if x.start is not None]
            stall = {"io": [NAN] * len(mt), "op": [NAN] * len(mt)}
            for (fam, tags), d in sorted(unit_threads(u, cache).items()):
                if "taskQueueSize" in d and ("idleTimeMillis" in d or "idleTimeMs" in d):
                    kind = "io"
                elif "completedTotalCount" in d and ("normalPendingCount" in d or "priorityPendingCount" in d):
                    kind = "op"
                else:
                    continue
                label = next((v for k, v in tags if k == "thread"), None) or " · ".join(v for k, v in tags)
                wait, idle, done = thread_values(u, d, kind)
                seen = [i for i, x in enumerate(wait) if x == x]
                if not seen:
                    continue
                if fam not in fam_idx:
                    fam_idx[fam] = len(fams)
                    fams.append(fam)
                key = (mi, fam, label)
                ti = tidx.get(key)
                if ti is None:
                    ti = tidx[key] = len(threads)
                    threads.append([mi, fam_idx[fam], kind, label, int(mt[seen[0]]), int(mt[seen[-1]])])
                else:
                    threads[ti][4] = min(threads[ti][4], int(mt[seen[0]]))
                    threads[ti][5] = max(threads[ti][5], int(mt[seen[-1]]))
                stall_values(mt, wait, idle, done, kind, stall[kind])
                runs = stuck_runs(mt, wait, idle, done, kind, period)
                n = len(mt)
                spans = []
                for w0, fi, la in runs:
                    prev = mt[fi - 1] if fi else mt[fi] - period
                    start = prev
                    if kind == "io" and idle[fi] < EPOCH_LIKE:
                        last_io = mt[fi] - idle[fi]
                        if w0 < fi:     # tasks already waited at the dump before: from the last I/O
                            start = max(last_io, mt[w0 - 1] if w0 else mt[w0] - period)
                        else:           # tasks came after the dump before
                            start = max(prev, last_io)
                    mx_idle = None
                    if kind == "io":
                        top = max(idle[fi:la + 1])
                        mx_idle = rnd(top) if top < EPOCH_LIKE else None
                    if la + 1 < n:
                        ok = wait[la + 1] == wait[la + 1] and (kind != "io" or idle[la + 1] == idle[la + 1])
                        then, tt = ("ok" if ok else "gone"), mt[la + 1]
                    elif later:
                        then, tt = "restart", later[0]
                    else:
                        then, tt = "end", mt[la]
                    eps.append([ti, int(start), int(mt[w0]), int(mt[la]), la - w0 + 1, mx_idle,
                                rnd(max(wait[w0:la + 1])), then, int(tt)])
                    spans.append((max(0, w0 - STUCK_CONTEXT), min(n - 1, la + STUCK_CONTEXT), mt[la] - start))
                merged = []
                for a, b, s in spans:
                    if merged and a <= merged[-1][1] + 1:
                        merged[-1][1] = max(merged[-1][1], b)
                        merged[-1][2] += s
                    else:
                        merged.append([a, b, s])
                for a, b, s in merged:
                    windows.setdefault(ti, []).append((s, thread_window(mt, wait, idle, done, a, b)))
            add_series(u, "unit=ms,metric=" + STALL_IO, stall["io"])
            add_series(u, "unit=ms,metric=" + STALL_OP, stall["op"])
    # charts for the threads stuck the longest first, within the size limit
    detail, used, cut = {}, 0, 0
    for ti in sorted(windows, key=lambda k: -sum(w[0] for w in windows[k])):
        size = sum(len(w[1]["t"]) * len(w[1]) for w in windows[ti])
        if used + size > STUCK_DETAIL_MAX and detail:
            cut += 1
            continue
        used += size
        detail[str(ti)] = [w[1] for w in windows[ti]]
    eps.sort(key=lambda e: (e[1], e[0]))
    return {"fam": fams, "list": threads, "eps": eps, "detail": detail, "cut": cut}


# --------------------------------------------------------------------------- saturated executors
# An executor is saturated at a dump when its pool is full (poolSize >= maximumPoolSize) and tasks wait in its queue
# (queueSize > 0): every thread is busy and work piles up. Kept when that lasts two dumps in a row at least.
POOL_RE = re.compile(r"(?:^|,)metric=[^,]*\.(?:queueSize|poolSize|maximumPoolSize|completedTasks|completedTaskCount|"
                     r"remainingQueueCapacity)(?:,|$)")


def export_saturation(members):
    cache = {}
    execs, eidx, eps = [], {}, []
    for mi, m in enumerate(members):
        units = sorted(m.units, key=lambda x: x.start or 0)
        for ui, u in enumerate(units):
            if not u.mtimes:
                continue
            mt, n = u.mtimes, len(u.mtimes)
            later = [x.start for x in units[ui + 1:] if x.start is not None]
            groups = {}
            for attrs, ms in u.series.items():
                p = cache.get(attrs)
                if p is None:
                    p = False
                    if POOL_RE.search(attrs):
                        name, _unit, tags = parse_attrs(attrs)
                        fam, _dot, suf = name.rpartition(".")
                        if fam and tags:
                            p = (fam, tuple(tags), suf)
                    cache[attrs] = p
                if p:
                    groups.setdefault((p[0], p[1]), {})[p[2]] = ms
            for (fam, tags), d in sorted(groups.items()):
                if not ("queueSize" in d and "poolSize" in d and "maximumPoolSize" in d):
                    continue
                q, pool, mx = dense(d["queueSize"], n), dense(d["poolSize"], n), dense(d["maximumPoolSize"], n)
                done = dense(d.get("completedTasks") or d.get("completedTaskCount"), n)
                cap = dense(d.get("remainingQueueCapacity"), n)
                seen = [i for i in range(n) if q[i] == q[i] and pool[i] == pool[i]]
                if not seen:
                    continue
                label = next((v for k, v in tags if k == "name"), None) or " · ".join(v for k, v in tags)
                key = (mi, fam, label)
                xi = eidx.get(key)
                if xi is None:
                    xi = eidx[key] = len(execs)
                    execs.append([mi, fam, label, int(mt[seen[0]]), int(mt[seen[-1]])])
                else:
                    execs[xi][3] = min(execs[xi][3], int(mt[seen[0]]))
                    execs[xi][4] = max(execs[xi][4], int(mt[seen[-1]]))
                runs = []
                for i in range(n):
                    if q[i] > 0 and mx[i] > 0 and pool[i] >= mx[i]:
                        if runs and runs[-1][1] == i - 1:
                            runs[-1][1] = i
                        else:
                            runs.append([i, i])
                for a, b in runs:
                    if b == a:
                        continue
                    rate = None
                    if done[b] == done[b] and done[a] == done[a] and mt[b] > mt[a] and done[b] >= done[a]:
                        rate = rnd((done[b] - done[a]) * 1000.0 / (mt[b] - mt[a]))
                    if b + 1 < n:
                        then, tt = "ok", mt[b + 1]
                    elif later:
                        then, tt = "restart", later[0]
                    else:
                        then, tt = "end", mt[b]
                    full = [cap[i] for i in range(a, b + 1) if cap[i] == cap[i]]
                    eps.append([xi, int(mt[a]), int(mt[b]), b - a + 1, rnd(max(q[a:b + 1])), rnd(max(pool[a:b + 1])),
                                rnd(max(mx[a:b + 1])), rate, then, int(tt), 1 if full and min(full) <= 0 else 0])
    eps.sort(key=lambda e: (e[1], e[0]))
    return {"list": execs, "eps": eps}


# --------------------------------------------------------------------------- data transfer
# What each member received and sent, per peer: another member, a client, a WAN target or another cluster. From the
# counters of each connection (tcp.connection.in.bytesRead / out.bytesWritten, tagged pipelineId=/local->/remote),
# and in all from the IO threads (tcp.inputThread / outputThread.bytesTransceived), which also count the connections
# opened and closed between two dumps.
PIPE_RE = re.compile(r"(?:^|,)metric=tcp\.connection\.(?:in\.bytesRead|out\.bytesWritten)(?:,|$)")
IOBYTES_RE = re.compile(r"(?:^|,)metric=tcp\.(?:input|output)Thread\.bytesTransceived(?:,|$)")
CTYPE_RE = re.compile(r"(?:^|,)metric=tcp\.connection\.connectionType(?:,|$)")
CONN_TYPES = {1: "member", 2: "Java client", 3: "C++ client", 4: "Python client", 5: "Node.js client", 6: "Go client",
              7: "REST client", 8: "Memcache client", 9: "Management Center", 10: ".NET client", 11: "CLC",
              12: "Management Center CLC", -1: "other client"}
LOOPBACK_IPS = frozenset(("localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1"))
WILDCARD_IPS = frozenset(("0.0.0.0", "::", "0:0:0:0:0:0:0:0", "*"))
LINK_BINS = 6000                    # time bins per member and peer
TRANSFER_BUDGET = 300000            # time bins of all the member-peer series together
MAX_CONNS = 3000                    # connections listed in the report


def norm_ip(host):
    h = host.strip().strip("[]").lower()
    if h.startswith("::ffff:") and "." in h:
        h = h[7:]
    if h in LOOPBACK_IPS:
        return "127.0.0.1"
    if h in WILDCARD_IPS:
        return "*"
    return h


def sock_addr(text):
    """'/10.0.0.1:5701', 'host/10.0.0.1:5701', '/[::1]:5701', '/0:0:0:0:0:0:0:1:5701' -> (ip, port), or None."""
    s = text.strip()
    if "/" in s:
        s = s.rsplit("/", 1)[1]
    host, _sep, port = s.rpartition(":")
    if not host or not port.isdigit():
        return None
    return norm_ip(host), int(port)


def parse_pipeline(pid):
    """'/10.0.0.1:5701->/10.0.0.5:61234' -> ((ip, port) local, (ip, port) remote), or None."""
    a, sep, b = pid.partition("->")
    if not sep:
        return None
    la, ra = sock_addr(a), sock_addr(b)
    return (la, ra) if la and ra else None


def dump_gap(mt):
    """Dumps further apart than this have a gap between them (3 periods, at least the period plus a minute)."""
    d = sorted(mt[i] - mt[i - 1] for i in range(1, len(mt)))
    per = d[len(d) // 2] if d else 60000
    return max(3 * per, per + 60000)


def counter_growth(ms, mt, gap):
    """(dump index, growth) of a counter of one lifetime, from the dump before. Its first value counts too when it
    starts after the first dump of the lifetime (a connection opened in between). Growth across a gap in the dumps
    is left out: when it happened is not known."""
    out = []
    if ms is None:
        return out
    n = len(mt)
    if ms.vals is None:
        i = ms.first
        if 0 < i < n and ms.cval > 0 and mt[i] - mt[i - 1] <= gap:
            out.append((i, ms.cval))
        return out
    prev = pi = None
    for j, v in enumerate(ms.vals):
        if v != v:
            continue
        i = ms.first + j
        if i >= n:
            break
        if prev is None:
            if j == 0 and 0 < i and v > 0 and mt[i] - mt[i - 1] <= gap:
                out.append((i, v))
        else:
            d = v - prev if v >= prev else v        # a counter that went down started again
            if d > 0 and mt[i] - mt[pi] <= gap:
                out.append((i, d))
        prev, pi = v, i
    return out


def export_transfer(members):
    """Bytes received and sent by each member, per peer. Each connection (pipeline) is matched to its peer: an
    outbound one (local address 0.0.0.0, or to a member port) goes to a member of the cluster, else to another
    cluster (a WAN target); an inbound one comes from a member whose outbound connection has the same ports, else
    from a client (the client type when DEBUG metrics give tcp.connection.connectionType), else from another
    cluster's member (WAN replication into this cluster, DEBUG metrics only)."""
    cache = {}
    pipes = []                              # [mi, pid, local, remote, (seconds, bytes) in, (seconds, bytes) out, first t, last t]
    totals = {}                             # mi -> {second: [received, sent]} of all bytes (IO threads)
    ctypes = {}                             # (mi, ip, port) -> connection type id

    def growth(ms, mt, gap):
        """The growth of a counter as (seconds, bytes) arrays: compact, there can be millions."""
        ts, gs = array.array("q"), array.array("d")
        for i, g in counter_growth(ms, mt, gap):
            ts.append(int(mt[i] // 1000))
            gs.append(g)
        return ts, gs

    def into(d, tg, col):
        for t, g in zip(*tg):
            c = d.get(t)
            if c is None:
                c = d[t] = [0.0, 0.0]
            c[col] += g
    for mi, m in enumerate(members):
        for u in m.units:
            mt = u.mtimes
            if not mt:
                continue
            gap = dump_gap(mt)
            per = {}
            for attrs, ms in u.series.items():
                p = cache.get(attrs)
                if p is None:
                    p = False
                    if PIPE_RE.search(attrs) or IOBYTES_RE.search(attrs) or CTYPE_RE.search(attrs):
                        name, _unit, tags = parse_attrs(attrs)
                        tg = dict(tags)
                        if name.startswith("tcp.connection.") and "pipelineId" in tg:
                            p = ("pipe", tg["pipelineId"], 0 if name.endswith(".bytesRead") else 1)
                        elif name.endswith(".bytesTransceived"):
                            p = ("io", 0 if name.startswith("tcp.input") else 1)
                        elif name.endswith(".connectionType") and "endpoint" in tg:
                            a = ADDR_RE.match(tg["endpoint"])
                            if a:
                                p = ("type", norm_ip(a.group(1)), int(a.group(2)))
                    cache[attrs] = p
                if not p:
                    continue
                if p[0] == "pipe":
                    per.setdefault(p[1], [None, None])[p[2]] = ms
                elif p[0] == "io":
                    into(totals.setdefault(mi, {}), growth(ms, mt, gap), p[1])
                else:
                    v = ms.cval if ms.vals is None else next((x for x in reversed(ms.vals) if x == x), None)
                    if v is not None:
                        ctypes[(mi, p[1], p[2])] = int(v)
            for pid, (ms_in, ms_out) in per.items():
                ends = parse_pipeline(pid)
                if ends is None:
                    continue
                idx = [x.first for x in (ms_in, ms_out) if x is not None]
                ln = [x.first + x.length() - 1 for x in (ms_in, ms_out) if x is not None]
                fi, la = max(0, min(idx)), min(len(mt) - 1, max(ln))
                pipes.append([mi, pid, ends[0], ends[1], growth(ms_in, mt, gap), growth(ms_out, mt, gap), mt[fi], mt[la]])
    if not pipes and not totals:
        return None
    # the members' addresses: from their file names, the local side of the connections they accepted, and the
    # member lists (members without diagnostics files)
    own = {}
    for mi, m in enumerate(members):
        if m.key[0] == "addr":
            own[mi] = (norm_ip(m.key[1]), m.key[2])
    srv = {mi: set([own[mi][1]]) if mi in own else set() for mi in range(len(members))}
    seen_local = {}
    for p in pipes:
        if p[2][0] != "*":
            seen_local.setdefault((p[0], p[2][1]), set()).add(p[3])
    for (mi, port), remotes in seen_local.items():
        if len(remotes) >= 2:                   # a port many connections arrive on: a server port
            srv[mi].add(port)
    ips = {mi: set([own[mi][0]]) if mi in own else set() for mi in range(len(members))}
    for p in pipes:
        if p[2][0] != "*" and p[2][1] in srv[p[0]]:
            ips[p[0]].add(p[2][0])
    addr_member = {}
    for mi in range(len(members)):
        for ip in ips[mi]:
            for port in srv[mi]:
                addr_member[(ip, port)] = mi
    cluster = set(addr_member)
    for m in members:
        for u in m.units:
            for c in u.cluster:
                for lab in c[3]:
                    host, _sep, port = lab.rpartition(":")
                    if host and port.isdigit():
                        cluster.add((norm_ip(host), int(port)))
    cluster_ips = set(a[0] for a in cluster)
    all_srv = set(a[1] for a in cluster) | set(x for v in srv.values() for x in v)

    def outbound(p):
        mi, (lip, lport), (rip, rport) = p[0], p[2], p[3]
        if lip == "*":
            return True
        if lport in srv[mi]:
            return False
        if (rip, rport) in cluster or rport in all_srv:
            return True
        return lport > rport
    dirs = [outbound(p) for p in pipes]
    out_by_port = {}
    for k, p in enumerate(pipes):
        if dirs[k]:
            out_by_port.setdefault((p[2][1], p[3][1]), []).append(k)
    peers, pidx, links, conns = [], {}, {}, []
    peer_types = {}

    def peer(kind, label, detail=""):
        key = (kind, label)
        i = pidx.get(key)
        if i is None:
            i = pidx[key] = len(peers)
            peers.append([kind, label, detail])
        return i
    found = []
    for k, p in enumerate(pipes):
        mi, (lip, lport), (rip, rport) = p[0], p[2], p[3]
        typ = None
        if dirs[k]:
            n = addr_member.get((rip, rport))
            if n is not None and n != mi:
                pi = peer("member", members[n].label)
            elif (rip, rport) in cluster:
                pi = peer("member", member_label(rip, rport), "no diagnostics files")
            else:
                pi = peer("wan", member_label(rip, rport), "another cluster: a WAN target")
        else:
            match = []
            for k2 in out_by_port.get((rport, lport), ()):
                q = pipes[k2]
                if q[0] != mi and (q[3][0] in ips[mi] or not ips[mi] or lip == "*") and \
                        (not ips[q[0]] or rip in ips[q[0]]) and q[6] <= p[7] and p[6] <= q[7]:
                    match.append(q[0])
            typ = ctypes.get((mi, rip, rport))
            if len(set(match)) == 1:
                pi = peer("member", members[match[0]].label)
            elif typ is not None and typ != 1:
                pi = peer("client", rip)
                peer_types.setdefault(pi, set()).add(CONN_TYPES.get(typ, "client"))
            else:
                other = next((port for (m2, ip2, port), t2 in ctypes.items()
                              if m2 == mi and ip2 == rip and t2 == 1 and (ip2, port) not in cluster), None)
                if other is not None:
                    pi = peer("wan", member_label(rip, other), "another cluster: WAN replication into this one")
                elif rip in cluster_ips and not all((ip, port) in addr_member for ip, port in cluster if ip == rip):
                    pi = peer("unknown", rip, "a member without diagnostics files, or a client on a member's host")
                else:
                    pi = peer("client", rip)
        found.append((pi, typ, dirs[k]))
    wan_src = {}
    for (pi, _typ, out), p in zip(found, pipes):
        if not out and peers[pi][0] == "wan":
            wan_src.setdefault(p[3][0], pi)
    for k, p in enumerate(pipes):
        mi, (lip, lport), (rip, rport) = p[0], p[2], p[3]
        pi, typ, out = found[k]
        if not out and typ is None and peers[pi][0] in ("client", "unknown") and rip in wan_src:
            pi = wan_src[rip]
        key = (mi, pi)
        b = links.get(key)
        if b is None:
            b = links[key] = [{}, 0]
        into(b[0], p[4], 0)
        into(b[0], p[5], 1)
        b[1] += 1
        conns.append([mi, pi, p[1], int(p[6]), int(p[7]), int(sum(p[4][1])), int(sum(p[5][1])),
                      CONN_TYPES.get(typ, "") if typ is not None else ""])
    for pi, ts in peer_types.items():
        peers[pi][2] = ", ".join(sorted(ts))
    used = sorted(set(pi for _mi, pi in links))             # the peers some connection ended up with
    remap = {pi: i for i, pi in enumerate(used)}
    peers = [peers[pi] for pi in used]
    links = {(mi, remap[pi]): b for (mi, pi), b in links.items()}
    for c in conns:
        c[1] = remap[c[1]]

    def to_bins(d):
        b = Bins(LINK_BINS, 2)
        b.d = d
        if len(d) >= LINK_BINS:
            b.coarsen(LINK_BINS)
        return b
    links = {k: [to_bins(v[0]), v[1]] for k, v in links.items()}
    totals = {mi: to_bins(d) for mi, d in totals.items()}
    fit_bins([b[0] for b in links.values()] + list(totals.values()), TRANSFER_BUDGET)
    conns.sort(key=lambda c: -(c[5] + c[6]))
    return {"peers": peers,
            "links": [[mi, pi, b[0].step * 1000, b[1], [[x[0], int(x[1]), int(x[2])] for x in b[0].export()]]
                      for (mi, pi), b in sorted(links.items())],
            "totals": [[mi, b.step * 1000, [[x[0], int(x[1]), int(x[2])] for x in b.export()]] for mi, b in sorted(totals.items())],
            "conns": conns[:MAX_CONNS], "nconns": len(conns), "types": bool(ctypes),
            "lives": [[mi, [[int(u.mtimes[0]), int(u.mtimes[-1])] for u in sorted(m.units, key=lambda x: x.mtimes[0] if x.mtimes else 0)
                            if u.mtimes]] for mi, m in enumerate(members)]}


# --------------------------------------------------------------------------- GC logs
# JDK 9+ unified logging (-Xlog:gc*:file=...:time,uptime,...):
#   [2026-10-01T06:23:05.509+0000][882.765s] GC(53) Pause Young (Concurrent Start) (G1 Humongous Allocation) 9586M->9526M(12288M) 19.075ms
# JDK 8 (-XX:+PrintGCDetails -XX:+PrintGCDateStamps):
#   2026-10-01T06:23:05.509+0000: 882.765: [GC pause (G1 Humongous Allocation) (young) (initial-mark), 0.0190750 secs]
GC_UNI_RE = re.compile(r"^((?:\[[^\]\n]*\])+)\s?(.*)$")
GC_ISO_RE = re.compile(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:[.,](\d{1,9}))?(Z|[+-]\d\d:?\d\d)?$")
GC_UPTIME_RE = re.compile(r"^(\d+(?:[.,]\d+)?)(s|ms)$")
GC_EVENT_RE = re.compile(r"^GC\((\d+)\) (.*)$")
GC_PAUSE_RE = re.compile(r"^(Pause [A-Za-z ]+?)((?: \((?:[^()]|\([^()]*\))*\))*)(?: (\d+(?:\.\d+)?)([BKMGT])->(\d+(?:\.\d+)?)([BKMGT])"
                         r"\((\d+(?:\.\d+)?)([BKMGT])\))? (\d+(?:\.\d+)?)ms$")
GC_PAREN_RE = re.compile(r"\(((?:[^()]|\([^()]*\))*)\)")
GC_HREG_RE = re.compile(r"^Humongous regions: (\d+)->(\d+)")
GC_REGION_RE = re.compile(r"[Hh]eap [Rr]egion [Ss]ize: (\d+)([KMG])")
GC8_RE = re.compile(r"(?:(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[.,]\d+[+-]\d{4}): )?(?<![\d.,])(\d+[.,]\d+): \[(GC pause|GC remark|GC cleanup|Full GC|GC)\b(.*)$")
GC8_SECS_RE = re.compile(r", (\d+[.,]\d+) secs\]")
GC8_HEAP_RE = re.compile(r"(\d+(?:\.\d+)?)([BKMG])->(\d+(?:\.\d+)?)([BKMG])\((\d+(?:\.\d+)?)([BKMG])\)")
GC8_HEAPLINE_RE = re.compile(r"Heap: (\d+(?:\.\d+)?)([BKMG])\(\d+(?:\.\d+)?[BKMG]\)->(\d+(?:\.\d+)?)([BKMG])\((\d+(?:\.\d+)?)([BKMG])\)")
GC_DUR_TAIL_RE = re.compile(r" \d+(?:[.,]\d+)?ms$")
GC_UNIT_MB = {"B": 1.0 / 1048576, "K": 1.0 / 1024, "M": 1.0, "G": 1024.0, "T": 1048576.0}
GC_TOSPACE = 1                      # flags of a pause: evacuation failure (to-space exhausted)
GC_UNDO = 2                         # a concurrent start whose cycle was undone: the humongous objects were already gone
GC_MARK = 4                         # a concurrent start that began a concurrent mark cycle


def gc_mb(v, unit):
    return round(float(v) * GC_UNIT_MB[unit], 1)


def sniff_gc(path, max_lines=400):
    """'uni' (JDK 9+ unified logging) or '8' (JDK 8 -XX:+PrintGCDetails) when the file is a GC log, else None."""
    uni = old = 0
    try:
        with open_text(path) as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                m = GC_UNI_RE.match(line)
                if m and (GC_EVENT_RE.match(m.group(2)) or m.group(2).startswith("Using ")):
                    uni += 1
                elif GC8_RE.search(line):
                    old += 1
                if uni >= 2 or old >= 2:
                    break
    except (OSError, EOFError, UnicodeDecodeError):
        return None
    return "uni" if uni >= 2 else "8" if old >= 2 else None


class GcFile(object):
    """One GC log file: its pauses, the collector, the JVM runs (start = wall time - uptime) and the member it belongs to."""

    def __init__(self, lf, fmt):
        self.lf, self.fmt = lf, fmt
        self.events = []            # (end time, jvm key, gc id, kind, cause, before MB, after MB, heap MB, pause ms, flags, humongous regions)
        self.collector = ""
        self.region_mb = None
        self.jvm_starts = []        # display times of the JVM starts seen
        self.first = self.last = None
        self.member = None
        self.how = ""
        self.no_time = 0
        self.drop = False
        self.up_runs = []           # pauses with the JVM uptime only: [{first, last, events, fresh}], one per JVM run
        self.placed = []            # how each of those runs was placed in time: [(pauses, how, JVM start)]
        self.unplaced = 0           # pauses of runs that could not be placed
        self.lane = None            # GC logs alone with the uptime only: the line (JVM) of this file

    def jvm_key(self, start):
        """The run a line belongs to: its JVM start, to the nearest 10 s (uptime and wall time round a little)."""
        k = int(round(start / 10000.0))
        if not self.jvm_starts or abs(self.jvm_starts[-1] - start) > 30000:
            self.jvm_starts.append(start)
        return k


def gc_disp(text, ctx):
    """Display millis of an ISO date-time with offset (the GC log's 'time' decoration)."""
    m = GC_ISO_RE.match(text)
    if not m:
        return None
    y, mo, d, hh, mi, ss = (int(x) for x in m.groups()[:6])
    ms = int((m.group(7) or "0")[:3].ljust(3, "0"))
    local = calendar.timegm((y, mo, d, hh, mi, ss, 0, 0, 0)) * 1000 + ms
    off = m.group(8)
    if not ctx.use_epoch:
        return local                    # as printed, like the diagnostics without epoch
    if off and off != "Z":
        sign = -1 if off[0] == "-" else 1
        o = off[1:].replace(":", "")
        local -= sign * (int(o[:2]) * 60 + int(o[2:4])) * 60000
    return local + ctx.offset


def parse_gc_file(gf, ctx):
    """Pauses of a GC log; a To-space exhausted line and the humongous region counts go with the pause of the same
    GC id that follows them. Lines with the JVM uptime only (no date-time decoration) are kept per JVM run, to be
    placed in time later with the JVM start of their member (anchor_gc)."""
    pending = {}
    cstart = {}                         # GC id of the last concurrent start pause -> (event list, index)
    run = None
    last8 = None

    def add(t, up, gid, kind, cause, before, after, heap, pause, fl, hr):
        """A pause: in gf.events when its time is known, else in the current uptime-only run. (list, index) or None."""
        if t is not None:
            j = gc_add(gf, ctx, t, run, gid, kind, cause, before, after, heap, pause, fl, hr)
            return None if j is None else (gf.events, j)
        r = gf.up_runs[-1]
        r["events"].append((up, None, gid, kind, cause, before, after, heap, pause, fl, hr))
        return r["events"], len(r["events"]) - 1

    def up_run(up, fresh=False):
        """The uptime-only run a line belongs to: a new one when the uptime goes back (a JVM restart)."""
        r = gf.up_runs[-1] if gf.up_runs else None
        if r is None or up < r["last"] - 1000 or (fresh and r["events"]):
            r = {"first": up, "last": up, "events": [], "fresh": fresh or up < 120000}
            gf.up_runs.append(r)
        r["last"] = max(r["last"], up)
        return r
    try:
        with open_text(gf.lf.path) as f:
            for line in f:
                if gf.fmt == "uni":
                    m = GC_UNI_RE.match(line)
                    if not m:
                        continue
                    decos = m.group(1)[1:-1].split("][")
                    msg = m.group(2).strip()
                    t = up = None
                    for d in decos:
                        d = d.strip()
                        if t is None and len(d) >= 19 and d[4:5] == "-" and d[10:11] == "T":
                            t = gc_disp(d, ctx)
                        elif up is None:
                            u = GC_UPTIME_RE.match(d)
                            if u:
                                up = float(u.group(1).replace(",", ".")) * (1000.0 if u.group(2) == "s" else 1.0)
                            elif d.endswith("ms") and d[:-2].isdigit() and len(d) > 14:
                                t = int(d[:-2]) + (ctx.offset if ctx.use_epoch else 0)
                    if msg.startswith("Using "):
                        gf.collector = msg[6:].strip()
                        if t is None and up is not None:
                            up_run(up, fresh=True)      # the first line of a JVM run
                        continue
                    r = GC_REGION_RE.search(msg)
                    if r:
                        gf.region_mb = gc_mb(r.group(1), r.group(2))
                    e = GC_EVENT_RE.match(msg)
                    if not e:
                        continue
                    if t is None:
                        if up is None:
                            gf.no_time += 1
                            continue
                        up_run(up)
                    elif up is not None:
                        run = gf.jvm_key(t - up)
                    gid, body = int(e.group(1)), e.group(2)
                    if body.startswith("To-space exhausted"):
                        pending.setdefault(gid, [0, None])[0] |= GC_TOSPACE
                        continue
                    h = GC_HREG_RE.match(body)
                    if h:
                        pending.setdefault(gid, [0, None])[1] = (int(h.group(1)), int(h.group(2)))
                        continue
                    if body.startswith(("Concurrent Undo Cycle", "Concurrent Mark Cycle")):
                        # the cycle's first line (no duration) follows the pause that started it, GC id - 1
                        ref = None if GC_DUR_TAIL_RE.search(body) else cstart.pop(gid - 1, None)
                        if ref is not None:
                            lst, j = ref
                            ev = lst[j]
                            lst[j] = ev[:9] + (ev[9] | (GC_UNDO if "Undo" in body else GC_MARK),) + ev[10:]
                        continue
                    p = GC_PAUSE_RE.match(body)
                    if not p:
                        continue
                    parens = GC_PAREN_RE.findall(p.group(2) or "")
                    kind = p.group(1).strip() + "".join(" (%s)" % x for x in parens if not gc_is_cause(x) and not x.startswith("Evacuation Failure"))
                    cause = next((x for x in parens if gc_is_cause(x) and not x.startswith("Evacuation Failure")), "")
                    before = gc_mb(p.group(3), p.group(4)) if p.group(3) else None
                    after = gc_mb(p.group(5), p.group(6)) if p.group(5) else None
                    heap = gc_mb(p.group(7), p.group(8)) if p.group(7) else None
                    fl, hr = pending.pop(gid, (0, None))
                    if any(x.startswith("Evacuation Failure") for x in parens):
                        fl |= GC_TOSPACE                # JDK 21+: (Evacuation Failure[: Allocation / Pinned]) on the pause line
                    ref = add(t, up, gid, kind, cause, before, after, heap, float(p.group(9)), fl, hr)
                    if ref is not None and "Concurrent Start" in kind:
                        cstart.clear()
                        cstart[gid] = ref
                else:
                    m = GC8_RE.search(line)
                    if m:
                        t = gc_disp(m.group(1).replace(",", "."), ctx) if m.group(1) else None
                        up = float(m.group(2).replace(",", ".")) * 1000.0
                        if t is not None:
                            run = gf.jvm_key(t - up)
                        elif m.group(1):
                            continue
                        what, rest = m.group(3), m.group(4)
                        if rest.lstrip().startswith("concurrent"):
                            continue                    # [GC concurrent-mark-end, 0.0012 secs]: not a pause
                        if t is None:
                            up_run(up)
                        parens = GC_PAREN_RE.findall(GC8_HEAP_RE.sub("", rest.split(",")[0]))   # not the (4096M) of a heap size
                        cause = next((x for x in parens if gc_is_cause(x)), "")
                        flags = [x for x in parens if not gc_is_cause(x)]
                        fl = GC_TOSPACE if any("to-space" in x for x in flags) else 0
                        if what == "Full GC":
                            kind = "Pause Full"
                        elif what == "GC remark":
                            kind = "Pause Remark"
                        elif what == "GC cleanup":
                            kind = "Pause Cleanup"
                        elif "mixed" in flags:
                            kind = "Pause Young (Mixed)"
                        elif "initial-mark" in flags:
                            kind = "Pause Young (Concurrent Start)"
                        else:
                            kind = "Pause Young"
                        secs = GC8_SECS_RE.findall(rest)            # the pause's own time is the last one (remark lists its phases first)
                        hm = GC8_HEAP_RE.findall(rest)
                        before = after = heap = None
                        if hm:
                            x = hm[-1]                  # the whole heap: the last before->after(total) of the line
                            before, after, heap = gc_mb(x[0], x[1]), gc_mb(x[2], x[3]), gc_mb(x[4], x[5])
                        pause = float(secs[-1].replace(",", ".")) * 1000.0 if secs else None
                        if pause is None:
                            continue
                        last8 = add(t, up, None, kind, cause, before, after, heap, pause, fl, None)
                    elif last8 is not None and "Heap:" in line:
                        h = GC8_HEAPLINE_RE.search(line)
                        if h:                           # the details of the pause just read (G1 with PrintGCDetails)
                            lst, j = last8
                            ev = lst[j]
                            lst[j] = ev[:5] + (gc_mb(h.group(1), h.group(2)), gc_mb(h.group(3), h.group(4)),
                                               gc_mb(h.group(5), h.group(6))) + ev[8:]
                            last8 = None
    except (OSError, EOFError) as e:
        warn("cannot read %s: %s" % (gf.lf.path, e))
    gf.up_runs = [r for r in gf.up_runs if r["events"]]


def gc_is_cause(text):
    """The reason in Pause Young (Normal) (G1 Humongous Allocation): not a phase of the pause."""
    return text not in ("Normal", "Concurrent Start", "Prepare Mixed", "Mixed", "young", "mixed", "initial-mark",
                        "to-space exhausted", "to-space overflow") and not text.startswith(("Concurrent", "young"))


def gc_add(gf, ctx, t, run, gid, kind, cause, before, after, heap, pause, fl, hr):
    if not ctx.inside(t):
        return None
    gf.events.append((t, run, gid, kind, cause, before, after, heap, pause, fl, hr))
    gf.first = t if gf.first is None else min(gf.first, t)
    gf.last = t if gf.last is None else max(gf.last, t)
    return len(gf.events) - 1


def gc_dist(a, b):
    """Folders apart: up from a to the closest common folder, then down to b."""
    pa = [] if a == "." else a.split("/")
    pb = [] if b == "." else b.split("/")
    c = 0
    while c < len(pa) and c < len(pb) and pa[c] == pb[c]:
        c += 1
    return (len(pa) - c) + (len(pb) - c)


def gc_name_score(label, text):
    """How well a member's address matches a file or folder name: 2 for its host (or its first label: uklpapscl101a
    of uklpapscl101a.example.com), 1 more for its port."""
    host = label.split(" · ")[0].rsplit(":", 1)[0].strip("[]").lower()
    port = label.split(" · ")[0].rsplit(":", 1)[-1] if ":" in label else ""
    short = None if re.match(r"^[\d.]+$", host) else host.split(".")[0]
    return (2 if host and (host in text or (short and re.search(r"(?:^|[^a-z0-9])%s(?:$|[^a-z0-9])" % re.escape(short), text))) else 0) + \
        (1 if port.isdigit() and re.search(r"[_:.-]%s(?:$|[^0-9])" % re.escape(port), text) else 0)


def gc_pick(gf, owners):
    """The owner of a GC log among owners {key: [folders, JVM start times, label]}: the one whose files are in the
    same folder; else the one whose host is in the GC log's file name, then in its folder names; else the one in the
    nearest folder; on a tie there, the one with a lifetime that started with that JVM (the GC log's wall time minus
    its uptime). Returns (key, how) or (None, "")."""
    dist = dict((k, min(gc_dist(gf.lf.reldir, d) for d in o[0])) for k, o in owners.items() if o[0])
    if not dist:
        return None, ""
    best = min(dist.values())
    near = sorted((k for k, d in dist.items() if d == best), key=str)
    if best == 0 and len(near) == 1:
        return near[0], "same folder"
    for text, how in ((gf.lf.name.lower(), "file name"), (gf.lf.reldir.lower(), "folder name")):
        hit = sorted(((gc_name_score(o[2], text), str(k), k) for k, o in owners.items()), reverse=True)
        if hit and hit[0][0] >= 2 and (len(hit) == 1 or hit[0][0] > hit[1][0]):
            return hit[0][2], how
    if len(near) == 1:
        return near[0], "same folder" if best == 0 else "nearest folder"
    if gf.jvm_starts:
        score = []
        for k in near:
            st = owners[k][1]
            if st:
                score.append((min(min(abs(js - x) for x in st) for js in gf.jvm_starts), str(k), k))
        score.sort()
        if score and score[0][0] < 600000 and (len(score) == 1 or score[1][0] - score[0][0] > 60000):
            return score[0][2], "JVM start time"
    return None, ""


def assign_gc(gcfiles, members, lms, ctx):
    """The member of each GC log (gc_pick over the members' diagnostics and member log folders); the others get a
    lane of their own."""
    owners = {}
    for i, m in enumerate(members):
        o = owners.setdefault(i, [set(), [], m.label])
        for u in m.units:
            o[0].update(u.dirs)
            for f in u.files:
                if f.start_ms:
                    o[1].append(f.start_ms + (ctx.offset if ctx.use_epoch else 0))
    for lm in lms:
        owners.setdefault(lm.index, [set(), [], lm.label])[0].update(f.reldir for f in lm.files)
    for gf in gcfiles:
        k, how = gc_pick(gf, owners)
        if k is not None:
            gf.member, gf.how = k, how


def unit_series(u, metric):
    """[(display time, value)] of a member-level metric of one lifetime (runtime.uptime, gc.minorCount...)."""
    pat = re.compile(r"(?:^|,)metric=%s(?:,|$)" % re.escape(metric))
    for attrs, ms in u.series.items():
        if not pat.search(attrs) or any(kv.split("=", 1)[0] not in ("unit", "metric") for kv in attrs.split(",")):
            continue
        vals = ms.vals if ms.vals is not None else [ms.cval] * ms.count
        return [(u.mtimes[ms.first + i], v) for i, v in enumerate(vals) if v == v and ms.first + i < len(u.mtimes)]
    return []


def gc_count_fit(run, start, counts):
    """How well the young pauses of an uptime-only run line up with the member's gc.minorCount (young collections
    since the JVM started) when the JVM started at `start`: the share of the dumps in the run's span where the count
    minus the young pauses logged so far stays the same, give or take one; and that difference (0 when the log has
    every pause since the start); and the number of dumps. None when fewer than 3 dumps fall in the run."""
    young = sorted(ev[0] for ev in run["events"] if ev[3].startswith("Pause Young"))
    diffs = []
    for t, c in counts:
        up = t - start
        if run["first"] - 1000 <= up <= run["last"] + 1000:
            diffs.append(int(round(c)) - bisect.bisect_right(young, up))
    if len(diffs) < 3:
        return None
    med = sorted(diffs)[len(diffs) // 2]
    return sum(1 for x in diffs if abs(x - med) <= 1) / float(len(diffs)), med, len(diffs)


def gc_count_start(run, counts, approx, before=0):
    """The JVM start that makes a run's young pauses match gc.minorCount at every dump near the run (a dump showing c
    comes after the run's (c - before)-th young pause and before the next one; `before`: the young GCs before the
    file, 0 for a run that starts with the JVM), near `approx` (the member's start); None when no start does."""
    young = sorted(ev[0] for ev in run["events"] if ev[3].startswith("Pause Young"))
    if not young:
        return None
    lo, hi, n = float("-inf"), float("inf"), 0
    for t, c in counts:
        if not approx + run["first"] - 3600000 <= t <= approx + run["last"] + 60000:
            continue
        k = min(len(young), max(0, int(round(c)) - before))
        if k >= 1:
            hi = min(hi, t - young[k - 1])
        if k < len(young):
            lo = max(lo, t - young[k])
        n += 0 < k < len(young)
    if n >= 3 and lo < hi and hi - lo < 600000 and approx - 3600000 <= (lo + hi) / 2 <= approx + 60000:
        return (lo + hi) / 2.0
    return None


def gc_lifetimes(m, ctx):
    """The JVM starts of a member's lifetimes, as candidates for its uptime-only GC logs: [(start, cover start,
    cover end, gc.minorCount points, how)] by start. The start is the dump time minus runtime.uptime; without that
    metric, the lifetime's start in the file names (when the member started, a little after its JVM)."""
    out = []
    for u in m.units:
        ups = [t - v for t, v in unit_series(u, "runtime.uptime") if v > 0]
        counts = unit_series(u, "gc.minorCount")
        c0 = u.cover[0][0] if u.cover else None
        c1 = u.cover[-1][1] if u.cover else None
        if ups:
            out.append((statistics.median(ups), c0, c1, counts, "JVM uptime in the diagnostics"))
            continue
        fs = [f.start_ms for f in u.files if f.start_ms]
        if fs:
            out.append((min(fs) + (ctx.offset if ctx.use_epoch else 0), c0, c1, counts, "approx"))
        elif c0 is not None:
            out.append((c0, c0, c1, counts, "approx"))
    return sorted(out, key=lambda x: x[0])


def gc_score(run, cands, j):
    """Whether an uptime-only run can be the JVM of the j-th lifetime of its member, and how sure: (rank, start, how)
    or None. Where the diagnostics have the GC counts over the run, they decide: they must line up with its young
    pauses (all of them since the start for a run that starts with the JVM). Without them, the run must overlap the
    lifetime's diagnostics. A JVM can't go on past the start of the next lifetime."""
    start, c0, c1, counts, how = cands[j]
    if how == "approx" and counts:              # no runtime.uptime: the start that lines up with the GC counts
        refined = None
        if run["fresh"]:
            refined = gc_count_start(run, counts, start)
        else:                                   # the young GCs before the file: about the median difference
            fit0 = gc_count_fit(run, start, counts)
            for k in sorted(range(fit0[1] - 3, fit0[1] + 4), key=lambda x: abs(x - fit0[1])) if fit0 else ():
                refined = gc_count_start(run, counts, start, k)
                if refined is not None:
                    break
        if refined is not None:
            start, how = refined, "GC counts in the diagnostics"
    a, b = start + run["first"], start + run["last"]
    if j + 1 < len(cands) and b > cands[j + 1][0] + 120000:
        return None
    fit = gc_count_fit(run, start, counts) if counts else None
    if fit is not None:
        if fit[0] < 0.9 or (run["fresh"] and abs(fit[1]) > 1):
            return None
        return (2, fit[0], fit[2], -abs(fit[1])), start, how
    ov = min(b, c1) - max(a, c0) if c0 is not None else -1
    return ((1, 0, ov, 0), start, how) if ov > 0 else None


def gc_same_jvm(r1, r2):
    """Two uptime-only runs from the same JVM (copies of a file, or files that overlap): their common GCs have the
    same uptime. GC ids start again from 0 at each JVM start, so another JVM's GC(7) has another uptime."""
    a = dict(((ev[2], ev[3]), ev[0]) for ev in r1["events"] if ev[2] is not None)
    common = [(a[(ev[2], ev[3])], ev[0]) for ev in r2["events"] if (ev[2], ev[3]) in a]
    return bool(common) and all(abs(x - y) <= 1 for x, y in common)


def anchor_gc(gcfiles, members, ctx):
    """GC logs with the JVM uptime only on their lines: each JVM run placed in time with the JVM start of one of
    its member's lifetimes. Runs go to lifetimes surest first, and two runs of one lifetime can't overlap in uptime
    unless they are the same JVM's (a restarted JVM logs from 0 again: gc.log and gc.log.1 can both start at 0 and
    can't both be one lifetime's). A file that matched no member goes to the one member whose GC counts match it."""
    cands = dict((i, gc_lifetimes(m, ctx)) for i, m in enumerate(members))
    left = {}
    for gf in gcfiles:
        if gf.up_runs and gf.member is None:    # the member whose GC counts line up with the pauses, if only one does
            fits = [i for i, cs in cands.items()
                    if any((gc_score(r, cs, j) or ((0,),))[0][0] == 2 for r in gf.up_runs for j in range(len(cs)))]
            if len(fits) == 1:
                gf.member, gf.how = fits[0], "GC counts"
    groups = {}
    for gf in gcfiles:
        for r in gf.up_runs:
            mi = gf.member if gf.member is not None and gf.member < len(members) else None
            groups.setdefault(mi, []).append((gf, r))
    for mi, runs in groups.items():
        cs = cands.get(mi) or []
        opts = []
        for k, (_gf, r) in enumerate(runs):
            for j in range(len(cs)):
                x = gc_score(r, cs, j)
                if x is not None:
                    opts.append((x[0], -k, k, j, x[1], x[2]))
        opts.sort(reverse=True)
        done, taken = {}, {}
        for _rank, _nk, k, j, start, how in opts:
            r = runs[k][1]
            if k in done:
                continue
            clash = [q for q in taken.get(j, []) if r["first"] <= runs[q][1]["last"] and runs[q][1]["first"] <= r["last"]]
            if clash and not all(gc_same_jvm(runs[q][1], r) for q in clash):
                continue
            if clash:
                start, how = done[clash[0]]
            done[k] = (start, how)
            taken.setdefault(j, []).append(k)
        for k, (gf, r) in enumerate(runs):
            if k not in done:
                left.setdefault(id(gf), []).append(r)
                continue
            start, how = done[k]
            key = gf.jvm_key(start)
            n = 0
            for ev in r["events"]:
                if gc_add(gf, ctx, start + ev[0], key, *ev[2:]) is not None:
                    n += 1
            gf.placed.append((n, how, start))
    for gf in gcfiles:
        gf.up_runs = left.get(id(gf), [])


def gc_follows(a, b):
    """Uptime-only run b is the next file of run a's JVM (rotated files): b doesn't start with the JVM, starts
    where a ended, and its first GC id follows a's last one (GC ids go on across a JVM's files, from 0 at its start)."""
    ga = [ev[2] for ev in a["events"] if ev[2] is not None]
    gb = [ev[2] for ev in b["events"] if ev[2] is not None]
    return bool(ga and gb) and not b["fresh"] and b["first"] >= a["last"] - 1000 and max(ga) <= min(gb) <= max(ga) + 3


def place_uptime(gcfiles, ctx):
    """GC logs alone with the JVM uptime only on their lines: nothing gives the JVM start, so the time axis is the
    uptime (0 is each JVM's start) and each JVM has a line of its own. A log's rotated files are one JVM when they
    follow each other (gc_follows) or are copies; a file that starts from 0 again is another JVM."""
    groups = {}
    for gf in gcfiles:
        for r in gf.up_runs:
            groups.setdefault((gf.lf.reldir, gc_stem(gf.lf.name)), []).append((gf, r))
    nchain = 0
    for key in sorted(groups):
        chains = []
        for gf, r in sorted(groups[key], key=lambda x: (x[1]["first"], x[0].lf.name)):
            for ch in chains:
                if any(gc_same_jvm(r0, r) for _g, r0 in ch) or gc_follows(ch[-1][1], r):
                    ch.append((gf, r))
                    break
            else:
                chains.append([(gf, r)])
        for ch in chains:
            nchain += 1
            names = []
            for gf, _r in ch:
                if gf.lf.name not in names:
                    names.append(gf.lf.name)
            label = "GC log " + (key[0] + "/" if key[0] != "." else "") + names[0] + \
                (" + %d rotated file%s" % (len(names) - 1, "" if len(names) == 2 else "s") if len(names) > 1 else "")
            for gf, r in ch:
                gf.lane = gf.lane or label
                gf.jvm_starts.append(0)
                n = 0
                for ev in r["events"]:
                    if gc_add(gf, ctx, ev[0], -nchain, *ev[2:]) is not None:
                        n += 1
                gf.placed.append((n, "uptime axis", None))
                r["events"] = []
    for gf in gcfiles:
        gf.up_runs = [r for r in gf.up_runs if r["events"]]


def gc_addr_key(host, port):
    h = norm_host(host)
    return ("addr", "127.0.0.1" if h in ("localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1") else h, port)


def assign_gc_selected(gcfiles, members, lms, files, logfiles, ctx):
    """With --member: the owner of each GC log among every member found (those left out too); the GC logs of the
    members kept go to them, the others are dropped."""
    owners = {}

    def add(key, label, d, start):
        o = owners.setdefault(key, [set(), [], label])
        o[0].add(d)
        if start:
            o[1].append(start + (ctx.offset if ctx.use_epoch else 0))
    for f in files:
        if f.host:
            add(gc_addr_key(f.host, f.port), member_label(f.host, f.port), f.reldir, f.start_ms)
        else:
            add(("dir", f.reldir), f.reldir, f.reldir, None)
    for lf in logfiles:
        if lf.log_addr is not None:
            add(gc_addr_key(*lf.log_addr), member_label(*lf.log_addr), lf.reldir, None)
        else:
            add(("dir", lf.reldir), lf.reldir, lf.reldir, None)
    keep = {}
    for i, m in enumerate(members):
        if m.key[0] == "addr":
            keep.setdefault(gc_addr_key(m.key[1], m.key[2]), i)
        for d in m.dirs:
            keep.setdefault(("dir", d), i)
    for lm in lms:
        if lm.key[0] != "file":
            keep.setdefault(gc_addr_key(lm.key[0], lm.key[1]), lm.index)
    for gf in gcfiles:
        k, how = gc_pick(gf, owners)
        if k in keep:
            gf.member, gf.how = keep[k], how
        else:
            gf.drop = True


GC_ROTATED_RE = re.compile(r"(\.log)(?:\.\d+|\.current|\.gz|[.\-_]\d{4}-?\d\d-?\d\d[\dT:._\-]*)+$")


def gc_stem(name):
    """The name of a GC log without what rotation adds: gc.log.0.current, gc.log.2.gz, gc.log.2026-10-01-06-08."""
    return GC_ROTATED_RE.sub(r"\1", name)


GC_PLACED = {"JVM uptime in the diagnostics": "uptime + JVM start (runtime.uptime)",
             "GC counts in the diagnostics": "uptime + JVM start (GC counts)", "approx": "uptime + member start (approximate)",
             "uptime axis": "JVM uptime (no date-time in the log)"}


def gc_time_source(gf):
    """Where the times of a GC log's pauses come from: its date-time decorations, or its uptime placed with the JVM
    start of its member (runtime.uptime in the diagnostics, the GC counts, or roughly the member's start)."""
    out = []
    placed = sum(n for n, _h, _s in gf.placed)
    if len(gf.events) > placed:
        out.append("date-time in the log")
    for h in sorted(set(h for _n, h, _s in gf.placed)):
        out.append(GC_PLACED.get(h, h))
    if gf.unplaced:
        out.append("uptime only: %s pause%s not placed in time" % (format(gf.unplaced, ","), "" if gf.unplaced == 1 else "s"))
    if gf.no_time:
        out.append("%d line%s with no time" % (gf.no_time, "" if gf.no_time == 1 else "s"))
    return "; ".join(out)


def export_gc(gcfiles, nmembers):
    """Per member, every pause once (rotated files and copies overlap), in time order, as columns. The GC logs of no
    member get a lane per folder and name (their rotated files together)."""
    kinds, kidx, causes, cidx = [], {}, [], {}
    by = {}
    extra = []
    lanes = {}
    for gf in gcfiles:
        mi = gf.member
        if mi is None and not gf.events:
            gf.how = "no member found"
            continue                            # nothing to chart (uptime only, not placed): listed, without a lane
        if mi is None:
            key = ("lane", gf.lane) if gf.lane else (gf.lf.reldir, gc_stem(gf.lf.name))
            if key not in lanes:
                lanes[key] = nmembers + len(extra)
                extra.append(gf.lane or "GC log " + (key[0] + "/" if key[0] != "." else "") + key[1])
            mi = gf.member = lanes[key]
            gf.how = "no member found"
        by.setdefault(mi, {})
        for ev in gf.events:
            key = (ev[1], ev[2], ev[3]) if gf.fmt == "uni" and ev[1] is not None else (ev[0], ev[3])
            by[mi][key] = ev
    out = []
    for mi in sorted(by):
        evs = sorted(by[mi].values(), key=lambda e: e[0])
        if not evs:
            continue
        col = {"m": mi, "t": [], "i": [], "k": [], "c": [], "b": [], "a": [], "h": [], "p": [], "f": []}
        prev = 0
        for j, ev in enumerate(evs):
            t, _run, gid, kind, cause, before, after, heap, pause, fl, hr = ev
            if kind not in kidx:
                kidx[kind] = len(kinds)
                kinds.append(kind)
            if cause not in cidx:
                cidx[cause] = len(causes)
                causes.append(cause)
            col["t"].append(int(t) - prev)
            prev = int(t)
            col["i"].append(-1 if gid is None else gid)
            col["k"].append(kidx[kind])
            col["c"].append(cidx[cause])
            col["b"].append(before)
            col["a"].append(after)
            col["h"].append(heap)
            col["p"].append(int(round(pause * 1000)))
            if fl or hr:
                col["f"].append([j, fl, hr[0] if hr else None, hr[1] if hr else None])
        out.append(col)
    files = [[-1 if gf.member is None else gf.member, (gf.lf.reldir + "/" if gf.lf.reldir != "." else "") + gf.lf.name, gf.how,
              None if gf.first is None else int(round(gf.first)), None if gf.last is None else int(round(gf.last)),
              len(gf.events), gf.collector, gf.region_mb, gf.fmt, len(gf.jvm_starts), gf.lf.size, gc_time_source(gf),
              gf.unplaced, gf.no_time] for gf in gcfiles]
    return {"kinds": kinds, "causes": causes, "mem": out, "files": files, "extra": extra}


# --------------------------------------------------------------------------- member logs
# The members' own logs (hazelcast.log, console output, ...) found with the diagnostics: java.util.logging (two
# lines per record), log4j / logback and the like (a date at the start of each record). Lines without a date
# continue the record before them (stack traces, member lists).
MONTHS = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
JUL_HEAD_RE = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2}), (\d{4}) (\d{1,2}):(\d{2}):(\d{2}) "
                         r"(AM|PM) (\S+)(?: \S+)?\s*$")
JUL_LEVEL_RE = re.compile(r"^(SEVERE|WARNING|INFO|CONFIG|FINE|FINER|FINEST): ?(.*)$")
ISO_HEAD_RE = re.compile(r"^\[?(\d{4})[-/](\d{2})[-/](\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:[.,](\d{1,9}))?"
                         r"(Z|[+-]\d{2}:?\d{2})?\]?[ \t]+(.*)$")
DMY_HEAD_RE = re.compile(r"^\[?(\d{2})[-/.](\d{2})[-/.](\d{4})[ T](\d{2}):(\d{2}):(\d{2})(?:[.,](\d{1,9}))?\]?[ \t]+(.*)$")
# the same date-times after something else (a level, an application name, a container log prefix): searched in the
# first 120 characters; "02 Nov 2026 01:30:06,766" and "02-Nov-2026 01:30:06.766"; the time of day alone, the date
# being in the file name (20261102-app.log, app.2026-11-02.log)
ISO_ANY_RE = re.compile(r"(\d{4})[-/](\d{2})[-/](\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:[.,](\d{1,9}))?(Z|[+-]\d{2}:?\d{2})?(?![\d:])")
MON_ANY_RE = re.compile(r"(?<!\d)(\d{1,2})[ -](Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[ -](\d{4})[ T:](\d{2}):(\d{2}):(\d{2})"
                        r"(?:[.,](\d{1,9}))?(?![\d:])")
TIME_HEAD_RE = re.compile(r"^\[?(\d{2}):(\d{2}):(\d{2})(?:[.,](\d{1,9}))?\]?[ \t]+(.*)$")
FNAME_DATE_RE = re.compile(r"(?<!\d)(20\d\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
ANSI_BARE_RE = re.compile(r"\[(?:\d{1,3}(?:;\d{1,3})*)m(.*?)\[0?m")      # colours whose escape character was lost: [31mERROR[m
LOG_SNIFF_MAX = 512 * 1024 * 1024       # how much of a file is read to find its first Hazelcast line
SNIFF_WHY = {}                          # path -> why a file is not a member log (for the console)


def clean_log_line(line):
    """A log line without its terminal colours (console output with escape codes)."""
    if "\x1b" in line:
        line = ANSI_RE.sub("", line)
    if "[m" in line or "[0m" in line:
        line = ANSI_BARE_RE.sub(r"\1", line)
    return line


def fname_day(path):
    """The day in a log file's name, as display millis of midnight, or None."""
    m = FNAME_DATE_RE.search(os.path.basename(path))
    return day_ms(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def json_get(o, *keys):
    for k in keys:
        v = o.get(k)
        if v is None and "." in k:              # ECS nests them: {"log": {"level": ...}}
            a, _d, b = k.partition(".")
            sub = o.get(a)
            v = sub.get(b) if isinstance(sub, dict) else None
        if v not in (None, ""):
            return v
    return None


def json_record(line, disp_offset):
    """A JSON log line (logstash encoder, ECS, log4j2 JsonLayout...) as a record, or None."""
    try:
        o = json.loads(line)
    except ValueError:
        return None
    if not isinstance(o, dict):
        return None
    ts = json_get(o, "@timestamp", "timestamp", "time", "ts", "date", "datetime", "instant", "timeMillis")
    t = None
    if isinstance(ts, dict) and "epochSecond" in ts:
        t = int(ts["epochSecond"]) * 1000 + int(ts.get("nanoOfSecond", 0)) // 1000000
    elif isinstance(ts, (int, float)) and not isinstance(ts, bool):
        t = int(ts if ts > 1e11 else ts * 1000)
    if t is not None:
        t += disp_offset if disp_offset is not None else 0
    elif isinstance(ts, str):
        m = ISO_ANY_RE.search(ts)
        if not m:
            return None
        g = m.groups()
        t = day_ms(int(g[0]), int(g[1]), int(g[2])) + ((int(g[3]) * 60 + int(g[4])) * 60 + int(g[5])) * 1000 + frac_ms(g[6])
        z = zone_ms(g[7])
        if z is not None and disp_offset is not None:
            t = t - z + disp_offset
    else:
        return None
    level = str(json_get(o, "level", "log.level", "severity", "levelname", "loglevel") or "INFO").upper()
    logger = str(json_get(o, "logger_name", "loggerName", "logger", "log.logger", "name") or "")
    thread = str(json_get(o, "thread_name", "thread", "threadName", "process.thread.name") or "")
    msg = json_get(o, "message", "msg", "log") or ""
    if not isinstance(msg, str):
        msg = json.dumps(msg)
    lines = msg.split("\n")
    st = json_get(o, "stack_trace", "error.stack_trace", "exception", "thrown", "stackTrace")
    if isinstance(st, dict):
        st = json_get(st, "extendedStackTrace", "stackTrace", "message", "name")
    extra = lines[1:] + (str(st).split("\n") if st else [])
    return [t, LOG_LEVELS.get(level, "I"), logger, thread, lines[0], extra[:MAX_EXTRA_LINES], max(0, len(extra) - MAX_EXTRA_LINES)]


def log_head(line, fmt, disp_offset):
    """(display ms, the rest of the line) of a record's first line in one of the one-line formats, or None.
    The time-of-day format gives ms since midnight (its day is added by the reader)."""
    if fmt == "isox":
        m = ISO_ANY_RE.search(line, 0, 120)
        if not m:
            return None
        g = m.groups()
        t = day_ms(int(g[0]), int(g[1]), int(g[2])) + ((int(g[3]) * 60 + int(g[4])) * 60 + int(g[5])) * 1000 + frac_ms(g[6])
        z = zone_ms(g[7])
        if z is not None and disp_offset is not None:
            t = t - z + disp_offset
    elif fmt == "mon":
        m = MON_ANY_RE.search(line, 0, 120)
        if not m:
            return None
        g = m.groups()
        t = day_ms(int(g[2]), MONTHS[g[1]], int(g[0])) + ((int(g[3]) * 60 + int(g[4])) * 60 + int(g[5])) * 1000 + frac_ms(g[6])
    elif fmt == "time":
        m = TIME_HEAD_RE.match(line)
        if not m:
            return None
        g = m.groups()
        return ((int(g[0]) * 60 + int(g[1])) * 60 + int(g[2])) * 1000 + frac_ms(g[3]), g[4]
    else:
        return None
    pre = line[:m.start()].strip(" []|-")
    return t, (pre + " " if pre else "") + line[m.end():].lstrip(" ]|")
LOG_LEVELS = {"SEVERE": "E", "FATAL": "E", "ERROR": "E", "WARNING": "W", "WARN": "W", "INFO": "I", "CONFIG": "I",
              "FINE": "D", "FINER": "D", "FINEST": "D", "DEBUG": "D", "TRACE": "D"}
REST_HZ_RE = re.compile(r"^\[\s*([A-Z]+)\s*\]\s+\[([^\]]*)\]\s+\[([^\]]*)\]:?\s?(.*)$")      # [ INFO] [thread] [c.h.x.Y]: msg
LEVEL_TOKEN_RE = re.compile(r"(?:^|[\s\[|])(SEVERE|FATAL|ERROR|WARNING|WARN|INFO|CONFIG|FINEST|FINER|FINE|DEBUG|TRACE)"
                            r"(?=[\s\]|:]|$)")
LOGGER_RE = re.compile(r"((?:[A-Za-z_$][\w$]*\.)+[A-Za-z_$][\w$]*(?:\([^)\s]*\))?)")
LOGGER_THREAD_RE = re.compile(r"^([A-Za-z_$][\w$.]*(?:\([^)\s]*\))?)\s+\[([^\]]*)\]\s*(?:-|:)\s?")     # Logger [thread] - message
HZ_LOGGER_PREFIXES = ("com.hazelcast.", "c.h.", "hazelcast.")
HZ_ADDR_RE = re.compile(r"\[([^\]\s]+)\]:(\d{1,5}) \[([^\]]*)\] \[(\d[^\]]*)\]")
HZ_PREFIX_RE = re.compile(r"^\[([^\]\s]+)\]:(\d{1,5}) \[([^\]]*)\] \[(\d[^\]]*)\] ?")
HZ_LOCAL_RE = re.compile(r"^(?:\[LOCAL\]|[\w.-]+) \[([^\]]*)\] \[(\d[^\]]*)\] ?")
EXC_RE = re.compile(r"(?<![\w$.])((?:[a-z_$][\w$]*\.)+[A-Z][\w$]*(?:Exception|Error|Throwable))(?![\w$])(?::\s?(.*))?")
NORM_RE = re.compile(r"0x[0-9a-fA-F]+|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|\d+")
OOME_RE = re.compile(r"OutOfMemoryError(?::\s*([^\n]*))?")
MEMBERS_LIST_RE = re.compile(r"Members \{size:(\d+), ver:(\d+)\}|Members \[(\d+)\] \{")
MEMBER_LINE_RE = re.compile(r"^\s*Member \[([^\]]+)\]:(\d{1,5})")
LIFECYCLE_RE = re.compile(r"^\s*\[[^\]]+\]:\d{1,5} is (STARTING|STARTED|SHUTTING_DOWN|SHUTDOWN|MERGING|MERGED|MERGE_FAILED)\b")
MAX_LOG_EVENTS = 1000               # rows kept per category and member: 1,000 to 2,000, spread over time (all are counted)
MAX_CP_ROWS = 1000                  # CP Subsystem lines kept per kind and member, the same way
BIN_STEPS = (1, 10, 60, 600, 3600, 86400)       # seconds per time bin, finest first
EXC_BINS = 5000                     # time bins per exception kind and member, and per notable kind and member
LEVEL_BINS = 20000                  # time bins of the warning / error counts per member
BIN_BUDGET = 400000                 # time bins of the exceptions and notable lines, all together
MAX_EXTRA_LINES = 200               # continuation lines kept per record
MAX_SAMPLE_LINES = 30               # lines of a stack trace kept as the sample of an exception
# notable lines, by priority: a line goes to the first category it matches
LOG_CATS = [
    ("oome", "Out of memory", 2, None),
    ("partlost", "Partition lost", 2, re.compile(r"is completely lost!|\bLOST partitions\b|Unable to assign LOST|PartitionLostEvent\{")),
    ("quorum", "Quorum / split-brain protection failed", 2,
     re.compile(r"(?i)split.brain protection exception|SplitBrainProtectionException|QuorumException|quorum\w*\s+has failed")),
    ("wan", "WAN replication problem", 2,
     re.compile(r"(?i)wan replication event queue is full|dropping (?:wan )?events|WanQueueFullException|WanReplicationException|"
                r"\bwan\b.{0,80}\b(?:failed|failure|unreachable|not reachable|refused|timed out|timeout)\b")),
    ("starvation", "Thread starvation / clock leap", 2, re.compile(r"(?i)thread starvation|clock leap")),
    ("clockjump", "System clock jump", 2, re.compile(r"(?i)clock jump|clock apparently jumped|clock-jump")),
    ("suspect", "Member suspected", 1, re.compile(r"(?i)\bsuspect(?:ing|ed)\b")),
    ("splitbrain", "Split brain / merge", 1,
     re.compile(r"(?i)split[- ]?brain(?![- ]?protection)|\bmerg(?:ing|e) (?:\[[^\]]*\] )?to\b|\bmerge (?:process|operation|instruction|tasks?)\b|"
                r"cluster for merge|looking to merge|\bMERGE_FAILED\b")),
    ("slowop", "Slow operation", 1, re.compile(r"(?i)slow operation detected")),
    ("delayed", "Delayed", 1, re.compile(r"(?i)\bdelayed\b")),
    ("connection", "Connection closed", 0,
     re.compile(r"(?i)\bconnection\b.{0,160}\b(?:closed|reset|refused|lost)\b|removing connection")),
    ("lifecycle", "Lifecycle", 0, None),
    ("membership", "Membership", 0, None),
]
CAT_INDEX = {c[0]: i for i, c in enumerate(LOG_CATS)}
CAT_REGEX = [(c[0], c[3]) for c in LOG_CATS if c[3] is not None]
# what any line can tell, Hazelcast's or not (HikariCP's "Thread starvation or clock leap detected" is the whole JVM's);
# the other kinds are about Hazelcast and come from its own lines only (an application's SQL "merge into" is no merge)
ANY_SOURCE_CATS = frozenset(("starvation", "clockjump"))
# words one of them needs (lower case): most lines have none, and skip the regular expressions
CAT_WORDS = ("lost", "wan", "split", "quorum", "starv", "leap", "clock", "suspect", "merg", "slow operation", "delayed", "connection")
WAN_LOGGER_RE = re.compile(r"(?:^|\.)Wan[A-Z]\w*$|\.wan\.")     # a warning or error of a WAN class: a WAN problem
# migrations and the partition table, as the master logs them (MigrationManagerImpl, InternalPartitionService...)
MIG_STATS_RE = re.compile(r"repartitionTime=(.+?), plannedMigrations=(\d+), completedMigrations=(\d+), remainingMigrations=(\d+), "
                          r"totalCompletedMigrations=(\d+)(?:.*?, elapsedMigrationTime=(\d+)ms)?")
MIG_DATE_RE = re.compile(r"\w{3} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2}) (\d{2}):(\d{2}):(\d{2}) (?:\S+ )?(\d{4})")
MIG_KINDS = [                           # kind, label, severity, pattern (the first that matches)
    ("lost", "Partition lost", 2, re.compile(r"is completely lost!|LOST partitions|Unable to assign LOST")),
    ("start", "Migration plan", 0, re.compile(r"Repartitioning cluster data\. Migration tasks count: (\d+)")),
    ("progress", "Progress", 0, re.compile(r"^Remaining migration tasks: \d+")),
    ("done", "All migrations completed", 0, re.compile(r"^All migration tasks have been completed")),
    ("failed", "Plan failed, aborted or interrupted", 1,
     re.compile(r"^Rebalance process was (failed|aborted)|MigrationProcessTask is interrupted")),
    ("balanced", "Balance ok, no migration", 0, re.compile(r"^Partition balance is ok")),
    ("promotion", "Promotion failed", 1, re.compile(r"Promotion (?:of \d+ partitions failed|failed|commit (?:to .{0,200} )?failed)|"
                                                  r"While promoting|stale promotion")),
    ("migfail", "Migration failed", 1,
     re.compile(r"Migration commit (?:failed|to .{0,200}failed)|Cannot (?:apply migration commit|commit )|AsyncMigrationTask failed|"
                r"^Error during Migration|Skipping migration|Marking active migration .* as FAILED|Failed to finalize migration|"
                r"Active migration could not be removed|is not migrating")),
    ("table", "Partition table", 0,
     re.compile(r"Initializing cluster partition table|Fetching partition tables from cluster|Most recent partition table is determined|"
                r"Applying the most recent of partition state|Setting cluster partition table|in partition table in \d+ partitions|"
                r"Storing snapshot of partition assignments|Identified a snapshot of left member|Partitions can't be assigned|"
                r"No member group is available|Some backup replicas are inconsistent|requested to (?:shutdown|demote) but still in partition table|"
                r"Ignoring received partition table")),
]
MIG_INDEX = {k[0]: i for i, k in enumerate(MIG_KINDS)}
# HealthMonitor: processors=8, physical.memory.total=31.0G, ..., heap.memory.used/max=75.75%, minor.gc.time=13209ms, ...
SIZE_UNITS = {"K": 1024.0, "M": 1024.0 ** 2, "G": 1024.0 ** 3, "T": 1024.0 ** 4, "P": 1024.0 ** 5}
HEALTH_COUNTERS = frozenset(("minor.gc.count", "minor.gc.time", "major.gc.count", "major.gc.time", "unknown.gc.count",
                             "unknown.gc.time", "operations.completed.count"))
# CP Subsystem lines
CP_LOGGER_RE = re.compile(r"\.(?:cp|raft)\.|\([^()]+\)$|(?:^|\.)(?:Raft\w*|CP[A-Z]\w*|\w*Raft\w*|LeaderElection\w*|PreVote\w*|"
                          r"Vote\w*Task|Append\w*Task|InstallSnapshot\w*|LeaderFailure\w*|MembershipChange\w*)$")
CP_TEXT_RE = re.compile(r"hz:core:raft|CPGroupId|RaftEndpoint|CP Group Members|CP Subsystem|CPMember|raftop|Raft")
CP_GROUP_SUFFIX_RE = re.compile(r"\(([^()]+)\)$")
CPGROUPID_RE = re.compile(r"CPGroupId\{name='([^']+)'")
CP_SNAP_RE = re.compile(r"(?:CP|Raft) Group Members \{groupId: ([^(]+)\((\d+)\), size:(\d+), term:(\d+), logIndex:(\d+)\}")
CP_MEMBER_RE = re.compile(r"CPMemberInfo\{uuid=([0-9a-fA-F-]+), address=\[([^\]]+)\]:(\d{1,5})[^}]*\}(?: - ([A-Z_]+))?( this)?")
RAFT_EP_RE = re.compile(r"RaftEndpoint\{uuid='([0-9a-fA-F-]+)'")
CP_KINDS = [
    ("leader", re.compile(r"We are the LEADER!")),
    ("demote", re.compile(r"Demoting to (\w+) from current role: \w+, term: \d+ to new term: (\d+)"
                          r"(?: and leader: RaftEndpoint\{uuid='([^']+)')?")),
    ("election", re.compile(r"Leader election started for term: (\d+)")),
    ("prevote", re.compile(r"Pre-vote started for next term: (\d+)")),
    ("noleader", re.compile(r"no current leader|is not available\. Will start new election|[Ll]eader failure detected")),
    ("setleader", re.compile(r"Setting leader: RaftEndpoint\{uuid='([^']+)'")),
    ("newterm", re.compile(r"Moving to new term: (\d+) from current term: (\d+)")),
    ("vote", re.compile(r"Granted (?:pre-)?vote for|(?:Pre-)?[Vv]ote granted from|majority during pre-vote|Ignored (?:Pre)?Vote")),
    ("status", re.compile(r"Status is set to: (\w+)")),
    ("group", re.compile(r"New CPGroupId|\] is (?:created|restored)|group .{0,40}(?:destroyed|created)")),
    ("session", re.compile(r"(?i)created new session|new session")),
    ("sessionend", re.compile(r"(?i)session.{0,80}(?:closed|closing|expired|invalidated|removed|destroyed)")),
    ("sessionother", re.compile(r"(?i)session")),
    ("restore", re.compile(r"CP restore (?:starting|completed)|Restored CP|Found existing CP data|Found Raft log")),
    ("members", re.compile(r"(?i)active CP members|CP Subsystem is enabled|Replaced CPMember|CP members? |already exists|"
                           r"promot|CPMembersContainer|CP member|is verified on|CPMemberInfo\{[^}]*\} is ")),
    ("rebalance", re.compile(r"(?i)leadership (?:claims|balance)|transfer leadership|rebalanc|get leadership")),
]


def sniff_log(path, max_lines=4000):
    """(format, address (host, port) or None, cluster, version) of a Hazelcast member log, or None (and why, in
    SNIFF_WHY). The format comes from the first lines: java.util.logging (two lines per record), a date-time at the
    start of the lines (ISO or day first), after something else, with the month in letters, JSON lines, or the time
    of day alone with the date in the file name. A partial log (no start or shutdown of the member) can have only
    application lines at first: the file is read until a Hazelcast line ([host]:port [cluster] [version]) shows up."""
    counts = dict((k, 0) for k in ("jul", "iso", "dmy", "isox", "mon", "json", "time"))
    has_day = fname_day(path) is not None
    hz = 0
    addrs = {}
    info = {}
    first = None
    read = 0
    try:
        with open_text(path) as f:
            for n, line in enumerate(f):
                read += len(line)
                if n < max_lines:
                    line = clean_log_line(line)
                    if first is None and line.strip():
                        first = line.strip()[:160]
                    c0 = line[:1]
                    if c0.isalpha() and JUL_HEAD_RE.match(line):
                        counts["jul"] += 1
                    elif (c0.isdigit() or c0 == "[") and ISO_HEAD_RE.match(line):
                        counts["iso"] += 1
                    elif (c0.isdigit() or c0 == "[") and DMY_HEAD_RE.match(line):
                        counts["dmy"] += 1
                    elif c0 == "{" and line.rstrip().endswith("}") and json_record(line, None) is not None:
                        counts["json"] += 1
                    elif has_day and c0 in "0123456789[" and TIME_HEAD_RE.match(line):
                        counts["time"] += 1
                    elif ISO_ANY_RE.search(line, 0, 120):
                        counts["isox"] += 1
                    elif MON_ANY_RE.search(line, 0, 120):
                        counts["mon"] += 1
                    if "azelcast" in line or "c.h." in line:
                        hz += 1
                elif addrs or read > LOG_SNIFF_MAX:
                    break
                if "]:" in line:
                    m = HZ_ADDR_RE.search(line)
                    if m:
                        k = (m.group(1), int(m.group(2)))
                        addrs[k] = addrs.get(k, 0) + 1
                        info.setdefault(k, (m.group(3), m.group(4)))
    except (OSError, EOFError, UnicodeError) as e:
        SNIFF_WHY[path] = "cannot read it: %s" % e
        return None
    fmt = max(counts, key=lambda k: counts[k])
    if counts[fmt] < 2:
        SNIFF_WHY[path] = "no date and time we know at the start of its lines" + (" (first line: %s)" % first if first else " (empty)")
        return None
    if not (hz or addrs):
        SNIFF_WHY[path] = "a log, but no Hazelcast line in it ([host]:port [cluster] [version]%s)" % (
            ", in the first %s" % human_size(LOG_SNIFF_MAX) if read > LOG_SNIFF_MAX else "")
        return None
    addr = max(addrs, key=lambda k: addrs[k]) if addrs else None
    cluster, version = info.get(addr, ("", ""))
    return fmt, addr, cluster, version


_DAY_CACHE = {}


def day_ms(y, mo, d):
    k = (y, mo, d)
    v = _DAY_CACHE.get(k)
    if v is None:
        v = _DAY_CACHE[k] = calendar.timegm((y, mo, d, 0, 0, 0, 0, 0, 0)) * 1000
    return v


def frac_ms(frac):
    return int((frac + "00")[:3]) if frac else 0


def zone_ms(z):
    if not z:
        return None
    if z == "Z":
        return 0
    sign = -1 if z[0] == "-" else 1
    z = z[1:].replace(":", "")
    return sign * (int(z[:2]) * 60 + int(z[2:4] or 0)) * 60000


def split_rest(rest):
    """level, thread, logger, message from what follows the date of a record."""
    m = REST_HZ_RE.match(rest)
    if m:
        return LOG_LEVELS.get(m.group(1), "I"), m.group(2), m.group(3), m.group(4)
    level, i = "I", 0
    thread = ""
    m = LEVEL_TOKEN_RE.search(rest, 0, 80)
    if m:
        level, i = LOG_LEVELS[m.group(1)], m.end()
        pre = rest[:m.start()].strip()
        if pre[:1] == "[" and pre[-1:] == "]" and "]" not in pre[1:-1]:
            thread = pre[1:-1]                      # [thread] INFO logger - msg
    tail = rest[i:].lstrip(" ]|:")
    if tail[:1].isdigit():                          # Spring Boot: '12345 --- [thread] logger : msg'
        tail = re.sub(r"^\d+\s+-+\s+", "", tail)
    if tail.startswith("["):
        j = tail.find("]")
        if j > 0:
            thread, tail = tail[1:j], tail[j + 1:].lstrip()
    logger = ""
    lt = LOGGER_THREAD_RE.match(tail) if not thread else None
    if lt:
        return level, lt.group(2), lt.group(1), tail[lt.end():]
    lm = LOGGER_RE.match(tail)
    if lm:
        logger, tail = lm.group(1), tail[lm.end():]
        tail = re.sub(r"^\s*(?:-+|:|\|)\s?", "", tail)
    return level, thread, logger, tail


def iter_log_records(path, fmt, disp_offset):
    """[display ms, level, logger, thread, message, continuation lines, lines dropped] per record."""
    cur = None
    with open_text(path) as f:
        if fmt == "jul":
            pend = None
            for line in f:
                line = line.rstrip("\r\n")
                if "\x1b" in line or "[m" in line or "[0m" in line:
                    line = clean_log_line(line)
                if line[:1].isalpha():
                    m = JUL_HEAD_RE.match(line)
                    if m:
                        if cur is not None:
                            yield cur
                            cur = None
                        h = int(m.group(4)) % 12 + (12 if m.group(7) == "PM" else 0)
                        t = (day_ms(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))
                             + ((h * 60 + int(m.group(5))) * 60 + int(m.group(6))) * 1000)
                        pend = (t, m.group(8))
                        continue
                if pend is not None:
                    m = JUL_LEVEL_RE.match(line)
                    cur = [pend[0], LOG_LEVELS[m.group(1)] if m else "I", pend[1], "", m.group(2) if m else line, [], 0]
                    pend = None
                    continue
                if cur is not None:
                    if len(cur[5]) < MAX_EXTRA_LINES:
                        cur[5].append(line)
                    else:
                        cur[6] += 1
        elif fmt in ("isox", "mon", "time", "json"):
            day = fname_day(path) if fmt == "time" else None
            prev_tod = None
            for line in f:
                line = line.rstrip("\r\n")
                if "\x1b" in line or "[m" in line or "[0m" in line:
                    line = clean_log_line(line)
                if fmt == "json":
                    rec = json_record(line, disp_offset) if line[:1] == "{" else None
                    if rec is not None:
                        if cur is not None:
                            yield cur
                        cur = rec
                        continue
                else:
                    hd = log_head(line, fmt, disp_offset)
                    if hd is not None:
                        t, rest = hd
                        if day is not None:             # the time of day: a new day when it goes back by over 12 h
                            if prev_tod is not None and t < prev_tod - 43200000:
                                day += 86400000
                            prev_tod = t
                            t += day
                        if cur is not None:
                            yield cur
                        level, thread, logger, msg = split_rest(rest)
                        cur = [t, level, logger, thread, msg, [], 0]
                        continue
                if cur is not None:
                    if len(cur[5]) < MAX_EXTRA_LINES:
                        cur[5].append(line)
                    else:
                        cur[6] += 1
        else:
            rx = ISO_HEAD_RE if fmt == "iso" else DMY_HEAD_RE
            for line in f:
                line = line.rstrip("\r\n")
                if "\x1b" in line or "[m" in line or "[0m" in line:
                    line = clean_log_line(line)
                c0 = line[:1]
                m = rx.match(line) if (c0.isdigit() or c0 == "[") else None
                if m:
                    if cur is not None:
                        yield cur
                    g = m.groups()
                    if fmt == "iso":
                        t = (day_ms(int(g[0]), int(g[1]), int(g[2])) + ((int(g[3]) * 60 + int(g[4])) * 60 + int(g[5])) * 1000
                             + frac_ms(g[6]))
                        z = zone_ms(g[7])
                        if z is not None and disp_offset is not None:
                            t = t - z + disp_offset
                        rest = g[8]
                    else:
                        t = (day_ms(int(g[2]), int(g[1]), int(g[0])) + ((int(g[3]) * 60 + int(g[4])) * 60 + int(g[5])) * 1000
                             + frac_ms(g[6]))
                        rest = g[7]
                    level, thread, logger, msg = split_rest(rest)
                    cur = [t, level, logger, thread, msg, [], 0]
                    continue
                if cur is not None:
                    if len(cur[5]) < MAX_EXTRA_LINES:
                        cur[5].append(line)
                    else:
                        cur[6] += 1
    if cur is not None:
        yield cur


def parse_health(text):
    """{key: (value, unit)} from a HealthMonitor line."""
    out = {}
    for part in text.split(","):
        k, eq, v = part.partition("=")
        if not eq:
            continue
        k, v = k.strip(), v.strip()
        unit = "count"
        mult = 1.0
        if v.endswith("%"):
            unit, v = "percent", v[:-1]
        elif v.endswith("ms"):
            unit, v = "ms", v[:-2]
        elif v[-1:] in SIZE_UNITS:
            unit, mult, v = "bytes", SIZE_UNITS[v[-1]], v[:-1]
        elif "memory" in k or ".space." in k:
            unit = "bytes"
        try:
            x = float(v) * mult
        except ValueError:
            continue
        out[k] = (x, unit)
    return out


def short_logger(logger):
    """com.hazelcast.internal.cluster.ClusterService -> ClusterService (with a group in parentheses kept)."""
    return logger.rsplit(".", 1)[-1] if logger else ""


class LogMember(object):
    """What the logs of one member say."""

    def __init__(self, key, label):
        self.key = key
        self.label = label
        self.files = []
        self.index = None
        self.first = self.last = None
        self.records = 0
        self.cluster = self.version = ""
        self.levels = Bins(LEVEL_BINS, 2)   # warning and error lines per time bin
        self.health_t = []
        self.health = {}                    # key -> list of values (None where missing)
        self.hunits = {}
        self.events = {}                    # category -> Sample of rows
        self.ecount = {}                    # category -> count
        self.ebins = {}                     # category -> Bins
        self.members = []                   # [t, size, ver, addresses]
        self.cpkeep = {}                    # kind -> Sample of [t, group, kind, term, level, text, record number]
        self.cpbins = {}                    # kind -> Bins (all the lines, counted)
        self.roles = {}                     # group -> [[t, role, term, leader]]
        self.cpstate = {}                   # group -> [term, role, leader]
        self.migkeep = {}                   # migration kind -> Sample of [t, kind, level, text, record number]
        self.migbins = {}                   # migration kind -> Bins (all the lines, counted)
        self.migprocs = []                  # migration plans: {s, e, rt, p, c, r, tot, o, el, pts}


class ExcGroup(object):
    __slots__ = ("cls", "root", "n", "first", "last", "lv", "per", "loggers", "msgs", "sample", "oome")

    def __init__(self, cls, root):
        self.cls, self.root = cls, root
        self.n = 0
        self.first = self.last = None
        self.lv = "I"
        self.per = {}                       # member index -> Bins
        self.loggers = {}
        self.msgs = {}
        self.sample = None
        self.oome = cls.endswith("OutOfMemoryError")


LEVEL_RANK = {"D": 0, "I": 1, "W": 2, "E": 3}


class Bins(object):
    """Counts per time bin: seconds, then coarser bins (BIN_STEPS) whenever there would be more than `cap`."""
    __slots__ = ("d", "step", "cap", "width")

    def __init__(self, cap, width=1):
        self.d, self.step, self.cap, self.width = {}, 1, cap, width

    def add(self, t, i=0, n=1):
        k = int(t // 1000) // self.step * self.step
        c = self.d.get(k)
        if c is None:
            if len(self.d) >= self.cap and self.step < BIN_STEPS[-1]:
                self.coarsen(self.cap)
                k = int(t // 1000) // self.step * self.step
                c = self.d.get(k)
            if c is None:
                c = self.d[k] = [0] * self.width
        c[i] += n

    def coarsen(self, cap):
        """Coarser bins until there are fewer than `cap` (and at least one step coarser)."""
        while self.step < BIN_STEPS[-1]:
            self.step = next(x for x in BIN_STEPS if x > self.step)
            merged = {}
            for k, v in self.d.items():
                m = merged.get(k // self.step * self.step)
                if m is None:
                    merged[k // self.step * self.step] = list(v)
                else:
                    for j, x in enumerate(v):
                        m[j] += x
            self.d = merged
            if len(self.d) < cap:
                break

    def __len__(self):
        return len(self.d)

    def export(self):
        """[[ms, count...]] in time order."""
        return [[k * 1000] + v for k, v in sorted(self.d.items())]


def fit_bins(bins, budget):
    """Coarsen the biggest sets of bins until they hold `budget` bins together at most."""
    total = sum(len(b) for b in bins)
    while total > budget:
        big = max(bins, key=len)
        if big.step >= BIN_STEPS[-1] or len(big) <= 1:
            break
        before = len(big)
        big.coarsen(before)
        total -= before - len(big)


class Sample(object):
    """At most 2 x `cap` rows spread evenly over the ones offered: when full, every other row is dropped and
    from then on only one row in `stride` is kept."""
    __slots__ = ("rows", "stride", "seen", "cap")

    def __init__(self, cap):
        self.rows, self.stride, self.seen, self.cap = [], 1, 0, cap

    def offer(self):
        """Whether the next row is kept (then add it with keep())."""
        self.seen += 1
        return (self.seen - 1) % self.stride == 0

    def keep(self, row):
        self.rows.append(row)
        if len(self.rows) >= 2 * self.cap:
            self.rows = self.rows[::2]
            self.stride *= 2


class LogCollector(object):
    def __init__(self):
        self.excs = {}
        self.oome = []                      # [t, member index, reason, logger, text]
        self.uuids = {}                     # CP member uuid -> address label
        self.cplog = {}                     # logger -> whether it is a CP Subsystem one

    def record(self, lm, rec, ctx):
        t, level, logger, thread, msg, extra, dropped = rec
        if not ctx.inside(t):
            return
        lm.records += 1
        if lm.first is None or t < lm.first:
            lm.first = t
        if lm.last is None or t > lm.last:
            lm.last = t
        # a line of Hazelcast's: its prefix ([host]:port [cluster] [version], or [LOCAL] before the address is known),
        # or a Hazelcast logger; the application's lines in the same log count as warnings, errors and exceptions only
        hzline = True
        m = HZ_PREFIX_RE.match(msg)
        if m is None and "]:" in msg[:300]:
            m = HZ_ADDR_RE.search(msg, 0, 300)          # after a logger and thread the layout did not separate
        if m:
            msg = msg[m.end():].lstrip()
            if not lm.version:
                lm.cluster, lm.version = m.group(3), m.group(4)
        else:
            m = HZ_LOCAL_RE.match(msg)
            if m:
                msg = msg[m.end():]
            else:
                hzline = logger.startswith(HZ_LOGGER_PREFIXES)
        if level in ("W", "E"):
            lm.levels.add(t, 0 if level == "W" else 1)
        if "heap.memory.used=" in msg or ("processors=" in msg and "load.process=" in msg):
            self.health(lm, t, msg)
            return
        head = msg.strip() or next((x.strip() for x in extra[:6] if x.strip()), "")
        # exceptions and OutOfMemoryError: the message and the lines that are not stack frames
        text_lines = [msg]
        if extra:
            for x in extra[:80]:
                s = x.lstrip()
                if s.startswith("at ") or s.startswith("... "):
                    continue
                text_lines.append(x)
        blob = "\n".join(text_lines)
        cat = None
        if "OutOfMemoryError" in blob:
            mo = OOME_RE.search(blob)
            reason = (mo.group(1) or "").strip()[:120] if mo else ""
            self.oome.append([t, lm.index, reason, logger, head.strip()[:400]])
            cat = "oome"
        if "Exception" in blob or "Error" in blob or "Throwable" in blob:
            self.exception(lm, t, level, logger, msg, extra, text_lines)
        low = (blob if head is msg else blob + "\n" + head).lower() if cat is None else ""
        if cat is None and any(w in low for w in CAT_WORDS):
            for key, rx in CAT_REGEX:
                if (hzline or key in ANY_SOURCE_CATS) and rx.search(blob if key == "quorum" else head):
                    cat = key
                    break
        if cat is None and hzline and level in ("W", "E") and WAN_LOGGER_RE.search(logger):
            cat = "wan"
        if hzline and ("igrat" in head or "artition" in head or "ebalance" in head or "LOST" in head):
            self.migration(lm, t, level, logger, head)
        if cat is None:
            mm = MEMBERS_LIST_RE.search(head) if "Members" in head else None
            if mm:
                size = int(mm.group(1) or mm.group(3))
                addrs = []
                for x in extra[:400]:
                    ml = MEMBER_LINE_RE.match(x)
                    if ml:
                        addrs.append(member_label(ml.group(1), int(ml.group(2))) + (" (this)" if x.rstrip().endswith("this") else ""))
                lm.members.append([t, size, int(mm.group(2)) if mm.group(2) else None])
                head = "Members {size:%d%s}: %s" % (size, ", ver:" + mm.group(2) if mm.group(2) else "", ", ".join(addrs))
                cat = "membership"
            elif " is " in head:
                ml = LIFECYCLE_RE.match(head)
                if ml:
                    head = ml.group(1)
                    cat = "lifecycle"
        if cat is not None:
            self.event(lm, cat, t, level, logger, head)
        cpl = self.cplog.get(logger)
        if cpl is None:
            cpl = self.cplog[logger] = bool(CP_LOGGER_RE.search(logger))
        if (cpl or (("CP" in head or "aft" in head) and CP_TEXT_RE.search(head))) and not head.startswith("License"):
            self.cp(lm, t, level, logger, msg, extra)

    def migration(self, lm, t, level, logger, head):
        """A line about migrations or the partition table: kept by kind; the plans followed from their first line
        (Repartitioning cluster data) through their progress to how they ended (one repartitionTime each)."""
        kind = m = None
        for k, _label, _sev, rx in MIG_KINDS:
            m = rx.search(head)
            if m:
                kind = k
                break
        if kind is None:
            return
        b = lm.migbins.get(kind)
        if b is None:
            b = lm.migbins[kind] = Bins(EXC_BINS)
        b.add(t)
        keep = lm.migkeep.get(kind)
        if keep is None:
            keep = lm.migkeep[kind] = Sample(MAX_LOG_EVENTS)
        if keep.offer():
            keep.keep([t, kind, level, head.strip()[:400], lm.records])
        procs = lm.migprocs
        cur = procs[-1] if procs else None
        if kind == "start":
            n = int(m.group(1))
            procs.append({"s": t, "e": None, "rt": None, "p": n, "c": 0, "r": n, "tot": None, "o": None, "el": None, "pts": [[t, n]]})
            return
        if kind == "failed" and "interrupted" in head:
            if cur is not None and cur["o"] is None:
                cur["o"], cur["e"] = "interrupted", t
            return
        if kind not in ("progress", "done", "failed"):
            return
        st = MIG_STATS_RE.search(head)
        if not st:
            return
        rt = st.group(1)
        dm = MIG_DATE_RE.search(rt)                 # when the plan was made (the member's local time, like its log lines)
        rt_t = None
        if dm:
            g = dm.groups()
            x = day_ms(int(g[5]), MONTHS[g[0]], int(g[1])) + ((int(g[2]) * 60 + int(g[3])) * 60 + int(g[4])) * 1000
            if t - 7 * 86400000 <= x <= t + 60000:
                rt_t = x
        if cur is None or (cur["rt"] is not None and cur["rt"] != rt) or (cur["rt"] is None and cur["o"] is not None) or \
                (cur["rt"] is None and rt_t is not None and abs(rt_t - cur["s"]) > 120000):
            s0 = rt_t if rt_t is not None else t    # a plan whose first line is not in the logs: it started at repartitionTime
            cur = {"s": s0, "e": None, "rt": rt, "p": int(st.group(2)), "c": 0, "r": int(st.group(2)), "tot": None, "o": None, "el": None,
                   "pts": [[s0, int(st.group(2))]]}
            procs.append(cur)
        cur["rt"] = rt
        cur["p"], cur["c"], cur["r"], cur["tot"] = int(st.group(2)), int(st.group(3)), int(st.group(4)), int(st.group(5))
        if st.group(6):
            cur["el"] = int(st.group(6))
        if cur["o"] is None:
            cur["pts"].append([t, cur["r"]])
            if kind == "done":
                cur["o"], cur["e"] = "completed", t
            elif kind == "failed":
                cur["o"], cur["e"] = ("aborted" if "aborted" in head else "failed"), t

    def event(self, lm, cat, t, level, logger, text):
        lm.ecount[cat] = lm.ecount.get(cat, 0) + 1
        b = lm.ebins.get(cat)
        if b is None:
            b = lm.ebins[cat] = Bins(EXC_BINS)
        b.add(t)
        rows = lm.events.get(cat)
        if rows is None:
            rows = lm.events[cat] = Sample(MAX_LOG_EVENTS)
        if rows.offer():
            rows.keep([t, level, short_logger(logger), text.strip()[:400], lm.records])

    def health(self, lm, t, text):
        vals = parse_health(text)
        if not vals:
            return
        i = len(lm.health_t)
        lm.health_t.append(t)
        for k, (x, unit) in vals.items():
            col = lm.health.get(k)
            if col is None:
                col = lm.health[k] = [None] * i
                lm.hunits[k] = unit
            col.append(x)
        for col in lm.health.values():
            if len(col) <= i:
                col.append(None)

    def exception(self, lm, t, level, logger, msg, extra, text_lines):
        top = root = None
        top_msg = ""
        for x in text_lines:
            s = x.strip()
            caused = s.startswith("Caused by:")
            if caused or s.startswith("Suppressed:"):
                m = EXC_RE.search(s)
                if m and caused:
                    root = m.group(1)
                continue
            if top is None:
                m = EXC_RE.search(s)
                if m:
                    top, top_msg = m.group(1), (m.group(2) or "").strip()
        if top is None:
            return
        if root == top:
            root = None
        key = (top, root)
        g = self.excs.get(key)
        if g is None:
            g = self.excs[key] = ExcGroup(top, root)
        g.n += 1
        if g.first is None or t < g.first:
            g.first = t
        if g.last is None or t > g.last:
            g.last = t
        if LEVEL_RANK.get(level, 1) > LEVEL_RANK.get(g.lv, 1):
            g.lv = level
        per = g.per.get(lm.index)
        if per is None:
            per = g.per[lm.index] = Bins(EXC_BINS)
        per.add(t)
        sl = short_logger(logger)
        g.loggers[sl] = g.loggers.get(sl, 0) + 1
        nm = NORM_RE.sub("#", top_msg)[:200]
        if nm in g.msgs or len(g.msgs) < 50:
            e = g.msgs.get(nm)
            if e is None:
                g.msgs[nm] = [1, top_msg[:300]]
            else:
                e[0] += 1
        if g.sample is None:
            lines = [msg] + list(extra[:MAX_SAMPLE_LINES])
            g.sample = [t, lm.index, logger, "\n".join(x[:300] for x in lines)]

    def cp(self, lm, t, level, logger, msg, extra):
        head = msg.strip() or next((x.strip() for x in extra[:6] if x.strip()), "")
        text = head
        gm = CP_GROUP_SUFFIX_RE.search(logger)
        group = gm.group(1) if gm else None
        if group is None:
            m = CPGROUPID_RE.search(msg)
            group = m.group(1) if m else ""
        for x in [msg] + list(extra[:20]):
            for m in CP_MEMBER_RE.finditer(x):
                self.uuids[m.group(1)] = member_label(m.group(2), int(m.group(3)))
        snap = CP_SNAP_RE.search(msg)
        lines = extra
        if snap is None and extra:
            for i, x in enumerate(extra[:3]):
                snap = CP_SNAP_RE.search(x)
                if snap:
                    lines = extra[i + 1:]
                    break
        st = lm.cpstate.setdefault(group, [None, None, None])
        kind, term, role = None, None, None
        if snap:
            group = snap.group(1).strip()
            st = lm.cpstate.setdefault(group, [None, None, None])
            term = int(snap.group(4))
            me, leader = None, None
            for x in lines[:50]:
                m = CP_MEMBER_RE.search(x)
                if not m:
                    if x.strip().startswith("]"):
                        break
                    continue
                lab = member_label(m.group(2), int(m.group(3)))
                if m.group(5):
                    me = m.group(4) or "FOLLOWER"
                if m.group(4) == "LEADER":
                    leader = lab
            kind, role = "snapshot", me
            st[2] = leader if leader else st[2]
            text = "term %d · this member %s%s" % (term, (me or "?").lower(), " · leader " + leader if leader else "")
        else:
            for k, rx in CP_KINDS:
                m = rx.search(head)
                if m:
                    kind = k
                    if k == "leader":
                        role, st[2] = "LEADER", lm.label
                    elif k == "election":
                        term, role = int(m.group(1)), "CANDIDATE"
                    elif k == "newterm":
                        term = int(m.group(1))
                    elif k == "setleader":
                        role, st[2] = "FOLLOWER", self.uuids.get(m.group(1), m.group(1)[:8])
                    elif k == "noleader":
                        role, st[2] = "FOLLOWER", None
                    elif k == "demote":
                        term, role = int(m.group(2)), m.group(1)
                        if m.group(3):
                            st[2] = self.uuids.get(m.group(3), m.group(3)[:8])
                    break
            if kind is None:
                kind = "problem" if level in ("W", "E") else "other"
        if term is not None:
            if st[0] is None:                       # the term was not known yet: give it to the lines just before
                for row in lm.roles.get(group, [])[-8:]:
                    if row[2] is None and t - row[0] <= 30000:
                        row[2] = term
                for k in ("leader", "setleader"):
                    for row in (lm.cpkeep[k].rows[-10:] if k in lm.cpkeep else []):
                        if row[1] == group and row[3] is None and t - row[0] <= 30000:
                            row[3] = term
            st[0] = term
        if role is not None:
            st[1] = role
            lm.roles.setdefault(group, []).append([t, role, st[0], st[2]])
        b = lm.cpbins.get(kind)
        if b is None:
            b = lm.cpbins[kind] = Bins(EXC_BINS)
        b.add(t)
        keep = lm.cpkeep.get(kind)
        if keep is None:
            keep = lm.cpkeep[kind] = Sample(MAX_CP_ROWS)
        if keep.offer():
            keep.keep([t, group, kind, st[0] if kind in ("snapshot", "leader", "election", "newterm", "setleader", "demote") else None,
                       level, text.strip()[:400], lm.records])


def match_log_members(logfiles, members, wanted):
    """Member logs by member: the address in their lines, matched with the diagnostics members (localhost counts
    as 127.0.0.1). Returns ([LogMember], extra member labels for those without diagnostics)."""
    def norm(h, p):
        h = norm_host(h)
        return ("127.0.0.1" if h in ("localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1") else h, p)
    by_addr = {}
    for i, m in enumerate(members):
        if m.key[0] == "addr":
            by_addr.setdefault(norm(m.key[1], m.key[2]), []).append(i)
    lms = {}
    for lf in logfiles:
        if lf.log_addr is not None:
            key = norm(*lf.log_addr)
            label = member_label(lf.log_addr[0], lf.log_addr[1])
        else:
            key = ("file", lf.reldir + "/" + re.sub(r"[.-]?\d*(\.log)?(\.\d+)?(\.gz)?$", "", lf.name))
            label = "log " + (lf.reldir + "/" if lf.reldir != "." else "") + lf.name
        lm = lms.get(key)
        if lm is None:
            lm = lms[key] = LogMember(key, label)
            hits = by_addr.get(key, [])
            if len(hits) == 1:
                lm.index = hits[0]
                lm.label = members[hits[0]].label
        lm.files.append(lf)
    out = list(lms.values())
    if wanted:
        def hit(lm):
            if lm.index is not None:
                return True
            for w in wanted:
                if lm.key[0] == "file":
                    if w.strip().strip("/") and w.strip().strip("/") in lm.label:
                        return True
                    continue
                host, port = parse_member_arg(w)
                if norm(host, lm.key[1])[0] == lm.key[0] and (port is None or port == lm.key[1]):
                    return True
            return False
        out = [lm for lm in out if hit(lm)]
    return out


def parse_member_logs(lms, members, ctx, disp_offset, quiet):
    """Read every member log; returns the collector. Log-only members get indices after the diagnostics members."""
    col = LogCollector()
    nxt = len(members)
    for lm in sorted(lms, key=lambda x: x.label):
        if lm.index is None:
            lm.index = nxt
            nxt += 1
    for i, lm in enumerate(sorted(lms, key=lambda x: x.index)):
        if not quiet:
            sys.stderr.write("  [logs %d/%d] %s: %d file(s), %s\n" % (i + 1, len(lms), lm.label, len(lm.files),
                                                                       human_size(sum(f.size for f in lm.files))))
        firsts = {}
        for lf in lm.files:                 # rotated files: oldest first
            try:
                firsts[lf.path] = next(iter_log_records(lf.path, lf.log_fmt, disp_offset))[0]
            except (StopIteration, OSError, EOFError):
                firsts[lf.path] = 0
        files = sorted(lm.files, key=lambda f: (firsts[f.path], f.name))
        for lf in files:
            try:
                for rec in iter_log_records(lf.path, lf.log_fmt, disp_offset):
                    col.record(lm, rec, ctx)
            except (OSError, EOFError) as e:
                warn("cannot read %s: %s" % (lf.path, e))
    return col


def export_logs(lms, col):
    if not lms:
        return None
    files, levels, events, evbins, health, membership, cp, roles, sampled = [], [], [], [], [], [], [], [], []
    hkeys, hidx = [], {}
    fit_bins([b for lm in lms for b in lm.ebins.values()] + [b for g in col.excs.values() for b in g.per.values()]
             + [b for lm in lms for b in lm.cpbins.values()], BIN_BUDGET)
    cpbins = []
    for lm in sorted(lms, key=lambda x: x.index):
        mi = lm.index
        for lf in lm.files:
            files.append([mi, (lf.reldir + "/" if lf.reldir != "." else "") + lf.name, lf.size, lf.log_fmt])
        if lm.levels:
            levels.append([mi, lm.levels.export(), lm.levels.step * 1000])
        for cat, smp in lm.events.items():
            ci = CAT_INDEX[cat]
            for r in smp.rows:
                events.append([r[0], mi, ci, r[1], r[2], r[3], r[4]])
            evbins.append([ci, mi, lm.ecount.get(cat, 0), lm.ebins[cat].export()])
            if smp.stride > 1:
                sampled.append([mi, cat, smp.seen, len(smp.rows)])
        if lm.health_t:
            cols = {}
            for k, vals in lm.health.items():
                if k not in hidx:
                    hidx[k] = len(hkeys)
                    hkeys.append([k, lm.hunits[k], "c" if k in HEALTH_COUNTERS else "g"])
                cols[hidx[k]] = health_values(lm.health_t, vals, k in HEALTH_COUNTERS)
            health.append([mi, [int(t) for t in lm.health_t], cols])
        if lm.members:
            membership.append([mi, lm.members])
        for kind, smp in lm.cpkeep.items():
            for r in smp.rows:
                cp.append([r[0], mi] + r[1:])
            if smp.stride > 1:
                sampled.append([mi, "cp:" + kind, smp.seen, len(smp.rows)])
        for kind, b in lm.cpbins.items():
            cpbins.append([kind, mi, sum(v[0] for v in b.d.values()), b.export()])
        for g, rows in lm.roles.items():
            roles.append([mi, g, rows])
    events.sort(key=lambda e: (e[0], e[1], e[6]))      # in the order they were logged, without the record number
    cp.sort(key=lambda e: (e[0], e[1], e[7]))
    events = [e[:6] for e in events]
    cp = [e[:7] for e in cp]
    excs = []
    for g in sorted(col.excs.values(), key=lambda g: (-g.n, g.cls)):
        excs.append({"c": g.cls, "r": g.root, "lv": g.lv, "n": g.n, "f": int(g.first), "l": int(g.last),
                     "ms": [[mi, per.export()] for mi, per in sorted(g.per.items())],
                     "lg": [k for k, _v in sorted(g.loggers.items(), key=lambda kv: -kv[1])[:4]],
                     "msg": [[v[1], v[0]] for _k, v in sorted(g.msgs.items(), key=lambda kv: -kv[1][0])[:8]],
                     "s": g.sample, "oome": g.oome})
    info = [[lm.index, lm.version, lm.cluster, lm.records, int(lm.first) if lm.first is not None else None,
             int(lm.last) if lm.last is not None else None, len(lm.files)] for lm in lms]
    mig = {"kinds": [[k[0], k[1], k[2]] for k in MIG_KINDS], "rows": [], "bins": [], "procs": []}
    for lm in sorted(lms, key=lambda x: x.index):
        for kind, smp in lm.migkeep.items():
            for r in smp.rows:
                mig["rows"].append([r[0], lm.index, MIG_INDEX[kind], r[2], r[3], r[4]])
            if smp.stride > 1:
                sampled.append([lm.index, "mig:" + kind, smp.seen, len(smp.rows)])
        for kind, b in lm.migbins.items():
            mig["bins"].append([MIG_INDEX[kind], lm.index, sum(v[0] for v in b.d.values()), b.export()])
        for pr in lm.migprocs:
            mig["procs"].append([lm.index, int(pr["s"]), int(pr["e"]) if pr["e"] is not None else None, pr["rt"] or "", pr["p"], pr["c"], pr["r"],
                                 pr["tot"], pr["o"] or "", pr["el"], [int(x) for q in pr["pts"] for x in q]])
    mig["rows"].sort(key=lambda e: (e[0], e[1], e[5]))
    mig["rows"] = [e[:5] for e in mig["rows"]]
    return {"files": files, "info": info, "cats": [[c[0], c[1], c[2]] for c in LOG_CATS], "levels": levels,
            "events": events, "evbins": evbins, "exc": excs, "oome": sorted(col.oome, key=lambda r: r[0]),
            "health": {"keys": hkeys, "rows": health}, "membership": membership, "cp": cp, "roles": roles,
            "uuids": col.uuids, "sampled": sampled, "cpbins": cpbins, "mig": mig}


def health_values(times, vals, counter):
    """Gauges as they are; counters as a rate per second between two samples (a drop, a restart, is skipped)."""
    if not counter:
        return [rnd(x) if x is not None else None for x in vals]
    out = [None] * len(vals)
    for i in range(1, len(vals)):
        a, b, dt = vals[i - 1], vals[i], times[i] - times[i - 1]
        if a is not None and b is not None and b >= a and 0 < dt <= 600000:
            out[i] = rnd((b - a) * 1000.0 / dt)
    return out


# --------------------------------------------------------------------------- report
def write_report(path, payload, rows_json):
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    raw = raw[:-1] + ',"prof":' + rows_json + "}"        # the profiler rows are JSON text already
    if len(raw) > 1500000:
        data = base64.b64encode(gzip.compress(raw.encode("utf-8"), 6)).decode("ascii")
        enc = "gzip"
    else:
        data = raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        enc = "json"
    nreal = sum(1 for m in payload["members"] if not m.get("gcOnly"))
    title = "Hazelcast diagnostics · %d member%s" % (nreal, "" if nreal == 1 else "s") if nreal else "Hazelcast GC logs"
    doc = (HTML_TEMPLATE.replace("__TITLE__", html.escape(title))
           .replace("__ENC__", enc).replace("__DATA__", data))
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    return os.path.getsize(path)


# --------------------------------------------------------------------------- main
def build_arg_parser():
    ap = argparse.ArgumentParser(
        prog="hz_diag_parser.py",
        description="Read Hazelcast diagnostics logs (all members under a directory) and write one interactive\n"
                    "HTML report: cluster, latency profilers (latency groups and slow threshold chosen in the report),\n"
                    "slow operations, operations, stuck threads, saturated executors, network, data transfer per member\n"
                    "and peer, WAN per publisher, data structures (maps, queues, topics...), MapStore latency, system,\n"
                    "CP Subsystem (CP objects too) and other metrics, with a Measure mode that gives the amount\n"
                    "(MB/GB...) in any span.\n"
                    "Member logs found there too (hazelcast.log, console output) get their own tabs: health monitor,\n"
                    "exceptions, OutOfMemoryError, notable lines and CP Subsystem activity; GC logs get a GC tab: every\n"
                    "pause, humongous allocations, full GCs and evacuation failures, heap after GC.",
        epilog="examples:\n"
               "  %(prog)s /data/diagnostics out/\n"
               "  %(prog)s /data/diagnostics out/ --member 10.0.0.1:5701 --member 10.0.0.2\n"
               "  %(prog)s /data/diagnostics out/ --from '27-10-2026 15:00' --to '27-10-2026 16:00'",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input_dir", help="directory with the diagnostics files, member logs and/or GC logs (searched "
                                      "recursively; .gz too)")
    ap.add_argument("output_dir", nargs="?", default="hz-diag-report",
                    help="where report.html goes (default ./hz-diag-report)")
    ap.add_argument("-m", "--member", action="append", metavar="HOST[:PORT]",
                    help="only these members (repeatable); a directory name works for unnamed files")
    ap.add_argument("--from", dest="time_from", metavar="TIME",
                    help="only data at or after TIME (log time, e.g. '27-10-2026 15:00')")
    ap.add_argument("--to", dest="time_to", metavar="TIME", help="only data at or before TIME")
    ap.add_argument("--points", type=points_arg, default=None, metavar="max|N",
                    help="time resolution: 'max' (default) keeps every metric and profiler dump, one point "
                         "each; N merges them into about N points across the time range (smaller file)")
    ap.add_argument("--top", type=int, default=40, metavar="N",
                    help="per metric, chart data for the N most active maps / threads / pipelines... (default 40)")
    ap.add_argument("--budget", type=int, default=8000000, metavar="N",
                    help="at most about N values for the per-map / per-thread / per-pipeline lines; past it "
                         "those lines get coarser, the rest keeps its resolution (default 8,000,000; 0: no limit)")
    ap.add_argument("--open", action="store_true", help="open the report in a browser when done")
    ap.add_argument("-q", "--quiet", action="store_true", help="don't print progress")
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return ap


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_arg_parser().parse_args(argv)
    try:
        return run(args)
    except UsageError as e:
        sys.stderr.write("error: %s\n" % e)
        return 2
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:             # output piped into head, less, ...
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        return 1


def choose_time(files):
    """With epoch millis everywhere: align on them and show the members' common local time (UTC if
    their time zones differ). Without: use the printed times as they are."""
    headed = [f for f in files if f.first_printed is not None]
    use_epoch = bool(headed) and all(f.first_epoch is not None for f in headed)
    offsets = sorted(set(int(round((f.first_printed - f.first_epoch) / 60000.0 / 15.0)) * 15
                         for f in headed if f.first_epoch is not None))
    if use_epoch and len(offsets) == 1:
        return True, offsets[0], fmt_offset(offsets[0]), "server time (%s)" % fmt_offset(offsets[0])
    if use_epoch:
        return True, 0, "UTC", "UTC (the members log in different time zones: %s)" % \
            ", ".join(fmt_offset(o) for o in offsets)
    return False, 0, "as printed", "time as printed in the logs (no epoch millis in some files)"


def run(args):
    started = time.time()
    if not os.path.isdir(args.input_dir):
        raise UsageError("not a directory: %s" % args.input_dir)
    root = os.path.abspath(args.input_dir)
    files = [LogFile(p, root) for p in discover(args.input_dir)]
    for lf in files:
        lf.peek()
    logfiles = []
    for lf in files:
        if lf.looks_diag:
            continue
        r = sniff_log(lf.path)
        if r:
            lf.log_fmt, lf.log_addr, lf.log_cluster, lf.log_version = r
            logfiles.append(lf)
    gcfiles = []
    for lf in files:
        if not lf.looks_diag and lf.log_fmt is None:
            g = sniff_gc(lf.path)
            if g:
                gcfiles.append(GcFile(lf, g))
    gcpaths = set(gf.lf.path for gf in gcfiles)
    skipped = [f for f in files if not f.looks_diag and f.log_fmt is None and f.path not in gcpaths]
    files = [f for f in files if f.looks_diag]
    if not files and not logfiles and not gcfiles:
        raise UsageError("no Hazelcast diagnostics files, member logs or GC logs found under %s" % args.input_dir)
    use_epoch, offset_min, tz, time_note = choose_time(files)
    if not files:
        time_note = "time as printed in the " + ("member and GC logs" if logfiles and gcfiles else "member logs" if logfiles else "GC logs")
    disp_offset = offset_min * 60000 if use_epoch else None
    log_firsts = []
    for lf in logfiles:
        try:
            lf.first_printed = next(iter_log_records(lf.path, lf.log_fmt, disp_offset))[0]
            log_firsts.append(lf.first_printed)
        except (StopIteration, OSError, EOFError):
            pass

    def disp(printed, epoch):
        if use_epoch and epoch is not None:
            return epoch + offset_min * 60000
        return printed

    firsts = [disp(f.first_printed, f.first_epoch) for f in files if f.first_printed is not None]
    lasts = [disp(f.last_printed, f.last_epoch) for f in files if f.last_printed is not None]
    ref = min(firsts or log_firsts or [0])
    lo = parse_when(args.time_from, ref) if args.time_from else None
    hi = parse_when(args.time_to, ref, end=True) if args.time_to else None
    if lo is not None and hi is not None and hi < lo:
        raise UsageError("--to is before --from")
    try:
        units = build_units(files, args.member) if files else []
    except UsageError:
        if not logfiles:
            raise
        units = []                              # the members asked for may have member logs only
    span0 = min(firsts) if firsts else ref
    span1 = max(lasts + firsts) if (lasts or firsts) else ref
    ctx = Ctx(use_epoch, offset_min * 60000, lo, hi)
    total = sum(f.size for u in units for f in u.files)
    for i, u in enumerate(units):
        if not args.quiet:
            sys.stderr.write("  [%d/%d] %s: %d file(s), %s\n" % (i + 1, len(units), u.label, len(u.files),
                                                                 human_size(sum(f.size for f in u.files))))
        for lf in u.files:
            try:
                parse_file(lf, u, ctx)
            except (OSError, EOFError) as e:
                warn("cannot read %s: %s" % (lf.path, e))
    members = finish_members(units)
    lms = match_log_members(logfiles, members, args.member)
    if args.member and not units and not lms:
        raise UsageError("no files for %s" % ", ".join(args.member))
    col = parse_member_logs(lms, members, ctx, disp_offset, args.quiet)
    total += sum(f.size for lm in lms for f in lm.files)
    for i, gf in enumerate(gcfiles):
        if not args.quiet:
            sys.stderr.write("  [gc %d/%d] %s, %s\n" % (i + 1, len(gcfiles), gf.lf.name, human_size(gf.lf.size)))
        parse_gc_file(gf, ctx)
    if args.member:                             # only the GC logs of the members asked for
        assign_gc_selected(gcfiles, members, lms, files, logfiles, ctx)
        anchor_gc(gcfiles, members, ctx)
        gcfiles = [gf for gf in gcfiles if gf.member is not None and not gf.drop]
    else:
        assign_gc(gcfiles, members, lms, ctx)
        anchor_gc(gcfiles, members, ctx)
    uptime_axis = not files and not lms and not any(gf.events for gf in gcfiles) and any(gf.up_runs for gf in gcfiles)
    if uptime_axis:                             # GC logs alone, with the uptime only: an uptime axis
        place_uptime(gcfiles, ctx)
        time_note = "JVM uptime: the GC logs have no date-time and no diagnostics came with them (0 is each JVM's start)"
    for gf in gcfiles:
        gf.unplaced = sum(len(r["events"]) for r in gf.up_runs)
        gf.up_runs = []
    total += sum(gf.lf.size for gf in gcfiles)
    nmem = max([len(members)] + [lm.index + 1 for lm in lms])
    gc = export_gc(gcfiles, nmem)
    parsed_s = time.time() - started
    if (lo is not None or hi is not None) and not any(u.mtimes or u.prof or u.cluster or u.events or u.hb or u.slowops or u.stot for u in units) \
            and not any(lm.records for lm in lms) and not any(gf.events for gf in gcfiles):
        raise UsageError("no data between --from and --to; these logs cover %s .. %s"
                         % (fmt_time(min(firsts + log_firsts)), fmt_time(max(lasts + firsts + log_firsts))))
    c = ctx.counters
    if c["truncated"]:
        warn("%d section(s) were cut short (end of a file, crash) and were skipped" % c["truncated"])
    if c["out_of_order"]:
        warn("%d line(s) older than what was already read for the same member were skipped "
             "(copies of the same data?)" % c["out_of_order"])

    covered = [c for m in members for c in m.cover] + [[lm.first, lm.last] for lm in lms if lm.first is not None]
    slowops = export_slowops(members)
    covered += [[r[2], r[2] + r[3]] for r in slowops["inv"]]    # a slow invocation can start before the first dump
    invs = export_invocations(members)
    covered += [[r[7], r[10]] for r in invs["inv"]]
    props = export_props(members)
    if not covered:                             # GC logs alone; with diagnostics or member logs, those set the range
        covered = [[gf.first, gf.last] for gf in gcfiles if gf.first is not None]
    rng = [min(c[0] for c in covered), max(c[1] for c in covered)] if covered else [span0, span1]
    if lo is not None:
        rng[0] = max(rng[0], lo)
    if hi is not None:
        rng[1] = min(rng[1], hi)
    stuck = export_stuck(members)               # before the metrics: it adds the per-member stall series
    pools = export_saturation(members)
    transfer = export_transfer(members)
    mgrid, period = build_grid(units, args.points)
    budget = max(100000, args.budget) if args.budget > 0 else float("inf")
    if mgrid is not None:
        mx = export_metrics(units, members, mgrid, period, max(1, args.top), budget)
    else:
        mx = {"metrics": [], "static": [], "flat": [], "timestamps": [], "efactor": 1}
    pnative = max(1000, int(round(profiler_period(units) / 1000.0)) * 1000)
    store = export_store(members)
    snative = max(1000, int(round(profiler_period(units, store=True) / 1000.0)) * 1000)
    pbin = None
    if args.points is not None:
        pbin = nice_step(max(pnative * 0.95, (rng[1] - rng[0]) / float(args.points)))
        if pbin <= pnative:
            pbin = None                         # already that coarse: keep every interval as it is
    prof = export_profilers(members, pbin)
    runs, lists = export_cluster(members)
    events, dropped, hb = export_events(members)
    logs = export_logs(lms, col)
    meta = {
        "version": __version__,
        "generated": datetime.datetime.now().strftime("%d-%m-%Y %H:%M"),
        "cmd": " ".join(["hz_diag_parser.py"] + [x if re.match(r"^[\w./:#@=,+-]+$", x) else repr(x)
                                                  for x in sys.argv[1:]]),
        "input": args.input_dir, "files": sum(len(m.files) for m in members),
        "bytes": sum(f.size for m in members for f in m.files),
        "skipped": [f.name + (" (" + SNIFF_WHY[f.path] + ")" if f.path in SNIFF_WHY else "") for f in skipped][:50],
        "logfiles": sum(len(lm.files) for lm in lms), "logbytes": sum(f.size for lm in lms for f in lm.files),
        "gcfiles": len(gcfiles), "gcbytes": sum(gf.lf.size for gf in gcfiles),
        "tz": tz, "timeNote": time_note, "range": [int(rng[0]), int(rng[1])], "uptime": uptime_axis,
        "window": [lo, hi] if (lo is not None or hi is not None) else None,
        "counters": c, "dropped": dropped, "top": args.top, "efactor": mx["efactor"],
        "timestamps": mx["timestamps"], "points": "max" if args.points is None else args.points,
        "pt0": prof["pt0"], "pstep": pbin or pnative, "pnative": pnative, "pfine": FINE_LO, "sstep": max(pbin or 0, snative),
    }
    plist = [{"label": m.label, "files": len(m.files), "bytes": sum(f.size for f in m.files),
              "dirs": m.dirs, "lifetimes": [[int(u.cover[0][0]), int(u.cover[-1][1])] for u in m.units if u.cover],
              "cover": [[int(a), int(b)] for a, b in m.cover]} for m in members]
    for lm in sorted(lms, key=lambda x: x.index):
        if lm.index >= len(members):            # member logs only
            plist.append({"label": lm.label, "files": 0, "bytes": 0, "dirs": sorted(set(f.reldir for f in lm.files)),
                          "lifetimes": [], "cover": [], "logsOnly": True})
    for label in gc["extra"]:                   # GC logs that no member's files are near
        plist.append({"label": label, "files": 0, "bytes": 0, "dirs": [], "lifetimes": [], "cover": [], "logsOnly": True, "gcOnly": True})
    payload = {
        "meta": meta,
        "members": plist,
        "g": {"t0": mgrid.t0, "step": mgrid.step, "n": mgrid.n} if mgrid else None,
        "metrics": mx["metrics"], "static": mx["static"], "flat": mx["flat"],
        "ops": prof["ops"],
        "events": events, "cluster": runs, "lists": lists, "hb": hb, "threads": stuck, "pools": pools, "transfer": transfer,
        "slowops": slowops, "inv": invs, "props": props, "gc": gc, "store": store, "logs": logs,
    }
    os.makedirs(args.output_dir, exist_ok=True)
    report = os.path.join(args.output_dir, "report.html")
    size = write_report(report, payload, prof["rows_json"])
    took = time.time() - started

    p = print
    labels = [x["label"] for x in plist]
    p("Hazelcast diagnostics report  (hz_diag_parser.py %s)" % __version__)
    p("  input    : %s  (%d diagnostics files, %s; %d member log files, %s%s)"
      % (args.input_dir, meta["files"], human_size(meta["bytes"]), meta["logfiles"], human_size(meta["logbytes"]),
         "; %d GC log files, %s" % (meta["gcfiles"], human_size(meta["gcbytes"])) if gcfiles else ""))
    if skipped:
        p("  skipped  : %d file(s) that are neither diagnostics, member logs nor GC logs" % len(skipped))
        for f in skipped[:20]:
            p("             %s: %s" % ((f.reldir + "/" if f.reldir != "." else "") + f.name,
                                      SNIFF_WHY.get(f.path, "not a diagnostics file (no section we know) nor a GC log")))
        if len(skipped) > 20:
            p("             ... and %d more" % (len(skipped) - 20))
    real = [x["label"] for x in plist if not x.get("gcOnly")]
    p("  members  : %s" % ("%d  (%s)" % (len(real), ", ".join(real)) if real else "none (GC logs only)"))
    if len(real) < len(labels):
        p("             + %d GC log(s) not linked to any member: %s" % (len(labels) - len(real), ", ".join(labels[len(real):])))
    ft = fmt_uptime if uptime_axis else fmt_time
    p("  window   : %s .. %s (%s), %s" % (ft(rng[0]), ft(rng[1]), human_dur((rng[1] - rng[0]) / 1000.0), time_note))
    if members:
        nmaps = [r for r in mx["metrics"] if r["c"] == "maps"]
        hidden_maps = set(h for r in nmaps for h in r.get("hidden", []))
        p("  metrics  : %d charted, %d unchanged (table), %d with no changing series, %d data structure metric(s)"
          % (len(mx["metrics"]), len(mx["static"]), len(mx["flat"]), len(nmaps)))
        if hidden_maps:
            p("  structs  : %d data structure(s) left out of at least one of their metrics because its value never changed"
              % len(hidden_maps))
        if mgrid is not None:
            p("  detail   : metrics %s, profilers %s" % (
                ("every dump (%s)" % step_name(mgrid.step)) if args.points is None else "per " + step_name(mgrid.step),
                ("every dump (%s)" % step_name(pnative)) if pbin is None else "per " + step_name(pbin)))
        else:
            p("  detail   : profilers %s" % (("every dump (%s)" % step_name(pnative)) if pbin is None
                                            else "per " + step_name(pbin)))
        if mx["efactor"] > 1:
            p("  note     : per-map / per-thread lines are per %s to stay under --budget %s values "
              "(raise it, or --budget 0 for no limit)" % (step_name(mgrid.step * mx["efactor"]), format(args.budget, ",")))
        p("  profiler : %d operation(s)" % len(prof["ops"]))
        p("  events   : %d, late heartbeats: %d" % (len(events), len(hb)))
    if stuck["list"]:
        eps = stuck["eps"]
        if eps:
            worst = max(eps, key=lambda e: e[3] - e[1])
            th = stuck["list"][worst[0]]
            p("  threads  : %d checked, %d STUCK (%d episode%s); longest %s: %s on %s%s"
              % (len(stuck["list"]), len(set(e[0] for e in eps)), len(eps), "" if len(eps) == 1 else "s",
                 human_dur((worst[3] - worst[1]) / 1000.0), th[3], members[th[0]].label,
                 " (still stuck at its last dump)" if worst[7] in ("end", "restart") else ""))
        else:
            p("  threads  : %d checked, none stuck" % len(stuck["list"]))
    if pools["list"]:
        eps = pools["eps"]
        if eps:
            worst = max(eps, key=lambda e: e[2] - e[1])
            ex = pools["list"][worst[0]]
            p("  executors: %d checked, %d SATURATED (pool full, tasks queued); longest %s: %s on %s"
              % (len(pools["list"]), len(set(e[0] for e in eps)), human_dur((worst[2] - worst[1]) / 1000.0), ex[2],
                 members[ex[0]].label))
        else:
            p("  executors: %d checked, none saturated" % len(pools["list"]))
    if transfer:
        kinds = {}
        for pk in transfer["peers"]:
            kinds[pk[0]] = kinds.get(pk[0], 0) + 1
        tin = sum(r[1] for _mi, _st, rows in transfer["totals"] for r in rows)
        tout = sum(r[2] for _mi, _st, rows in transfer["totals"] for r in rows)
        best = max(transfer["links"], key=lambda x: sum(r[1] + r[2] for r in x[4]), default=None)
        p("  transfer : %s; received %s, sent %s%s"
          % ("%d connection(s) with %s" % (transfer["nconns"], ", ".join("%d %s" % (n, {"member": "member(s)", "client": "client host(s)", "wan": "WAN / other cluster address(es)",
                                                           "unknown": "unknown"}.get(k, k)) for k, n in sorted(kinds.items())))
             if transfer["nconns"] else "no per-connection counters, totals only",
             human_size(tin), human_size(tout),
             "; busiest %s <-> %s %s" % (labels[best[0]], transfer["peers"][best[1]][1], human_size(sum(r[1] + r[2] for r in best[4])))
             if best else ""))
    if gcfiles:
        evs = [(col_["m"], k, c_, p_) for col_ in gc["mem"] for k, c_, p_ in zip(col_["k"], col_["c"], col_["p"])]
        hum = sum(1 for e in evs if "Humongous" in gc["causes"][e[2]])
        full = sum(1 for e in evs if gc["kinds"][e[1]].startswith("Pause Full"))
        tosp = sum(1 for col_ in gc["mem"] for f_ in col_["f"] if f_[1] & GC_TOSPACE)
        undo = sum(1 for col_ in gc["mem"] for f_ in col_["f"] if f_[1] & GC_UNDO)
        w = max(evs, key=lambda e: e[3]) if evs else None
        linked = set(gf.member for gf in gcfiles if gf.how != "no member found")
        nfree = sum(1 for gf in gcfiles if gf.how == "no member found")
        p("  gc logs  : %d file(s), %s: %s pause(s)%s; %d humongous allocation GC(s)%s, %d full GC(s), %d evacuation failure(s)"
          % (len(gcfiles), "for %d member(s)%s" % (len(linked), " and %d not linked to any" % nfree if nfree else "") if linked else
             "%d JVM(s) on the uptime axis" % len(gc["extra"]) if uptime_axis else "none linked to a member", format(len(evs), ","),
             ", longest %s ms (%s, %s)" % (format(w[3] / 1000.0, ".1f"), gc["kinds"][w[1]], labels[w[0]]) if w else "", hum,
             " (%d undone: the humongous objects were gone by the pause)" % undo if undo else "", full, tosp))
        if uptime_axis:
            p("             the GC logs have the JVM uptime only and no diagnostics came with them: the time axis is the JVM uptime,"
              " one line per JVM (log with -Xlog:gc*:file=...:time,uptime for the date and time)")
        for gf in gcfiles:
            rel = (gf.lf.reldir + "/" if gf.lf.reldir != "." else "") + gf.lf.name
            if gf.how == "no member found" and gf.member is not None and not uptime_axis:
                p("             %s: no member's files near it; shown as %s" % (rel, labels[gf.member]))
            for n, how, start in gf.placed:
                if start is not None:
                    p("             %s: %s pause(s) with the JVM uptime only, placed with the JVM start %s (%s)"
                      % (rel, format(n, ","), fmt_time(start), "from runtime.uptime" if how.startswith("JVM uptime") else
                         "from the GC counts" if how.startswith("GC counts") else "approximately: the member's start"))
            if gf.unplaced:
                p("             %s: %s pause(s) with the JVM uptime only, not placed in time: %s (log with -Xlog:gc*:file=...:time,uptime)"
                  % (rel, format(gf.unplaced, ","), "they match none of the lifetimes of %s in the diagnostics" % labels[gf.member]
                     if gf.member is not None and gf.member < len(members) else
                     "no member matches the log (folder, host name, GC counts)" if gf.member is None and members else
                     "no member's diagnostics to place them with"))
            if gf.no_time:
                p("             %s: %d line(s) without a date-time, skipped (log with -Xlog:gc*:file=...:time,uptime)" % (rel, gf.no_time))
    if slowops["dumps"]:
        si = slowops["inv"]
        if si:
            w = max(si, key=lambda r: r[3])
            p("  slow ops : %s slow invocation(s) of %d operation(s) on %d member(s); longest %s: %s on %s%s"
              % (format(len(si), ","), len(set(r[1] for r in si)), len(set(r[0] for r in si)), human_dur(w[3] / 1000.0),
                 slowops["ops"][w[1]].rsplit(".", 1)[-1], labels[w[0]], " (still running at its last dump)" if w[7] else ""))
        else:
            p("  slow ops : none in %s SlowOperations section(s)" % format(sum(d[1] for d in slowops["dumps"]), ","))
    if invs["dumps"]:
        per = min([d[5] for d in invs["dumps"] if d[5]] or [0])
        peaks = []
        for icol in invs["ser"]:                # (not col: the member logs' collector, used below)
            tp, ts = inv_totals(icol)
            for key, tot in (("p", tp), ("s", ts)):
                if tot:
                    i = max(range(len(tot)), key=lambda j: tot[j][1])
                    peaks.append((key, tot[i][1], icol["m"], tot[i][0]))
        pk = max((x for x in peaks if x[0] == "p"), key=lambda x: x[1], default=None)
        sk = max((x for x in peaks if x[0] == "s"), key=lambda x: x[1], default=None)
        p("  invocations: %s sampler dump(s) on %d member(s)%s%s%s"
          % (format(sum(d[1] for d in invs["dumps"]), ","), len(invs["dumps"]), " (every %s)" % human_dur(per / 1000.0) if per else "",
             "; at most %s pending at a sample (%s at %s)" % (format(int(round(pk[1])), ","), labels[pk[2]], fmt_time(pk[3])) if pk else "",
             ", %s of them pending longer than the slow threshold (%s at %s)" % (format(int(round(sk[1])), ","), labels[sk[2]], fmt_time(sk[3]))
             if sk and sk[1] > 0 else ""))
        iv = invs["inv"]
        if iv:
            w = max(iv, key=lambda r: r[8])
            late = sum(1 for r in iv if r[12] and r[8] > r[12])
            parts = {}
            for r in iv:
                if r[3] >= 0:
                    parts[r[3]] = parts.get(r[3], 0) + 1
            top = sorted(parts.items(), key=lambda x: -x[1])[:3]
            p("             %s invocation(s) listed as slow%s; longest pending %s: %s%s%s on %s%s%s%s"
              % (format(len(iv), ","), " (the longest %s kept)" % format(len(iv), ",") if invs["capped"] else "", human_dur(w[8] / 1000.0),
                 inv_short(invs["ops"][w[1]]), " " + w[2] if w[2] else "", " partition %d" % w[3] if w[3] >= 0 else "", labels[w[0]],
                 " (still pending at its last dump)" if w[19] == 2 else "",
                 "; %s past their call timeout" % format(late, ",") if late else "",
                 "; most listed: " + ", ".join("partition %d (%s)" % (pt, format(n, ",")) for pt, n in top) if top and top[0][1] > 1 else ""))
        nt = sum(d[4] for d in invs["dumps"])
        if nt:
            p("             %s dump(s) listed only the first 99 slow invocations (hazelcast.diagnostics.invocation.slow.max.count)" % format(nt, ","))
    if store["dumps"]:
        sops = [o.split("\u0001") for o in store["ops"]]
        calls = sum(r[2] for r in store["tot"])
        stime = sum(r[3] for r in store["tot"])
        w = max(store["tot"], key=lambda r: r[4]) if store["tot"] else None
        p("  stores   : %d data structure(s) with a store or loader, %d method(s); %s call(s), %s in them (since the members "
          "started)%s; %s dump(s)"
          % (len(set((o[0], o[1]) for o in sops)), len(sops), format(calls, ","), human_us(stime),
             "; slowest %s: %s %s on %s" % (human_us(w[4]), sops[w[1]][1], sops[w[1]][2], labels[w[0]]) if w else "",
             format(sum(d[1] for d in store["dumps"]), ",")))
    if logs:
        nrec = sum(lm.records for lm in lms)
        nexc = sum(g.n for g in col.excs.values())
        hs = sum(len(lm.health_t) for lm in lms)
        p("  logs     : %s records from %d member(s); %d exception kind(s), %s occurrence(s); %s health monitor sample(s)"
          % (format(nrec, ","), len(lms), len(col.excs), format(nexc, ","), format(hs, ",")))
        if col.oome:
            p("  logs     : %d OutOfMemoryError(s): %s" % (len(col.oome), "; ".join(
                "%s %s %s" % (fmt_time(r[0]), labels[r[1]], r[2] or "") for r in col.oome[:5])))
        notable = {}
        for lm in lms:
            for cat, n in lm.ecount.items():
                if cat not in ("lifecycle", "membership", "oome"):
                    notable[cat] = notable.get(cat, 0) + n
        if notable:
            p("  logs     : " + ", ".join("%s %d" % (LOG_CATS[CAT_INDEX[k]][1].lower(), v)
                                          for k, v in sorted(notable.items(), key=lambda kv: CAT_INDEX[kv[0]])))
        cpn = {}
        for lm in lms:
            for kind, b in lm.cpbins.items():
                cpn[kind] = cpn.get(kind, 0) + sum(v[0] for v in b.d.values())
        if cpn:
            groups = sorted(set(r[2] for r in logs["cp"] if r[2]))
            p("  cp       : %s line(s); %d group(s)%s; %d leader election(s) won, %d session(s) created, %d warning(s) / error(s)"
              % (format(sum(cpn.values()), ","), len(groups), " (" + ", ".join(groups[:6]) + ")" if groups else "",
                 cpn.get("leader", 0), cpn.get("session", 0), sum(1 for r in logs["cp"] if r[5] in ("W", "E"))))
    p("\nWrote %s (%s; read %s in %.1f s, total %.1f s)" % (report, human_size(size), human_size(total), parsed_s, took))
    if size > 150 * 1024 * 1024:
        p("This report is big and may be slow to open: --points 2000, --from/--to or --member make it smaller.")
    if args.open:
        import pathlib
        import webbrowser
        webbrowser.open(pathlib.Path(report).resolve().as_uri())
    return 0


# --------------------------------------------------------------------------- HTML report

HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10); --chip: #f0efec;
  --accent: #2a78d6; --select: rgba(42,120,214,0.12); --gap: rgba(137,135,129,0.13); --event: rgba(82,81,78,0.45);
  --row-sel: rgba(42,120,214,0.09); --stuck: #e34948; --stuck-band: rgba(227,73,72,0.13);
  --warn: #c98500; --logs: #6250d6; --logs-soft: rgba(98,80,214,0.07);
  --tin: #2a78d6; --tout: #eb6834; --msel: rgba(42,120,214,0.10);
  --font: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10); --chip: #262624;
    --accent: #3987e5; --select: rgba(57,135,229,0.18); --gap: rgba(137,135,129,0.14); --event: rgba(195,194,183,0.4);
    --row-sel: rgba(57,135,229,0.14); --stuck: #e66767; --stuck-band: rgba(230,103,103,0.17);
    --warn: #eda100; --logs: #9085e9; --logs-soft: rgba(144,133,233,0.13);
    --tin: #3987e5; --tout: #d95926; --msel: rgba(57,135,229,0.16);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10); --chip: #262624;
  --accent: #3987e5; --select: rgba(57,135,229,0.18); --gap: rgba(137,135,129,0.14); --event: rgba(195,194,183,0.4);
  --row-sel: rgba(57,135,229,0.14); --stuck: #e66767; --stuck-band: rgba(230,103,103,0.17);
  --warn: #eda100; --logs: #9085e9; --logs-soft: rgba(144,133,233,0.13);
  --tin: #3987e5; --tout: #d95926; --msel: rgba(57,135,229,0.16);
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
html, body { background: var(--page); }
body { margin: 0; color: var(--ink); font: 14px/1.45 var(--font); -webkit-text-size-adjust: 100%; }
.wrap { max-width: 1240px; margin: 0 auto; padding: 0 16px 56px; }
header.head { padding: 22px 0 6px; }
.eyebrow { font-size: 12px; color: var(--muted); letter-spacing: .02em; }
h1 { font-size: 22px; line-height: 1.25; font-weight: 600; margin: 2px 0 6px; overflow-wrap: anywhere; }
h1 .title-note { font-size: 14px; font-weight: 400; color: var(--muted); }
h2 { font-size: 16px; font-weight: 600; margin: 26px 0 0; }
h2 + .lead { margin-top: 2px; }
.lead { font-size: 13px; color: var(--ink-2); margin: 6px 0 0; max-width: 860px; }
.sub { margin: 0; color: var(--ink-2); font-size: 13px; overflow-wrap: anywhere; }
.sub span + span::before { content: "\00b7"; margin: 0 7px; color: var(--muted); }
details.files { margin-top: 8px; font-size: 12px; color: var(--ink-2); }
details.files summary { cursor: pointer; color: var(--muted); }
details.files ul { margin: 6px 0 0; padding-left: 18px; max-height: 260px; overflow: auto; }
.top { background: var(--page); z-index: 10; }
@media (min-width: 800px) { .top { position: sticky; top: 0; } }
.tabs { display: flex; gap: 2px; overflow-x: auto; border-bottom: 1px solid var(--border); scrollbar-width: none; }
.tabs::-webkit-scrollbar { display: none; }
@media (min-width: 760px) { .tabs { flex-wrap: wrap; overflow-x: visible; } }   /* every tab in sight; phones scroll them */
.tabs button { font: inherit; font-size: 13.5px; background: none; border: 0; border-bottom: 2px solid transparent;
  padding: 9px 11px 7px; color: var(--ink-2); cursor: pointer; white-space: nowrap; }
.tabs button:hover { color: var(--ink); }
.tabs button[aria-selected="true"] { color: var(--ink); border-bottom-color: var(--accent); font-weight: 600; }
.tabs .cnt { color: var(--muted); font-weight: 400; font-size: 12px; margin-left: 5px; font-variant-numeric: tabular-nums; }
.tabs .cnt.warn { color: var(--stuck); font-weight: 600; }
.tabs.logs button[aria-selected="true"] { border-bottom-color: var(--logs); }
.srcbar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; padding: 2px 0 8px; }
.srcbar .ctl-label { margin-right: 2px; }
.srcbar button { font: inherit; font-size: 13px; border: 1px solid var(--border); background: var(--surface); color: var(--ink-2);
  border-radius: 999px; padding: 4px 13px; cursor: pointer; }
.srcbar button:hover { color: var(--ink); }
.srcbar button[aria-pressed="true"] { background: var(--ink); border-color: var(--ink); color: var(--surface); font-weight: 600; }
.srcbar button.logs[aria-pressed="true"] { background: var(--logs); border-color: var(--logs); color: #fff; }
.srcbar button:disabled { opacity: .45; cursor: default; }
.srcbar button:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.srcbar .src-n { font-weight: 400; opacity: .8; font-size: 12px; margin-left: 5px; }
.srcbanner { margin: 14px 0 0; padding: 8px 12px; border-left: 3px solid var(--logs); background: var(--logs-soft);
  border-radius: 0 8px 8px 0; font-size: 13px; color: var(--ink-2); overflow-wrap: anywhere; }
.srcbanner b { color: var(--ink); }
.kpi.alert { border-color: var(--stuck); }
.card.alert { border-color: var(--stuck); box-shadow: inset 3px 0 0 var(--stuck); }
.logsec .card:not(.alert) { box-shadow: inset 3px 0 0 var(--logs); }
pre.sample { font-family: var(--mono); font-size: 11.5px; line-height: 1.5; background: var(--chip); color: var(--ink);
  border-radius: 8px; padding: 10px 12px; margin: 8px 0 0; overflow: auto; max-height: 380px; white-space: pre; }
.lvl-E { color: var(--stuck); font-weight: 600; }
.lvl-W { color: var(--warn); font-weight: 600; }
td.msg { white-space: normal; overflow-wrap: anywhere; min-width: 280px; font-size: 12px; }
.toolbar { border-bottom: 1px solid var(--border); padding: 8px 0; display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; }
.ctxbar { border-bottom: 1px solid var(--border); padding: 7px 0; }
.ctxbar .tabctl { margin: 0; }
.ctxbar .ctl { min-width: 0; max-width: 100%; }
.ctxbar .ctxrow select { max-width: min(300px, calc(100vw - 110px)); }
.ctxbar .ctxrow select.opsel { max-width: min(380px, calc(100vw - 110px)); }
.ctl { display: inline-flex; align-items: center; gap: 8px; }
.ctl-label { font-size: 12px; color: var(--muted); }
.ctl.chk { gap: 6px; font-size: 13px; color: var(--ink-2); cursor: pointer; }
.ctl.chk input { margin: 0; accent-color: var(--accent, #2a78d6); }
.seg { display: inline-flex; background: var(--chip); border-radius: 8px; padding: 2px; flex-wrap: wrap; }
.seg button { font: inherit; font-size: 12.5px; border: 0; border-radius: 6px; background: transparent;
  color: var(--ink-2); padding: 4px 10px; cursor: pointer; }
.seg button[aria-pressed="true"] { background: var(--surface); color: var(--ink); font-weight: 600; box-shadow: 0 0 0 1px var(--border); }
select, input[type="search"] { font: inherit; font-size: 12.5px; color: var(--ink); background: var(--surface);
  border: 1px solid var(--border); border-radius: 7px; padding: 4px 8px; max-width: 260px; }
input[type="search"] { width: 200px; }
.seg button:focus-visible, .btn:focus-visible, select:focus-visible, input:focus-visible, th button:focus-visible, .mbar input:focus-visible,
.tabs button:focus-visible, tr.click:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.range { font-size: 12.5px; color: var(--ink-2); display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.range b { color: var(--ink); font-weight: 600; font-variant-numeric: tabular-nums; }
.btn { font: inherit; font-size: 12.5px; border: 1px solid var(--border); background: var(--surface); color: var(--ink);
  border-radius: 7px; padding: 3px 10px; cursor: pointer; }
.btn:disabled { opacity: .45; cursor: default; }
.btn-s { font-size: 11.5px; padding: 1px 8px; color: var(--ink-2); }
.btn[aria-pressed="true"] { background: var(--chip); color: var(--ink); }
.hint { font-size: 12px; color: var(--muted); }
.spacer { flex: 1 1 auto; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 14px 0 0; }
.kpi { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; min-width: 0; }
.kpi-label { font-size: 12px; color: var(--ink-2); }
.kpi-value { font-size: 24px; font-weight: 600; line-height: 1.25; margin-top: 2px; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.kpi-sub { font-size: 12px; color: var(--muted); margin-top: 2px; overflow-wrap: anywhere; }
.kpi-value.bad { color: var(--stuck); }
.kpi.link { cursor: pointer; }
.kpi.link:hover { border-color: var(--axis); }
.kpi.link:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; margin: 14px 0; }
.cap { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 2px 12px; }
.cap-l { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 10px; min-width: 0; }
.cap-r { display: flex; align-items: baseline; gap: 10px; margin-left: auto; }
.cap h3 { font-size: 14px; font-weight: 600; margin: 0; }
.cap .note { font-size: 12px; color: var(--muted); }
code, .mname { font-family: var(--mono); font-size: 11.5px; color: var(--muted); overflow-wrap: anywhere; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 8px 0 2px; align-items: center; font-size: 12px; color: var(--ink-2); }
.lg { display: inline-flex; align-items: center; gap: 6px; background: none; border: 0; padding: 0; font: inherit; color: inherit; cursor: pointer; max-width: 100%; }
.lg span:last-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 320px; }
.lg.off { opacity: .4; }
.lg-static { cursor: default; }
.key { width: 14px; height: 2px; border-radius: 1px; flex: none; display: inline-block; }
.sw { width: 10px; height: 10px; border-radius: 3px; flex: none; display: inline-block; }
.lg-note { font-size: 11.5px; color: var(--muted); }
.key-oome { height: 0; border-top: 2px dashed var(--stuck); border-radius: 0; }
.chart { position: relative; margin-top: 6px; }
svg.plot { display: block; width: 100%; touch-action: pan-y; -webkit-user-select: none; user-select: none; }
.plot text { font-family: var(--font); }
.plot .tick { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.plot .tick-day { fill: var(--ink-2); }
.plot .row-label { fill: var(--ink-2); font-size: 11.5px; }
.plot .row-head { fill: var(--ink); font-size: 12px; font-weight: 600; }
.plot .end-label { fill: var(--ink-2); font-size: 11px; }
.plot .gridline { stroke: var(--grid); stroke-width: 1; shape-rendering: crispEdges; }
.plot .axis-line { stroke: var(--axis); stroke-width: 1; shape-rendering: crispEdges; }
.plot .cover { stroke: var(--axis); stroke-width: 1; shape-rendering: crispEdges; }
.plot .gap { fill: var(--gap); }
.plot .evline { stroke: var(--event); stroke-width: 1; shape-rendering: crispEdges; }
.plot .ev-label { fill: var(--muted); font-size: 10.5px; paint-order: stroke; stroke: var(--surface); stroke-width: 3px; stroke-linejoin: round; }
.plot .cross { stroke: var(--muted); stroke-width: 1; shape-rendering: crispEdges; pointer-events: none; }
.plot .brush { fill: var(--select); stroke: var(--accent); stroke-width: 1; pointer-events: none; }
.plot .brush.measuring { stroke-dasharray: 4 3; }
.plot .msel { fill: var(--msel); }
.plot .msel-edge { stroke: var(--accent); stroke-width: 1; stroke-dasharray: 3 3; shape-rendering: crispEdges; }
.plot .hit { fill: transparent; cursor: crosshair; }
.plot .ln { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.plot .ln-faint { fill: none; stroke-width: 1; stroke-linejoin: round; stroke-linecap: round; }
.plot .ln-sel { fill: none; stroke-width: 3; stroke-linejoin: round; stroke-linecap: round; }
.plot .dot { stroke: var(--surface); stroke-width: 2; }
.plot .dot-hot { stroke: var(--ink); stroke-width: 1.5; pointer-events: none; fill: none; }
.plot .hot { stroke: var(--surface); stroke-width: 2; pointer-events: none; }
.plot .empty { fill: var(--muted); font-size: 13px; paint-order: stroke; stroke: var(--surface); stroke-width: 8px; stroke-linejoin: round; }
.plot .band { fill: var(--stuck-band); }
.plot .stuckbar { fill: var(--stuck); }
.plot .bar-hot { fill: none; stroke: var(--ink); stroke-width: 1.5; pointer-events: none; }
.plot .row-hl { fill: var(--row-sel); }
.plot .bar-label { fill: var(--ink); font-size: 10px; font-weight: 600; paint-order: stroke; stroke: var(--surface); stroke-width: 2.5px;
  stroke-linejoin: round; pointer-events: none; }
.plot .oomeline { stroke: var(--stuck); stroke-width: 1.5; stroke-dasharray: 4 3; shape-rendering: crispEdges; }
.plot .oome-label { fill: var(--stuck); font-size: 10.5px; font-weight: 600; paint-order: stroke; stroke: var(--surface); stroke-width: 3px; stroke-linejoin: round; }
.plot.clickable .hit { cursor: pointer; }
.plot .gcdot { stroke: var(--surface); stroke-width: 1; }
.plot .gcfail { stroke: var(--stuck); stroke-width: 1; stroke-dasharray: 1 3; shape-rendering: crispEdges; }
.plot .gcx { fill: none; stroke: var(--stuck); stroke-width: 2.5; stroke-linecap: round; paint-order: stroke; }
.gcx-key { color: var(--stuck); font-weight: 700; font-size: 12px; line-height: 1; }
.sw.sw-dot { border-radius: 50%; }
.lg-static.off { opacity: .35; }
.kpi.hl { box-shadow: inset 3px 0 0 var(--hl); }
.tip-foot.bad { color: var(--stuck); font-weight: 600; }
.sw-band { background: var(--stuck-band); border: 1px solid var(--stuck); }
.more { margin-top: 8px; font-size: 12.5px; color: var(--ink-2); }
.measure { margin-top: 8px; padding-top: 7px; border-top: 1px dashed var(--border); font-size: 12.5px; color: var(--ink-2); }
.measure .mhead { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; margin-bottom: 4px; }
.measure .mhead b { color: var(--accent); }
.measure .mrows { display: flex; flex-wrap: wrap; gap: 3px 16px; }
.measure .mi { display: inline-flex; align-items: center; gap: 6px; min-width: 0; }
.measure .mi .ml { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 260px; }
.measure .mi b { color: var(--ink); font-variant-numeric: tabular-nums; white-space: nowrap; }
.measure .mtot { border-left: 1px solid var(--border); padding-left: 12px; }
.mbar { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 12.5px; color: var(--ink-2); }
.mbar input { font: inherit; font-size: 12.5px; color: var(--ink); background: var(--surface); border: 1px solid var(--border);
  border-radius: 7px; padding: 3px 7px; width: 112px; font-variant-numeric: tabular-nums; }
.mbar input[aria-invalid="true"] { border-color: var(--stuck); }
.mbar .mdur { color: var(--muted); }
td .tbar { height: 4px; border-radius: 2px; margin: 3px 0 0 auto; }
td .tbar.in { background: var(--tin); }
td .tbar.out { background: var(--tout); }
table.trm td, table.trm th { white-space: nowrap; }
.tip { position: fixed; z-index: 30; pointer-events: none; display: none; background: var(--surface); color: var(--ink);
  border: 1px solid var(--border); border-radius: 10px; box-shadow: 0 6px 24px rgba(0,0,0,.16);
  padding: 8px 10px; font-size: 12px; min-width: 200px; max-width: 380px; }
.tip-t { font-weight: 600; overflow-wrap: anywhere; }
.tip-sub { color: var(--muted); margin: 1px 0 5px; overflow-wrap: anywhere; }
.tip-row { display: grid; grid-template-columns: 14px auto 1fr; align-items: center; gap: 7px; line-height: 1.6; }
.tip-row b { font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
.tip-lbl { color: var(--ink-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tip-foot { color: var(--ink-2); margin-top: 4px; overflow-wrap: anywhere; }
.table-wrap { overflow-x: auto; }
.twin { margin-top: 10px; max-height: 420px; overflow: auto; }
.table-wrap.tall { max-height: 520px; overflow: auto; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
th, td { padding: 6px 8px; text-align: left; border-bottom: 1px solid var(--grid); white-space: nowrap; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
td.wide { white-space: normal; overflow-wrap: anywhere; min-width: 220px; }
td.wrapcell { white-space: normal; overflow-wrap: anywhere; min-width: 150px; }
th { font-weight: 600; color: var(--ink-2); font-size: 12px; position: sticky; top: 0; background: var(--surface); }
th button { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
th[aria-sort] button { color: var(--ink); }
tr.click { cursor: pointer; }
tr.click:hover td { background: var(--chip); }
tr.sel td { background: var(--row-sel); }
td.op { font-weight: 600; }
td.op .ptag { font-weight: 400; font-size: 11px; color: var(--muted); margin-left: 6px; }
td.op .meth { font-weight: 400; color: var(--ink-2); }
td .share { color: var(--muted); font-size: 11px; margin-left: 6px; }
svg.hist { display: block; }
svg.hist .hist-tick { fill: var(--axis); }
td .pkg { font-weight: 400; color: var(--muted); }
.chips { display: inline-flex; flex-wrap: wrap; gap: 2px 10px; }
.chip { display: inline-flex; align-items: center; gap: 5px; font-variant-numeric: tabular-nums; }
.chip span { color: var(--muted); }
.empty-note { color: var(--muted); font-size: 13px; padding: 6px 0; }
dl.kv { display: grid; grid-template-columns: minmax(90px, max-content) 1fr; gap: 4px 14px; font-size: 13px; margin: 12px 0; }
dl.kv dt { color: var(--ink-2); }
dl.kv dd { margin: 0; overflow-wrap: anywhere; }
.selbar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; font-size: 13px; color: var(--ink-2); margin: 10px 0 0; }
.selbar b { color: var(--ink); overflow-wrap: anywhere; }
.tabctl { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin: 14px 0 0; }
.latctl .ctl { flex-wrap: wrap; gap: 6px 8px; }
.latctl select.opsel { max-width: min(340px, 100%); }
th button .sw { margin-right: 5px; vertical-align: -1px; }
pre.sample.wrappre { white-space: pre-wrap; overflow-wrap: anywhere; }
.tabctl select.opsel { max-width: min(380px, 100%); }
.ctl-label + pre.sample { margin-top: 4px; }
details.sect { margin: 14px 0; }
details.sect > summary { cursor: pointer; font-size: 14px; font-weight: 600; }
details.sect p { color: var(--ink-2); font-size: 13px; margin: 8px 0; }
.list-inline { font-size: 12.5px; color: var(--ink-2); overflow-wrap: anywhere; }
.notes { font-size: 13px; color: var(--ink-2); }
.notes h3 { font-size: 14px; color: var(--ink); margin: 0 0 6px; }
.notes li { margin: 5px 0; }
.notes code { font-size: 12px; }
.loading { padding: 60px 0; color: var(--muted); }
@media (max-width: 640px) {
  h1 { font-size: 19px; }
  .card { padding: 12px 12px; }
  .hint { display: none; }
  .kpi-value { font-size: 20px; }
  .kpis { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
  input[type="search"] { width: 160px; }
}
</style>
</head>
<body>
<div class="wrap">
  <header class="head">
    <div class="eyebrow">Hazelcast diagnostics</div>
    <h1 id="title">Reading the report data…</h1>
    <p class="sub" id="subtitle"></p>
    <details class="files" id="files-box" hidden><summary id="members-sum"></summary><ul id="members-list"></ul></details>
  </header>
  <div class="top" id="top" hidden>
    <nav class="tabs" id="tabs" role="tablist" aria-label="Report sections"></nav>
    <div class="toolbar" id="toolbar"></div>
    <div class="ctxbar" id="ctxbar" hidden></div>
  </div>
  <main id="main"></main>
  <section class="card notes" id="notes" hidden>
    <h3>How this works</h3>
    <ul>
      <li><b>Files and members.</b> Every file under the input folder that holds diagnostics sections is read
        (<code>.log</code>, <code>.gz</code>, …). A member is its address (<code>host:port</code>, from the file name or
        <code>thisAddress</code>); the start time in the file names separates its lifetimes (JVM runs), so counters
        never mix across a restart. The same file found twice is read once.</li>
      <li><b>Times</b> <span id="tz-note"></span>. Drag across any chart to zoom; every chart, table and number follows
        the range. Double-click a chart or press <i>Zoom out</i> to go back. Shaded areas have no data.</li>
      <li><b>Metrics</b> come from the MetricsPlugin dumps (every minute by default). Counters, which only grow
        (<code>map.getCount</code>, <code>tcp.bytesReceived</code>, <code>gc.minorTime</code>, …), are shown as a rate per
        second between two dumps of the same lifetime; a drop (restart) is skipped, not drawn as a negative rate.
        Everything else (sizes, used heap, pending counts) is a gauge, shown as the highest value in each time bin.
        The logs don't say which is which: names with words like <i>size</i>, <i>pending</i>, <i>used</i>,
        <i>max</i> are gauges; any other metric that never went down is a counter.</li>
      <li><b>Data structures</b>: maps, multimaps, replicated maps, caches, queues, topics, reliable topics,
        executors (<code>executor.*</code> with a name; the internal pools, <code>executor.internal.*</code>, are on the
        Operations tab), flake ID generators, PN counters… one kind at a time: a metric chart, a table of every
        structure of that kind (rates are averages per second and sizes the highest, in the time range in view) and,
        for the one you click, every metric it has. Lists and sets only report times (creation, last access), which
        are not charted. The <b>CP Subsystem</b> tab does the same for the CP objects (AtomicLong, FencedLock,
        semaphore, CountDownLatch, CPMap, CP sessions), named <code>name@group</code>.</li>
      <li><b>Cleaning</b>: for each metric of a data structure, a structure is left out when
        every value it had, on every member, is the same as its first one. Per-thread, per-pipeline and other per-item
        series that never changed are left out the same way (all-zero pipelines, for instance). Metrics that never changed are listed in the <i>Unchanged</i> table of their tab instead of drawn as
        flat lines.</li>
      <li><b>Lines</b>: <i>By member</i> draws one line per member (for per-map or per-thread metrics, the sum over all
        of them, or the max for percentages and maxima); <i>By name / By operation</i> draws one line per
        item, summed over the selected members. Colours follow the member, or the item's rank over the whole
        capture, so they don't change when you zoom or filter; items past the eighth are grey.</li>
      <li><b>Latency</b> comes from OperationsProfiler (time an operation ran on that member) and InvocationProfiler
        (end-to-end time of the invocations that member sent). Their counters are cumulative since the member
        started, so the first dump of each lifetime is a baseline and only the growth after it is counted. The
        profilers count calls in buckets that double in size (powers of 2 µs), so every boundary is one of 1.02 ms,
        2.05 ms, 4.1 ms, 8.19 ms … 524 ms, 1.05 s, 2.1 s, 4.19 s, 8.39 s: 1.05 s and 2.1 s are the nearest to 1 s
        and 2 s. Each bucket from 1.02 ms up is kept for every interval, so the groups can be changed in the report:
        the fastest group (default &lt; 4.1 ms), the slowest (default ≥ 524 ms) and the <i>Slow from</i> threshold
        (default 131 ms), which is always a group boundary; the boundaries in between are taken from 1.02, 4.1,
        32.8, 131 and 524 ms, 1.05 and 2.1 s (at most six groups; five by default).
        Groups faster than the threshold are blue, the slow ones red. The <i>Operation</i> list picks one operation
        (it lists those that Find and the filters leave); a row of the table picks one row. A max is exact
        when the member's all-time max went up in that interval, otherwise an upper bound (≤). In the charts, an
        interval that spans two time slots is shared between them by overlap (so a slot can show 0.4 calls); the
        tables count each interval once, by its end.</li>
      <li><b>WAN</b> collects the <code>wan.*</code> metrics and the WAN traffic (<code>tcp.*.WAN</code>), and the
        latency of the WAN operations (classes in a <code>wan</code> package or named <code>Wan…</code>). The
        <i>WAN publisher</i> list keeps, in every chart and number of the tab, only the items of one publisher
        (<code>publisherId</code> and <code>replication</code> tags: its queue, its targets, its maps); the WAN bytes
        and the WAN operations are not per publisher.
        <b>CP Subsystem</b> collects the <code>raft.*</code> and <code>cp.*</code> metrics and the CP operations
        (<code>cp</code> or <code>raft</code> packages). Those operations stay on the Latency tab as well.</li>
      <li><b>Slow operations</b> come from the SlowOperations sections of the slow operation detector (every minute):
        each operation that ran on an operation thread for longer than its threshold (10 s by default), with the
        stack trace it was caught in, how many such invocations it counted, and the invocations it still keeps:
        start, duration so far, operation details. It lists them again at every dump for an hour after they last
        ran, so the report keeps each invocation once (by operation, start and identity hash), with the longest
        duration seen; one that ended less than 3 s before its member's last dump is marked still running. The end of
        an invocation is its start plus its duration.</li>
      <li><b>Invocations</b> come from the Invocations sections of the invocation sampler, written every
        <code>hazelcast.diagnostics.invocation.sample.period.seconds</code> when it is above 0. At each sample it goes
        through every invocation the member is waiting on (from the call until the response and the backup
        acknowledgements arrive) and adds one per operation to <i>History</i>, and to <i>SlowHistory</i> when it has
        been pending for longer than <code>hazelcast.diagnostics.invocation.slow.threshold.seconds</code> (5 s by
        default). Both count since the member started, so the growth from one dump to the next is the number pending
        at that sample; the first dump of each lifetime is the baseline, and a gap of several periods (a file
        missing) is shared out evenly. The slow ones are also listed with their details (<i>Pending</i>), the first
        <code>hazelcast.diagnostics.invocation.slow.max.count</code> - 1 (99) of them: an invocation listed at several
        dumps (its call id changes when it is retried) is shown once, from its first invocation to the last dump that
        listed it, with the longest duration seen. It is still pending at the member's last dump, or gone by the next
        dump, or not known when that dump's list was cut at 99. Past its call timeout, an invocation waits on because
        the target keeps answering its heartbeats: the operation is still queued or running there. The partition
        thread of a partition is <code>partition-operation.thread-</code> (partition id modulo
        <code>operation.partitionThreadCount</code> of the target).</li>
      <li><b>MapStore</b> comes from the MapStoreLatency sections (and QueueStoreLatency and the like), written when
        <code>hazelcast.diagnostics.storeLatency.period.seconds</code> is above 0: for each map and each method of its
        MapStore or MapLoader (load, loadAll, loadAllKeys, store, storeAll, delete, deleteAll), the same counters as
        the profilers: calls, total and max time, calls per power-of-2 µs bucket. They are cumulative since the member
        started, so the charts use the growth between two dumps, and the table <i>Since the members started</i> has
        the counters themselves. With <code>hazelcast.diagnostics.storeLatency.reset.period.seconds</code> the counters
        are reset after some dumps: a dump with a count, a total, a max or a bucket lower than at the dump before
        counts as reset, everything in it is new, and the totals keep what was counted before. The latency groups and
        the slow threshold are the Latency tab's.</li>
      <li><b>GC logs</b> found under the input folder are read too: JDK 9+ unified logging (<code>-Xlog:gc*</code>) and
        JDK 8 <code>-XX:+PrintGCDetails</code>. A line with a date-time (the <code>time</code> decoration, e.g.
        <code>-Xlog:gc*:file=gc.log:time,uptime</code>, or <code>-XX:+PrintGCDateStamps</code>) is placed by it. A line
        with the JVM uptime only (the default decorations of <code>-Xlog</code>: uptime, level, tags) is placed with
        the JVM start of its member: in the diagnostics, the dump time minus <code>runtime.uptime</code> (without that
        metric, the lifetime's start in the file names, refined with the GC counts). Each JVM run of the log (at a
        restart the uptime starts again from 0) goes to the member's lifetime whose <code>gc.minorCount</code> lines up
        with its young pauses or, with no counts over it, whose diagnostics it overlaps; two runs that both start from 0
        can't be one lifetime. A run that matches no lifetime isn't placed: the GC log files table counts its pauses.
        With GC logs alone and the uptime only, the time axis is the JVM uptime (0 is each JVM's start) and each JVM
        has a line of its own.
        A GC log goes with the member whose diagnostics or member logs are in the same folder; else with the member whose
        host name (and port) is in the GC log's file name, then in its folder names (<code>gc_logs_uklpapscl101a/gc.log</code>);
        else with the one in the nearest folder, and when several are as near, the one whose lifetime started with that JVM
        (wall time minus uptime). A GC log that matches no member gets a line of its own on the GC tab, but it isn't
        counted as a member. Rotated files and copies are read once (by JVM run and GC id). Each pause is
        a dot on the GC tab, coloured by its kind: young and mixed, remark / cleanup / other, and the ones caused by a
        humongous allocation; full GCs and evacuation failures (to-space exhausted) are marked with a red line and cross.
        The heap is the one printed on the pause line (before → after (committed)); the time in pauses is the pauses' sum
        over the time the member has a GC log.</li>
      <li><b>Stuck threads</b> are checked at every metrics dump, for every thread of every member: a thread is stuck
        when it stays idle while its queue stays above 0, at two dumps in a row at least. For an IO thread
        (<code>tcp.inputThread</code>, <code>tcp.outputThread</code>): tasks wait in its queue
        (<code>taskQueueSize</code> &gt; 0) and it did no I/O since the dump before (<code>idleTimeMillis</code> at least
        90% of the time since that dump). For a partition or generic operation thread: operations wait for it
        (<code>normalPendingCount</code> + <code>priorityPendingCount</code> &gt; 0) and it completed none since the dump
        before (<code>completedTotalCount</code> unchanged). An episode starts at the thread's last I/O (from
        <code>idleTimeMillis</code>), or at the dump before when nothing was waiting there yet, and ends at the last dump
        with work waiting, so its end is only as precise as the dump period. Threads that are idle with an empty queue,
        or busy with a queue, are not stuck.</li>
      <li><b>Transfer</b>: each connection has its own byte counters in the metrics (<code>tcp.connection.in.bytesRead</code>,
        <code>tcp.connection.out.bytesWritten</code>, named <code>local address-&gt;remote address</code>). A member's
        connection is matched to another member when that member has the same connection going out (same ports); a
        connection a member opened to an address outside the cluster goes to another cluster (WAN replication). The others
        are clients, by host; their type (Java, C++, .NET, Python, Management Center…) and WAN replication coming in from
        another cluster are known when the metrics include <code>tcp.connection.connectionType</code> (DEBUG level). The
        member totals come from the IO threads (<code>tcp.inputThread</code> / <code>outputThread.bytesTransceived</code>),
        which also count the connections that opened and closed between two dumps (<i>not seen as a connection</i>). A
        counter's first dump in a lifetime is its baseline, and growth across a gap in the dumps is left out.</li>
      <li><b>Measure</b>: pick <i>Drag to: Measure</i> in the toolbar (or hold Shift while dragging), then drag across any
        chart, or type the times; every chart then shows what went through that span: the bytes for byte counters (rates
        added up over time), the count for other counters and the profilers, the average and max for sizes. The Transfer
        tab's amounts follow the span. Escape or <i>Clear</i> removes it. Amounts are as precise as the dumps: at the
        edges of the span, a dump interval counts for the part inside.</li>
      <li><b>Saturated executors</b> (Operations tab) are checked at every metrics dump, for every executor with a
        <code>queueSize</code>, a <code>poolSize</code> and a <code>maximumPoolSize</code> (<code>executor.internal.*</code>
        and the like): saturated when its pool is full (<code>poolSize</code> ≥ <code>maximumPoolSize</code>) and tasks
        wait in its queue (<code>queueSize</code> &gt; 0), at two dumps in a row at least.</li>
      <li><b>Member logs.</b> The members' own logs found in the input folder (<code>hazelcast.log</code>, an
        application's log with the Hazelcast lines in it, console output, rotated and <code>.gz</code> files) are read
        too: java.util.logging, log4j, log4j2 and logback layouts, a date and time at the start of the line or after a
        level or a prefix, the month in letters, JSON lines (logstash, ECS, log4j2), or the time of day alone with the
        date in the file name; terminal colours are removed. A file is a member log when its lines have a date we know
        and it has a Hazelcast line (<code>[host]:port [cluster] [version]</code>) somewhere in its first 512 MB, so a
        partial log, without the member's start or shutdown, is read too. The files that are not read are listed with
        the reason in <i>Members and files</i> and in the console. A log belongs to the member whose address its lines
        print; a member with logs but no diagnostics gets its own entry. Their tabs (<i>Source: Member logs</i>, in
        purple) chart the HealthMonitor lines, every exception grouped by class and root cause, OutOfMemoryError (also
        marked on every chart of both sources), the lines that matter (partitions lost:
        <code>partitionId=N is completely lost!</code>; WAN replication problems: the event queue full and events
        dropped, failures, warnings and errors of the WAN classes; thread starvation and clock leaps, system clock
        jumps, suspected members, quorum and split-brain protection failures, split brain and merges, slow and delayed
        operations, closed connections, lifecycle and membership: an application's lines in the same log count for
        OutOfMemoryError, thread starvation and clock jumps only, the other kinds and the migrations come from
        Hazelcast's own lines, the ones with its <code>[host]:port [cluster] [version]</code> prefix or a Hazelcast
        logger), the migrations and the CP Subsystem: groups, roles,
        terms, elections, sessions, persistence. Lost partitions and WAN problems are also on the Overview, and WAN
        problems on the WAN tab. Times are the ones printed in the logs (converted when a line has a zone offset), so
        they line up with the diagnostics when both were written in the same time zone.</li>
      <li><b>Migrations</b> come from the master's log lines: <code>Repartitioning cluster data. Migration tasks
        count: N</code> starts a plan, <code>Remaining migration tasks</code> follows it, and <code>All migration tasks
        have been completed</code> or <code>Rebalance process was failed</code> (or aborted) ends it, all with the
        plan's <code>repartitionTime</code>, which keeps the plans apart (a plan whose first line is not in the logs
        starts at its repartitionTime). Lost partitions, failed promotions and migrations, and changes to the partition
        table are listed by kind.</li>
      <li><b>Configuration</b> comes from the BuildInfo, SystemProperties and ConfigProperties sections each member
        writes at the top of each diagnostics file. A member with several lifetimes shows its last one, and the values
        its earlier lifetimes had are marked. The JVM arguments are read from <code>java.vm.args</code>; heap and GC
        from them (<code>-Xms</code>, <code>-Xmx</code>, <code>-XX:+UseG1GC</code>...).</li>
      <li id="limits-note"></li>
    </ul>
    <p class="hint" id="gen-note"></p>
  </section>
</div>
<div class="tip" id="tip" role="tooltip"></div>
<script id="hz-data" type="application/octet-stream" data-enc="__ENC__">__DATA__</script>
<script>
(async () => {
"use strict";
const $ = id => document.getElementById(id);

async function loadData() {
  const node = $("hz-data"), enc = node.getAttribute("data-enc");
  if (enc === "json") return JSON.parse(node.textContent);
  if (typeof DecompressionStream === "undefined")
    throw new Error("This browser cannot unpack the embedded data (it needs DecompressionStream: Chrome/Edge 80+, Firefox 113+, Safari 16.4+).");
  const b64 = node.textContent.trim(), bin = atob(b64), bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return JSON.parse(await new Response(stream).text());
}
let D;
try { D = await loadData(); }
catch (e) { $("title").textContent = "Could not read the report data"; $("subtitle").textContent = String((e && e.message) || e); return; }
$("hz-data").textContent = "";

const META = D.meta, MEMBERS = D.members, NM = MEMBERS.length, NDIAG = MEMBERS.filter(m => !m.logsOnly).length;
// short member labels for tables and row labels: without the domain every host name shares (.example.com)
const MSHORT = (() => {
  const hp = MEMBERS.map(m => { const i = m.label.lastIndexOf(":"); return m.gcOnly || i <= 0 ? null : [m.label.slice(0, i), m.label.slice(i)]; });
  const hosts = hp.filter(x => x).map(x => x[0]);
  let suf = null;
  for (const h of hosts) {
    if (/^[\d.]+$/.test(h) || /^\[/.test(h) || h.indexOf(".") < 0) { suf = ""; break; }
    const d = h.slice(h.indexOf("."));
    if (suf == null) suf = d;
    while (suf && !(h.length > suf.length && h.slice(h.length - suf.length) === suf)) { const j = suf.indexOf(".", 1); suf = j < 0 ? "" : suf.slice(j); }
  }
  return MEMBERS.map((m, i) => suf && hp[i] && hp[i][0].length > suf.length ? hp[i][0].slice(0, hp[i][0].length - suf.length) + hp[i][1] : m.label);
})();
const mShort = m => MSHORT[m];
const G = D.g || { t0: META.range[0], step: 60000, n: 0 };
const OPS = D.ops, EF = META.efactor || 1, PT0 = META.pt0 || 0, PSTEP = META.pstep || 5000;
const X_MIN = META.range[0], X_MAX = Math.max(META.range[1], META.range[0] + 60000);

/* ---------- colour: fixed categorical order (follows the member or the item, never the rank in view) */
const CAT = { light: ["#2a78d6","#eb6834","#1baf7a","#eda100","#e87ba4","#008300","#6250d6","#e34948"],
              dark:  ["#3987e5","#d95926","#199e70","#c98500","#d55181","#008300","#9085e9","#e66767"] };
const GREY = { light: "#b5b4ac", dark: "#5b5a56" };
const BLUE = {100:"#cde2fb",150:"#b7d3f6",200:"#9ec5f4",250:"#86b6ef",300:"#6da7ec",350:"#5598e7",
  400:"#3987e5",450:"#2a78d6",500:"#256abf",550:"#1c5cab",600:"#184f95",650:"#104281",700:"#0d366b"};
// the red of the diverging pair, at the lightness of each step of the blue ramp
const RED = {250:"#f1968e",300:"#ed7f78",350:"#e66963",400:"#e14c4a",500:"#ba3334",550:"#a6272a",600:"#902123",650:"#7d171a",700:"#671215",200:"#f7aba4"};
// latency groups: blue below the slow threshold, red from it; on each side the further a group is from the
// threshold, the stronger its colour. Steps by the number of groups on that side, nearest the threshold first
// (each side checked as an ordinal ramp on both surfaces; dark mode: darker = nearer the surface = weaker).
const LAT_ARM = { light: { 1: [600], 2: [300, 600], 3: [300, 500, 700], 4: [250, 400, 550, 700], 5: [250, 350, 500, 600, 700] },
  dark: { 1: [400], 2: [550, 300], 3: [600, 400, 250], 4: [600, 500, 400, 300], 5: [600, 500, 400, 300, 200] } };
const root = document.documentElement;
const mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
const isDark = () => { const t = root.getAttribute("data-theme"); return t ? t === "dark" : !!(mq && mq.matches); };
const scheme = () => isDark() ? "dark" : "light";
const catColor = i => (i >= 0 && i < 8) ? CAT[scheme()][i] : GREY[scheme()];
const memberColor = m => catColor(m);
const grey = () => GREY[scheme()];
/* ---------- latency groups. The profiler buckets double in size (powers of 2 microseconds): a boundary b is
   2^b us. The reader picks the fastest group's top, the slowest group's start and the slow threshold, which is
   always a boundary too; the groups in between follow from those (at most six groups). */
const LAT_FAST = [10, 11, 12, 13, 14, 15, 16], LAT_SLOWEST = [17, 18, 19, 20, 21, 22, 23];
const LAT_ALL = LAT_FAST.concat(LAT_SLOWEST), LAT_FILL = [10, 12, 15, 17, 19, 20, 21];
const edgeLabel = b => fmtUs(Math.pow(2, b));
function rangeLabel(a, b) {
  const x = edgeLabel(a), y = edgeLabel(b), ux = x.slice(x.indexOf(" ")), uy = y.slice(y.indexOf(" "));
  return (ux === uy ? x.slice(0, x.indexOf(" ")) : x) + "–" + y;
}
let LG = null;
function latGroups() {
  const f = state.lat.f, s = state.lat.s, t = state.lat.t, key = f + "|" + s + "|" + t;
  if (LG && LG.key === key) return LG;
  const edges = [f, s];
  if (t > f && t < s) edges.push(t);
  edges.sort((a, b) => a - b);
  while (edges.length < 5) {          // fill in from the usual boundaries, splitting the widest gaps first
    let best = null;
    for (const c of LAT_FILL) {
      if (c <= f || c >= s || edges.indexOf(c) >= 0) continue;
      let lo = f, hi = s;
      for (const e of edges) { if (e < c && e > lo) lo = e; if (e > c && e < hi) hi = e; }
      const sc = Math.min(c - lo, hi - c), gap = hi - lo, slow = c > t ? 1 : 0;     // a tie: the slow side first
      if (!best || sc > best[1] || (sc === best[1] && (gap > best[2] || (gap === best[2] && slow > best[3])))) best = [c, sc, gap, slow];
    }
    if (!best) break;
    edges.push(best[0]);
    edges.sort((a, b) => a - b);
  }
  const G = edges.length + 1, tg = edges.indexOf(t) + 1;
  const labels = [];
  for (let g = 0; g < G; g++) labels.push(g === 0 ? "< " + edgeLabel(edges[0]) : g === G - 1 ? "≥ " + edgeLabel(edges[G - 2]) : rangeLabel(edges[g - 1], edges[g]));
  const gof = new Int8Array(64);            // bucket exponent -> group
  for (let e = 0; e < 64; e++) { let g = 0; for (const b of edges) if (b < e) g++; gof[e] = g; }
  LG = { key, edges, G, tg, labels, gof, slowLabel: "≥ " + edgeLabel(t), rows: false };
  return LG;
}
function latColor(g) {
  const L = latGroups(), sc = scheme();
  if (g < L.tg) return BLUE[LAT_ARM[sc][L.tg][L.tg - 1 - g]];
  return RED[LAT_ARM[sc][L.G - L.tg][g - L.tg]];
}

/* ---------- formatting */
const pad2 = n => (n < 10 ? "0" : "") + n;
const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function sig3(v) {
  if (v === 0) return "0";
  const a = Math.abs(v);
  let s = a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : a >= 1 ? v.toFixed(2) : v.toPrecision(2);
  if (s.indexOf(".") >= 0 && s.indexOf("e") < 0) s = s.replace(/0+$/, "").replace(/\.$/, "");
  return s;
}
function compact(v) {
  const a = Math.abs(v);
  if (a >= 1e12) return sig3(v / 1e12) + "T";
  if (a >= 1e9) return sig3(v / 1e9) + "B";
  if (a >= 1e6) return sig3(v / 1e6) + "M";
  if (a >= 1e3) return sig3(v / 1e3) + "K";
  return sig3(v);
}
const fmtInt = v => Math.round(v).toLocaleString("en-US");
function fmtNum(v) { const a = Math.abs(v); if (a >= 1e6) return compact(v); if (Number.isInteger(v)) return fmtInt(v); return sig3(v); }
const KB = 1024, MB = KB * 1024, GB = MB * 1024, TB = GB * 1024;
function fmtBytes(v) {
  const a = Math.abs(v);
  if (a >= TB) return sig3(v / TB) + " TB";
  if (a >= GB) return sig3(v / GB) + " GB";
  if (a >= MB) return sig3(v / MB) + " MB";
  if (a >= KB) return sig3(v / KB) + " KB";
  return sig3(v) + " B";
}
function fmtUs(us) {
  if (us == null || !isFinite(us)) return "–";
  if (Math.abs(us) < 1000) return sig3(us) + " µs";
  if (Math.abs(us) < 1e6) return sig3(us / 1000) + " ms";
  if (Math.abs(us) < 6e7) return sig3(us / 1e6) + " s";
  return dur(us / 1e6);
}
const fmtMs = ms => fmtUs(ms * 1000);
function fmtPct(p) {
  if (p == null || !isFinite(p)) return "–";
  if (p === 0) return "0%";
  if (Math.abs(p) < 0.01) return "<0.01%";
  return (Math.abs(p) >= 10 ? p.toFixed(1) : Math.abs(p) >= 1 ? p.toFixed(2) : p.toFixed(3)).replace(/\.?0+$/, "") + "%";
}
// value of a metric in its unit; rate: true or "/s", "/min", "/h" for a counter
function fmtVal(v, u, rate) {
  if (v == null || !isFinite(v)) return "–";
  const r = rate === true ? "/s" : (rate || "");
  if (u === "bytes") return fmtBytes(v) + r;
  if (u === "ms") return r ? sig3(v) + " ms" + r : fmtMs(v);
  if (u === "us") return r ? sig3(v) + " µs" + r : fmtUs(v);
  if (u === "ns") return fmtUs(v / 1000) + r;
  if (u === "percent") return sig3(v) + "%";
  if (u === "pct") return fmtPct(v);
  if (u === "s") return sig3(v) + " s";
  return fmtNum(v) + r;
}
const BNAME = { 1: "B", 1024: "KB", 1048576: "MB", 1073741824: "GB", 1099511627776: "TB" };
function tickVal(v, u, rate, s) {
  if (v === 0) return "0";
  if (u === "bytes") return s ? sig3(v / s) + " " + BNAME[s] : fmtBytes(v);
  if (u === "ms" && !rate) return fmtMs(v);
  if (u === "us" && !rate) return fmtUs(v);
  if (u === "ns" && !rate) return fmtUs(v / 1000);
  if (u === "percent" || u === "pct") return sig3(v) + "%";
  if (u === "s") return sig3(v) + " s";
  return compact(v);
}
// GC logs alone with the JVM uptime only: times are uptimes (3d 06:06:54), 0 being each JVM's start
const UPTIME = !!META.uptime;
function clock(ms, sec) { const d = new Date(ms); return pad2(d.getUTCHours()) + ":" + pad2(d.getUTCMinutes()) + (sec ? ":" + pad2(d.getUTCSeconds()) : ""); }
function upTime(ms) { const d = Math.floor(ms / 86400000); return (d ? d + "d " : "") + clock(ms, true); }
function dayLabel(ms) { if (UPTIME) return Math.floor(ms / 86400000) + "d"; const d = new Date(ms); return pad2(d.getUTCDate()) + " " + MON[d.getUTCMonth()]; }
function logTime(ms) { if (UPTIME) return upTime(ms); const d = new Date(ms); return pad2(d.getUTCDate()) + "-" + pad2(d.getUTCMonth() + 1) + "-" + d.getUTCFullYear() + " " + clock(ms, true); }
function dur(sec) {
  sec = Math.round(sec);
  if (sec < 60) return sec + " s";
  let m = Math.floor(sec / 60); const s = sec % 60;
  if (m < 60) return m + "m " + pad2(s) + "s";
  let h = Math.floor(m / 60); m %= 60;
  if (h < 24) return h + "h " + pad2(m) + "m";
  const d = Math.floor(h / 24); h %= 24;
  return d + "d " + pad2(h) + "h";
}
const MULTI_DAY = Math.floor(X_MIN / 86400000) !== Math.floor(X_MAX / 86400000);
function shortTime(ms) {
  if (ms == null) return "–";
  if (UPTIME) return upTime(ms);
  if (!MULTI_DAY) return clock(ms, true);
  const d = new Date(ms);
  return pad2(d.getUTCDate()) + "-" + pad2(d.getUTCMonth() + 1) + " " + clock(ms, true);
}
function span(a, b) {
  if (UPTIME) return upTime(a) + " → " + upTime(b);
  return Math.floor(a / 86400000) === Math.floor(b / 86400000) ? logTime(a) + " → " + clock(b, true) : logTime(a) + " → " + logTime(b);
}
function stepName(ms) { return ms % 3600000 === 0 ? (ms / 3600000) + " h" : ms % 60000 === 0 ? (ms / 60000) + " min" : (ms / 1000) + " s"; }
function shortOp(op) { const i = op.lastIndexOf("."); return i >= 0 && i < op.length - 1 ? op.slice(i + 1) : op; }

/* ---------- DOM: text from the logs only ever goes in through textContent */
const SVGNS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs, parent) {
  const e = document.createElementNS(SVGNS, tag);
  if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
function el(tag, cls, parent, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}
function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); }
function lowerBound(a, x) { let lo = 0, hi = a.length; while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] < x) lo = m + 1; else hi = m; } return lo; }
function upperBound(a, x) { let lo = 0, hi = a.length; while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] <= x) lo = m + 1; else hi = m; } return lo; }

/* ---------- state */
const TABS = [["overview", "Overview"], ["latency", "Latency"], ["slowops", "Slow operations"], ["operations", "Operations"], ["threads", "Threads"],
  ["network", "Network"], ["transfer", "Transfer"], ["wan", "WAN"], ["maps", "Data structures"], ["system", "System"], ["cluster", "Cluster"], ["cp", "CP Subsystem"],
  ["other", "Other"]];
const LOGTABS = [["logs", "Overview"], ["logs-health", "Health monitor"], ["logs-exceptions", "Exceptions"], ["logs-migrations", "Migrations"],
  ["logs-cp", "CP Subsystem"]];
const HASLOGS = !!(D.logs && D.logs.info && D.logs.info.length);
const HASDIAG = MEMBERS.some(m => !m.logsOnly);
const GCD = D.gc || { kinds: [], causes: [], mem: [], files: [], extra: [] };
const HASGC = GCD.files.length > 0;
const ST = D.store || { ops: [], tot: [], dumps: [], idle: [] };
const HASSTORE = ST.dumps.length > 0;
const IV = D.inv || { ops: [], ser: [], tot: [], inv: [], raw: [], dumps: [], capped: false };
const HASINV = IV.dumps.length > 0;
const PR = D.props || { secs: [], keys: [], vals: [], rows: [], lives: [], changes: [] };
const HASPROPS = PR.rows.length > 0;
if (HASGC && HASDIAG) TABS.splice(TABS.findIndex(t => t[0] === "system") + 1, 0, ["gc", "GC"]);
if (HASSTORE) TABS.splice(TABS.findIndex(t => t[0] === "maps") + 1, 0, ["mapstore", "MapStore"]);
if (HASINV) TABS.splice(TABS.findIndex(t => t[0] === "slowops") + 1, 0, ["invocations", "Invocations"]);
if (HASPROPS) TABS.splice(TABS.findIndex(t => t[0] === "other"), 0, ["config", "Configuration"]);
const ALLTABS = TABS.concat(HASLOGS ? LOGTABS : []).concat(HASGC && !HASDIAG ? [["gc", "GC"]] : []);
const DEFTAB = HASDIAG ? "overview" : HASLOGS ? "logs" : "gc";
const groupOf = id => id === "logs" || id.indexOf("logs-") === 0 || (id === "gc" && !HASDIAG && HASLOGS) ? "logs" : id === "gc" && !HASDIAG ? "gc" : "diag";
const LOG_OOME = HASLOGS ? D.logs.oome : [];
const LOGX = HASLOGS ? D.logs.exc : [];
const LOGCP = HASLOGS ? D.logs.cp : [];
const state = { tab: "overview", member: "all", prof: "both", group: "member", q: "", x0: X_MIN, x1: X_MAX,
  log: true, mapMetric: null, mapSel: null, mapSort: { key: null, dir: -1 },
  evKind: "all", thSel: null, thSort: { key: "a", dir: 1 }, last: {},
  logCat: "all", excSel: null, excSort: { key: "n", dir: -1 }, cpKind: "all", satSort: { key: "a", dir: 1 },
  dragMode: "zoom", msel: null, trKind: "all", trSel: null, trSort: { key: "tot", dir: -1 }, wanPub: null, cpGroup: null,
  lat: { f: 12, s: 19, t: 17 } };      // latency groups: fastest < 4.1 ms, slowest >= 524 ms, slow from 131 ms
const selMembers = () => state.member === "all" ? MEMBERS.map((m, i) => i) : [+state.member];
const selSet = () => new Set(selMembers());
const findQ = () => state.q.trim().toLowerCase();

/* ---------- scales and axes */
function xScale(x0, x1, L, R) { const k = (R - L) / Math.max(1, x1 - x0); const f = x => L + (x - x0) * k; f.inv = X => x0 + (X - L) / k; return f; }
function niceStep(raw) { const p = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p; }
function linTicks(lo, hi, count, unit, intOnly) {
  let s = 1;
  if (unit === "bytes") { const a = Math.max(Math.abs(lo), Math.abs(hi)); s = a >= TB ? TB : a >= GB ? GB : a >= MB ? MB : a >= KB ? KB : 1; }
  const make = s => {
    const l = lo / s, h = hi / s > l ? hi / s : l + 1;
    let step = niceStep((h - l) / Math.max(1, count));
    if (intOnly && (step < 1 || h - l <= 6)) step = 1;
    if (unit === "ms" && (h - l) / Math.max(1, count) >= 1000) step = TSTEPS.find(x => x >= (h - l) / Math.max(1, count)) || step;   // whole seconds, minutes, hours
    const a = Math.floor(l / step + 1e-9) * step, b = Math.ceil(h / step - 1e-9) * step, ticks = [];
    for (let v = a; v <= b + step * 1e-6; v += step) ticks.push(+(v * s).toPrecision(12));
    return { ticks, min: a * s, max: b * s, s };
  };
  let T = make(s);
  if (unit === "bytes" && s < TB && T.max / s >= 1024) T = make(s * 1024);
  return T;
}
const TSTEPS = [1,2,5,10,15,30,60,120,300,600,900,1800,3600,7200,10800,21600,43200,86400,172800,604800].map(s => s * 1000);
function timeTicks(x0, x1, maxN) {
  const sp = Math.max(1, x1 - x0);
  let step = TSTEPS[TSTEPS.length - 1];
  for (const s of TSTEPS) if (sp / s <= maxN) { step = s; break; }
  const ticks = [];
  for (let t = Math.ceil(x0 / step) * step; t <= x1; t += step) ticks.push(t);
  return { ticks, step };
}
let uid = 0;
function frame(ch, H, M) {
  const W = ch.width, svg = ch.svg;
  clear(svg);
  svg.setAttribute("width", W); svg.setAttribute("height", H); svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  ch.W = W; ch.H = H; ch.M = M; ch.top = M.t; ch.bottom = H - M.b;
  ch.sx = xScale(state.x0, state.x1, M.l, W - M.r);
  const id = "clip" + (++uid);
  const cp = svgEl("clipPath", { id }, svgEl("defs", null, svg));
  svgEl("rect", { x: M.l - 4, y: 0, width: Math.max(0, W - M.l - M.r + 8), height: H - M.b + 2 }, cp);
  ch.g = { grid: svgEl("g", null, svg), events: svgEl("g", { "clip-path": "url(#" + id + ")" }, svg),
    marks: svgEl("g", { "clip-path": "url(#" + id + ")" }, svg), axes: svgEl("g", null, svg), over: svgEl("g", null, svg) };
  const y = ch.bottom;
  svgEl("line", { x1: M.l, x2: W - M.r, y1: y + 0.5, y2: y + 0.5, class: "axis-line" }, ch.g.axes);
  const T = timeTicks(state.x0, state.x1, Math.max(2, Math.floor((W - M.l - M.r) / (W < 640 ? 62 : 96))));
  let prevDay = null;
  for (const t of T.ticks) {
    const X = Math.round(ch.sx(t)) + 0.5;
    svgEl("line", { x1: X, x2: X, y1: y, y2: y + 4, class: "axis-line" }, ch.g.axes);
    svgEl("text", { x: X, y: y + 16, class: "tick", "text-anchor": "middle" }, ch.g.axes).textContent = clock(t, T.step < 60000);
    const day = Math.floor(t / 86400000);
    if (day !== prevDay) svgEl("text", { x: X, y: y + 30, class: "tick tick-day", "text-anchor": "middle" }, ch.g.axes).textContent = dayLabel(t);
    prevDay = day;
  }
}
function finish(ch) {
  const g = ch.g.over;
  ch.cross = svgEl("line", { x1: 0, x2: 0, y1: ch.top, y2: ch.bottom, class: "cross", visibility: "hidden" }, g);
  ch.hot = svgEl("g", null, g);
  ch.brush = svgEl("rect", { x: 0, y: ch.top, width: 0, height: ch.bottom - ch.top, class: "brush", visibility: "hidden" }, g);
  svgEl("rect", { x: ch.M.l, y: ch.top, width: Math.max(0, ch.W - ch.M.l - ch.M.r), height: ch.bottom - ch.top, class: "hit" }, g);
}
function emptyNote(ch, text) {
  svgEl("text", { x: (ch.M.l + ch.W - ch.M.r) / 2, y: (ch.top + ch.bottom) / 2 + 4, class: "empty", "text-anchor": "middle" }, ch.g.marks).textContent = text;
}
// shade the time with no data from the selected members
function unionCover(idx, get) {
  const spans = [];
  for (const i of idx) for (const c of (get ? get(i) : MEMBERS[i].cover)) spans.push(c.slice());
  spans.sort((a, b) => a[0] - b[0]);
  const out = [];
  for (const s of spans) { if (out.length && s[0] <= out[out.length - 1][1] + 90000) out[out.length - 1][1] = Math.max(out[out.length - 1][1], s[1]); else out.push(s); }
  return out;
}
function shadeGaps(ch, covIn, label) {
  const cov = covIn || unionCover(selMembers());
  let prev = state.x0;
  const shade = (a, b) => {
    if (b - a < 60000) return;
    const A = ch.sx(Math.max(a, state.x0)), B = ch.sx(Math.min(b, state.x1));
    if (B - A < 1) return;
    svgEl("rect", { x: A, y: ch.top, width: B - A, height: ch.bottom - ch.top, class: "gap" }, ch.g.events);
    if (B - A > 46 + (label ? 30 : 0)) svgEl("text", { x: (A + B) / 2, y: ch.top + 11, class: "ev-label", "text-anchor": "middle" }, ch.g.events).textContent = label || "no data";
  };
  for (const c of cov) { if (c[1] < state.x0 || c[0] > state.x1) { if (c[1] < state.x0) prev = Math.max(prev, c[1]); continue; } shade(prev, c[0]); prev = Math.max(prev, c[1]); }
  shade(prev, state.x1);
}
// the measured span, on every time chart
function drawMeasureBand(ch) {
  if (!state.msel) return;
  const [a, b] = state.msel;
  if (b < state.x0 || a > state.x1) return;
  const A = ch.sx(Math.max(a, state.x0)), B = ch.sx(Math.min(b, state.x1));
  svgEl("rect", { x: A.toFixed(1), y: ch.top, width: Math.max(1, B - A).toFixed(1), height: ch.bottom - ch.top, class: "msel" }, ch.g.events);
  for (const t of [a, b]) if (t >= state.x0 && t <= state.x1) {
    const X = Math.round(ch.sx(t)) + 0.5;
    svgEl("line", { x1: X, x2: X, y1: ch.top, y2: ch.bottom, class: "msel-edge" }, ch.g.events);
  }
}
// membership changes, drawn on every time chart
const MARKERS = (() => {
  const out = [];
  for (const e of D.events) {
    if (e[2] !== "MemberAdded" && e[2] !== "MemberRemoved") continue;
    const last = out.find(o => o.kind === e[2] && o.who === e[4] && Math.abs(o.t - e[0]) < 60000);
    if (last) { last.seen.add(e[1]); continue; }
    out.push({ t: e[0], kind: e[2], who: e[4], size: e[5], seen: new Set([e[1]]) });
  }
  return out;
})();
function drawMarkers(ch, labels) {
  drawMeasureBand(ch);
  const used = [[], []];
  for (const mk of MARKERS) {
    if (mk.t < state.x0 || mk.t > state.x1) continue;
    const X = Math.round(ch.sx(mk.t)) + 0.5;
    svgEl("line", { x1: X, x2: X, y1: ch.top, y2: ch.bottom, class: "evline" }, ch.g.events);
    if (!labels) continue;
    const text = (mk.kind === "MemberAdded" ? "+ " : "− ") + mk.who, w = text.length * 6.1;
    const right = X + 4 + w <= ch.W - ch.M.r, a = right ? X + 4 : X - 4 - w, b = a + w;
    const row = [0, 1].find(r => used[r].every(u => b + 6 <= u[0] || a - 6 >= u[1]));
    if (row == null) continue;
    used[row].push([a, b]);
    svgEl("text", { x: right ? X + 4 : X - 4, y: ch.top + 10 + row * 13, class: "ev-label", "text-anchor": right ? "start" : "end" }, ch.g.events).textContent = text;
  }
  // OutOfMemoryError from the member logs: on every time chart (the lane charts say it in their legend)
  if (LOG_OOME.length) {
    const ss = selSet();
    let lastX = -1e9;
    for (const o of LOG_OOME) {
      if (o[0] < state.x0 || o[0] > state.x1 || !ss.has(o[1])) continue;
      const X = Math.round(ch.sx(o[0])) + 0.5;
      svgEl("line", { x1: X, x2: X, y1: ch.top, y2: ch.bottom, class: "oomeline" }, ch.g.events);
      if (labels !== false && X - lastX > 40 && X + 38 <= ch.W - ch.M.r) {
        svgEl("text", { x: X + 3, y: ch.bottom - 5, class: "oome-label" }, ch.g.events).textContent = "OOME";
        lastX = X;
      }
    }
  }
}

// the legend entry of those lines, when one is in view
function oomeLegend(host) {
  const ss = selSet();
  if (!LOG_OOME.some(o => ss.has(o[1]) && o[0] >= state.x0 && o[0] <= state.x1)) return false;
  const it = el("span", "lg lg-static", host);
  el("span", "key key-oome", it);
  el("span", null, it, "OutOfMemoryError (member logs)");
  return true;
}

/* ---------- hover, tooltip, zoom */
const tip = $("tip");
function tipRow(box, color, value, label, line) {
  const row = el("div", "tip-row", box), k = el("span", line === false ? "sw" : "key", row);
  if (color) k.style.background = color;
  el("b", null, row, value); el("span", "tip-lbl", row, label);
}
function showTip(build, x, y) {
  clear(tip);
  build(tip);
  tip.style.display = "block";
  const w = tip.offsetWidth, h = tip.offsetHeight, pad = 14;
  let X = x + pad, Y = y + pad;
  if (X + w > window.innerWidth - 8) X = x - pad - w;
  if (Y + h > window.innerHeight - 8) Y = window.innerHeight - 8 - h;
  tip.style.left = Math.max(8, X) + "px"; tip.style.top = Math.max(8, Y) + "px";
}
function hideTip() { tip.style.display = "none"; }
let panels = [];
function setCross(t) {
  for (const c of panels) {
    if (!c.cross || c.tab !== state.tab) continue;
    if (t == null || t < state.x0 || t > state.x1) { c.cross.setAttribute("visibility", "hidden"); continue; }
    const X = Math.round(c.sx(t)) + 0.5;
    c.cross.setAttribute("x1", X); c.cross.setAttribute("x2", X); c.cross.setAttribute("visibility", "visible");
  }
}
function clearHover() { hideTip(); setCross(null); for (const c of panels) if (c.hot) clear(c.hot); }
function setView(a, b) {
  if (b - a < 30000) { const m = (a + b) / 2; a = m - 15000; b = m + 15000; }
  state.x0 = Math.max(X_MIN, a); state.x1 = Math.min(X_MAX, b);
  if (state.x1 <= state.x0) { state.x0 = X_MIN; state.x1 = X_MAX; }
  update();
}
function resetView() { state.x0 = X_MIN; state.x1 = X_MAX; update(); }
// a measured span: every chart shows what went through it (amounts for rates and counts, average and max otherwise)
function setMeasure(a, b) {
  a = Math.max(X_MIN, Math.round(a / 1000) * 1000); b = Math.min(X_MAX, Math.round(b / 1000) * 1000);
  state.msel = b > a ? [a, b] : null;
  update();
}
function clearMeasure() { state.msel = null; update(); }
function bindChart(ch) {
  const svg = ch.svg;
  let drag = null;
  const loc = e => { const r = svg.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  const clampX = x => Math.max(ch.M.l, Math.min(ch.W - ch.M.r, x));
  svg.addEventListener("pointerdown", e => {
    if (e.button !== 0 || !ch.sx || ch.noZoom) return;
    const p = loc(e);
    if (p[0] < ch.M.l || p[0] > ch.W - ch.M.r) return;
    drag = { x0: p[0], moved: false, measure: (state.dragMode === "measure") !== !!e.shiftKey };
    try { svg.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
  });
  svg.addEventListener("pointermove", e => {
    if (!ch.sx) return;
    const p = loc(e), x = clampX(p[0]);
    if (drag) {
      if (Math.abs(x - drag.x0) > 3) drag.moved = true;
      if (drag.moved) {
        ch.brush.setAttribute("x", Math.min(x, drag.x0)); ch.brush.setAttribute("width", Math.abs(x - drag.x0)); ch.brush.setAttribute("visibility", "visible");
        ch.brush.setAttribute("class", drag.measure ? "brush measuring" : "brush");
      }
    }
    const inside = p[0] >= ch.M.l && p[0] <= ch.W - ch.M.r && p[1] >= ch.top && p[1] <= ch.bottom;
    setCross(inside && !ch.noCross ? ch.sx.inv(x) : null);
    if (ch.hot) clear(ch.hot);
    const build = inside && !(drag && drag.moved) && ch.tipAt ? ch.tipAt(ch.sx.inv(x), p[1], p[0]) : null;
    if (build) showTip(build, e.clientX, e.clientY); else hideTip();
  });
  svg.addEventListener("pointerup", e => {
    if (!drag) return;
    const d = drag, x = clampX(loc(e)[0]);
    drag = null;
    if (ch.brush) ch.brush.setAttribute("visibility", "hidden");
    if (d.moved && Math.abs(x - d.x0) > 6) {
      ch.zoomedAt = Date.now();
      const a = ch.sx.inv(Math.min(x, d.x0)), b = ch.sx.inv(Math.max(x, d.x0));
      if (d.measure) setMeasure(a, b); else setView(a, b);
    }
  });
  // a click that was not the end of a drag-to-zoom (charts with rows to pick)
  svg.addEventListener("click", e => { if (ch.onClick && Date.now() - (ch.zoomedAt || 0) > 300) ch.onClick(loc(e)); });
  svg.addEventListener("pointercancel", () => { drag = null; if (ch.brush) ch.brush.setAttribute("visibility", "hidden"); });
  svg.addEventListener("pointerleave", () => { if (!drag) clearHover(); });
  svg.addEventListener("dblclick", () => resetView());
}

/* ---------- Measure: what went through a span of time, on each chart */
// spec.measure: "rate" (per-second rates of a counter: integrated over the span), "sum" (amounts per bin: added up),
// "gauge" (sizes: average and max), "none", or a function (a, b) -> [{label, color, text}]; inferred when not given.
// A series can carry amount(a, b): the exact amount from its source, when the chart only holds the part in view.
function measureKind(spec) {
  if (spec.measure) return typeof spec.measure === "function" ? "fn" : spec.measure;
  if (spec.kind === "stack") return "sum";
  if ((spec.rate === true || spec.rate === "/s") && spec.unit !== "percent" && spec.unit !== "pct") return "rate";
  return "gauge";
}
function measureSeries(s, a, b, kind) {
  if (s.amount && kind !== "gauge") return { amount: s.amount(a, b) };
  let sum = 0, n = 0, mx = -Infinity, lo = Infinity, seen = false;
  if (s.v) {
    const i0 = Math.max(0, Math.floor((a - s.t0) / s.step)), i1 = Math.min(s.v.length - 1, Math.floor((b - s.t0) / s.step));
    for (let i = i0; i <= i1; i++) {
      const x = s.v[i];
      if (x !== x) continue;
      const t = s.t0 + i * s.step, ov = Math.min(b, t + s.step) - Math.max(a, t);
      if (ov <= 0) continue;
      seen = true;
      if (kind === "rate") sum += x * ov / 1000;
      else if (kind === "sum") sum += x * ov / s.step;
      else { sum += x; n++; if (x > mx) mx = x; if (x < lo) lo = x; }
    }
  } else if (s.pts) {
    let prev = null;
    for (const q of s.pts) {
      if (kind === "rate") {
        if (prev && q[0] - prev[0] <= (s.join || 600000)) {
          const ov = Math.min(b, q[0]) - Math.max(a, prev[0]);
          if (ov > 0) { sum += q[1] * ov / 1000; seen = true; }
        }
      } else if (q[0] >= a && q[0] <= b) {
        seen = true;
        sum += q[1];
        if (kind !== "sum") { n++; if (q[1] > mx) mx = q[1]; if (q[1] < lo) lo = q[1]; }
      }
      prev = q;
    }
  } else if (s.runs) {
    for (const r of s.runs) {
      if (r[1] < a || r[0] > b) continue;
      seen = true; sum += r[2]; n++;
      if (r[2] > mx) mx = r[2]; if (r[2] < lo) lo = r[2];
    }
    if (kind !== "gauge") kind = "gauge";
  }
  if (!seen) return null;
  return kind === "gauge" ? { avg: sum / n, max: mx, min: lo, runs: !!s.runs } : { amount: sum };
}
function fmtAmount(v, unit) {
  if (unit === "bytes") return fmtBytes(v);
  if (unit === "ms") return fmtMs(v);
  if (unit === "us") return fmtUs(v);
  if (unit === "ns") return fmtUs(v / 1000);
  if (unit === "s") return dur(v);
  return fmtCount(v);
}
function measureReadout(p, spec, orig) {
  const box = p.mbox;
  if (!box) return;
  clear(box);
  const sel = state.msel, kind = measureKind(spec);
  if (!sel || kind === "none") { box.hidden = true; return; }
  const [a, b] = sel;
  box.hidden = false;
  const head = el("div", "mhead", box);
  el("b", null, head, "Measured");
  head.appendChild(document.createTextNode(" " + span(a, b) + " · " + dur((b - a) / 1000)));
  if (a < state.x0 || b > state.x1) head.appendChild(document.createTextNode(" (part of it is out of view)"));
  const zb = el("button", "btn btn-s", head, "Zoom to it"); zb.type = "button";
  zb.addEventListener("click", () => setView(a - (b - a) * 0.1, b + (b - a) * 0.1));
  const cb = el("button", "btn btn-s", head, "Clear"); cb.type = "button";
  cb.addEventListener("click", clearMeasure);
  const list = el("div", "mrows", box);
  let rows = [];
  if (kind === "fn") rows = spec.measure(a, b) || [];
  else {
    const fmtG = x => (spec.fmt ? spec.fmt(x) : fmtVal(x, spec.unit, ""));
    let total = 0;
    for (const s of orig) {
      if (p.off.has(s.key) || s.key === "ref" || /^x:/.test(s.key || "")) continue;
      const r = measureSeries(s, a, b, kind);
      if (!r) continue;
      if (r.amount != null) { total += r.amount; rows.push({ label: s.label, color: s.color, num: r.amount, text: fmtAmount(r.amount, spec.unit) }); }
      else rows.push({ label: s.label, color: s.color, num: r.max, text: r.runs ? (r.min === r.max ? fmtG(r.max) : fmtG(r.min) + " to " + fmtG(r.max)) : "avg " + fmtG(r.avg) + " · max " + fmtG(r.max) });
    }
    if (!spec.ordered) rows.sort((x, y) => y.num - x.num);       // ordered: series in an order of their own (latency groups)
    if (rows.length > 1 && kind !== "gauge") rows.total = fmtAmount(total, spec.unit);
  }
  if (!rows.length) { el("span", "hint", list, "No data in this span."); return; }
  for (const r of rows.slice(0, 12)) {
    const it = el("span", "mi", list), sw = el("span", "sw", it);
    sw.style.background = r.color || grey();
    el("span", "ml", it, r.label);
    el("b", null, it, r.text);
  }
  if (rows.length > 12) el("span", "mi hint", list, "+ " + (rows.length - 12) + " more");
  if (rows.total) { const it = el("span", "mi mtot", list); el("span", "ml", it, "Total"); el("b", null, it, rows.total); }
}
// "15:10", "15:10:30", "27-10 15:10", "27-10-2026 15:10:30" (display time), the date from ref when not given
function parseWhen(text, ref) {
  if (UPTIME) {                                   // "06:10", "3d 06:10:30": uptime, the day of ref when not given
    const u = /^\s*(?:(\d+)\s*d\s*)?(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$/.exec(text || "");
    return u ? (u[1] != null ? +u[1] : Math.floor(ref / 86400000)) * 86400000 + ((+u[2] * 60 + +u[3]) * 60 + (u[4] ? +u[4] : 0)) * 1000 : null;
  }
  const m = /^\s*(?:(\d{1,2})[-/.](\d{1,2})(?:[-/.](\d{4}))?\s+)?(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$/.exec(text || "");
  if (!m) return null;
  const r = new Date(ref);
  return Date.UTC(m[3] ? +m[3] : r.getUTCFullYear(), m[2] ? +m[2] - 1 : r.getUTCMonth(), m[1] ? +m[1] : r.getUTCDate(), +m[4], +m[5], m[6] ? +m[6] : 0);
}

/* ---------- panels: one card per chart, drawn when visible and only when something changed */
const io = "IntersectionObserver" in window ? new IntersectionObserver(es => {
  for (const e of es) {
    const p = e.target._panel;
    if (!p) continue;
    p.inView = e.isIntersecting;
    if (p.inView && p.dirty) renderPanel(p);
  }
}, { rootMargin: "500px 0px" }) : null;
function addPanel(tab, parent, o) {
  const card = el("section", "card panel", parent);
  const cap = el("div", "cap", card), left = el("div", "cap-l", cap);
  const h3 = el("h3", null, left, o.title);
  if (o.code) el("code", "mname", left, o.code);
  const right = el("div", "cap-r", cap), note = el("span", "note", right);
  const p = { tab, card, h3, note, o, dirty: true, inView: !io, off: new Set(), showTable: false };
  if (!o.noTable) {
    const tb = el("button", "btn btn-s", right, "Table");
    tb.type = "button"; tb.setAttribute("aria-pressed", "false");
    tb.title = "Show the values of this chart as a table";
    tb.addEventListener("click", () => { p.showTable = !p.showTable; tb.setAttribute("aria-pressed", String(p.showTable)); p.dirty = true; renderPanel(p); });
  }
  p.legend = el("div", "legend", card);
  p.host = el("div", "chart", card);
  p.host.style.minHeight = (o.height || 210) + "px";
  p.svg = svgEl("svg", { class: "plot", role: "img" }, p.host);
  p.svg.setAttribute("aria-label", (o.aria || o.title) + ". The Table button lists the values.");
  p.mbox = el("div", "measure", card); p.mbox.hidden = true;
  p.foot = el("div", "more", card); p.foot.hidden = true;
  p.twin = el("div", "table-wrap twin", card); p.twin.hidden = true;
  card._panel = p;
  bindChart(p);
  panels.push(p);
  if (io) io.observe(card);
  return p;
}
function dropPanels(pred) {
  panels = panels.filter(p => { if (pred(p)) { if (io) io.unobserve(p.card); p.card.remove(); return false; } return true; });
}
function renderPanel(p) {
  if (state.tab !== p.tab || p.card.hidden) return;
  p.dirty = false;
  p.width = Math.max(280, p.host.clientWidth || 600);
  try { p.o.render(p); }
  catch (e) { console.error(e); clear(p.svg); }
}
function renderVisible() { for (const p of panels) if (p.tab === state.tab && p.dirty && p.inView && !p.card.hidden) renderPanel(p); }
function invalidate() { for (const p of panels) p.dirty = true; renderVisible(); }

/* ---------- series: grid series {t0, step, v}, step runs {runs}, points {pts} */
function toSeries(a, f) {
  const src = a[2], v = new Float64Array(src.length);
  for (let i = 0; i < src.length; i++) { const x = src[i]; v[i] = x == null ? NaN : x; }
  return { m: a[0], o: a[1], f, t0: G.t0 + a[1] * G.step * f, step: G.step * f, v };
}
function combine(rows, agg) {
  if (rows.length === 1) return rows[0];
  let lo = Infinity, hi = -Infinity;
  for (const r of rows) { lo = Math.min(lo, r.o); hi = Math.max(hi, r.o + r.v.length); }
  const v = new Float64Array(hi - lo).fill(NaN);
  for (const r of rows) {
    for (let i = 0; i < r.v.length; i++) {
      const x = r.v[i];
      if (x !== x) continue;
      const j = r.o - lo + i, c = v[j];
      v[j] = c !== c ? x : agg === "max" ? Math.max(c, x) : agg === "min" ? Math.min(c, x) : c + x;
    }
  }
  const f = rows[0].f;
  return { m: -1, o: lo, f, t0: G.t0 + lo * G.step * f, step: G.step * f, v };
}
function gridRange(s, x0, x1) {
  return [Math.max(0, Math.floor((x0 - s.t0) / s.step)), Math.min(s.v.length - 1, Math.floor((x1 - s.t0) / s.step))];
}
function valueAt(s, t, tol) {
  if (s.v) {
    const i = Math.floor((t - s.t0) / s.step);
    if (i < 0 || i >= s.v.length) return null;
    const x = s.v[i];
    return x === x ? { v: x, a: s.t0 + i * s.step, b: s.t0 + (i + 1) * s.step, i } : null;
  }
  if (s.runs) {
    let best = null;
    for (const r of s.runs) { if (r[0] <= t && t <= r[1] + 90000) best = r; if (r[0] > t) break; }
    return best ? { v: best[2], a: best[0], b: best[1], run: best } : null;
  }
  if (s.pts) {
    let best = null, bd = tol;
    for (const p of s.pts) { const d = Math.abs(p[0] - t); if (d <= bd) { bd = d; best = p; } }
    return best ? { v: best[1], a: best[0], b: best[0], pt: best } : null;
  }
  return null;
}
function seriesExtent(s, x0, x1) {
  let lo = Infinity, hi = -Infinity, pos = Infinity, nonInt = false;
  const take = x => { if (x < lo) lo = x; if (x > hi) hi = x; if (x > 0 && x < pos) pos = x; if (x % 1 !== 0) nonInt = true; };
  if (s.v) { const [a, b] = gridRange(s, x0, x1); for (let i = a; i <= b; i++) { const x = s.v[i]; if (x === x) take(x); } }
  else if (s.runs) { for (const r of s.runs) if (r[1] >= x0 && r[0] <= x1) take(r[2]); }
  else if (s.pts) { for (const p of s.pts) if (p[0] >= x0 && p[0] <= x1) take(p[1]); }
  return { lo, hi, pos, nonInt };
}
function peakIn(s) { return seriesExtent(s, state.x0, state.x1).hi; }

/* ---------- the time-series chart */
function tsMargins(W, labels) { return { l: W < 640 ? 52 : 64, r: labels ? (W < 640 ? 14 : 104) : 14, t: 12, b: 36 }; }
function mergeFactor(step, plotW) {
  const room = Math.max(20, plotW / 1.25);
  if ((state.x1 - state.x0) / step <= room) return 1;
  const want = (state.x1 - state.x0) / room;
  for (const s of TSTEPS) if (s >= want && s % step === 0) return s / step;
  return Math.ceil(want / step);
}
// k neighbouring bins as one: the highest for gauges, the average for rates
function aggGrid(s, k) {
  const o = s.o != null ? s.o : Math.round((s.t0 - G.t0) / s.step);
  const j0 = Math.floor(o / k), n = Math.floor((o + s.v.length - 1) / k) - j0 + 1;
  const v = new Float64Array(n).fill(NaN), c = new Uint32Array(n), mean = s.agg === "mean", sum = s.agg === "sum";
  for (let i = 0; i < s.v.length; i++) {
    const x = s.v[i];
    if (x !== x) continue;
    const j = Math.floor((o + i) / k) - j0;
    if (mean || sum) { v[j] = c[j] ? v[j] + x : x; c[j]++; }
    else if (!(v[j] >= x)) v[j] = x;
  }
  if (mean) for (let j = 0; j < n; j++) if (c[j]) v[j] /= c[j];
  const origin = s.t0 - o * s.step;
  return Object.assign({}, s, { v, o: j0, step: s.step * k, t0: origin + j0 * k * s.step });
}
function drawTS(p, spec) {
  const H = spec.H || p.o.height || 210;
  // every dump is kept; when there are more points than pixels, neighbours are merged for drawing
  const room = plotWidth(p);
  let finest = Infinity, shown = 0;
  const orig = spec.series;
  spec.series = spec.series.map(s => {
    if (!s.v) return s;
    finest = Math.min(finest, s.step);
    const k = s.noAgg ? 1 : mergeFactor(s.step, room), out = k > 1 ? aggGrid(s, k) : s;
    shown = Math.max(shown, out.step);
    return out;
  });
  if (spec.baseStep) finest = spec.baseStep;
  let rate = spec.rate === true ? "/s" : (spec.rate || "");
  let note = spec.note || "";
  let counts = false;
  if (spec.rate === true && (spec.unit === "count" || !spec.unit)) {
    let top = 0;
    for (const s of spec.series) if (!p.off.has(s.key)) { const e = seriesExtent(s, state.x0, state.x1); if (e.hi > top) top = e.hi; }
    if (top > 0 && top * (shown || 1000) / 1000 <= 20) {
      // a counter that moves only a few times per slot (elections, failures...): how many in each slot
      rate = "";
      counts = true;
      spec.series = spec.series.map(s => s.v ? Object.assign({}, s, { v: s.v.map(x => Math.round(x * s.step * 1000) / 1e6) }) : s);
      note = note.replace("per second, average per {step}", "how many per {step}");
    } else if (top > 0 && top < 1) {
      // less than once a second reads better per minute or per hour
      const f = top * 60 < 1 ? 3600 : 60;
      rate = f === 60 ? "/min" : "/h";
      spec.series = spec.series.map(s => s.v ? Object.assign({}, s, { v: s.v.map(x => x * f) }) : s);
      note = note.replace("per second", f === 60 ? "per minute" : "per hour");
    }
  }
  if (note.indexOf("{step}") >= 0)
    note = shown ? note.replace("{step}", stepName(shown) + (shown > finest ? " (zoom in for every " + stepName(finest) + ")" : ""))
      : note.replace(/^[^·]*\{step\}( · )?/, "").replace(/ · [^·]*\{step\}/, "");        // nothing drawn: no step to name
  spec.note = note;
  spec.rateStr = rate;
  const visible = spec.series.filter(s => !p.off.has(s.key));
  const colored = visible.filter(s => !s.faint);
  const direct = spec.kind !== "stack" && colored.length >= 2 && colored.length <= 4 && p.width >= 640 && !spec.noDirect;
  const M = tsMargins(p.width, direct);
  frame(p, H, M);
  p.spec = spec;
  if (spec.shade !== false) shadeGaps(p, spec.cover ? spec.cover() : null, spec.coverLabel);
  for (const b of spec.bands || []) {           // time spans to mark (stuck episodes)
    if (b[1] < state.x0 || b[0] > state.x1) continue;
    const A = p.sx(Math.max(b[0], state.x0)), B = p.sx(Math.min(b[1], state.x1));
    svgEl("rect", { x: A.toFixed(1), y: p.top, width: Math.max(3, B - A).toFixed(1), height: p.bottom - p.top, class: "band" }, p.g.events);
  }
  const x0 = state.x0, x1 = state.x1;
  const log = !!spec.log;
  let lo = Infinity, hi = -Infinity, pos = Infinity, nonInt = false;
  let stackTot = null;
  if (spec.kind === "stack") {
    const s0 = visible[0];
    if (s0) {
      const [a, b] = gridRange(s0, x0, x1);
      stackTot = new Float64Array(s0.v.length);
      for (const s of visible) for (let i = a; i <= b; i++) { const x = s.v[i]; if (x === x) stackTot[i] += x; }
      for (let i = a; i <= b; i++) if (stackTot[i] > hi) hi = stackTot[i];
      lo = 0; pos = 1;
    }
  } else {
    for (const s of visible) { const e = seriesExtent(s, x0, x1); lo = Math.min(lo, e.lo); hi = Math.max(hi, e.hi); pos = Math.min(pos, e.pos); nonInt = nonInt || e.nonInt; }
  }
  const has = hi > -Infinity;
  const unit = spec.unit;
  let sy, ticks, tscale = 0;
  const floor = log ? (spec.logMin || 0) : 0;     // counts below one call are slivers of shared intervals
  if (floor && pos < floor) pos = floor;
  if (log) {
    const lmin = has && pos < Infinity ? Math.floor(Math.log10(pos)) : 0, lmax = has && hi > 0 ? Math.max(lmin + 1, Math.ceil(Math.log10(hi))) : lmin + 1;
    const k = (p.bottom - p.top) / (lmax - lmin);
    sy = v => p.bottom - (Math.log10(v) - lmin) * k;
    ticks = [];
    const every = lmax - lmin > 6 ? 2 : 1;
    for (let e = lmin; e <= lmax; e += every) ticks.push(Math.pow(10, e));
  } else {
    const intOnly = (counts || !nonInt) && !rate && unit !== "bytes" && unit !== "percent" && unit !== "pct";
    const pct = unit === "percent" || unit === "pct";
    const T = linTicks(has ? Math.min(0, lo) : 0, has ? (hi > 0 ? (pct && hi <= 100 ? Math.min(100, hi * 1.04) : hi * 1.04) : (hi === 0 ? 1 : hi)) : 1, Math.max(2, Math.round((p.bottom - p.top) / 46)), unit, intOnly);
    const k = (p.bottom - p.top) / ((T.max - T.min) || 1);
    sy = v => p.bottom - (v - T.min) * k;
    ticks = T.ticks;
    tscale = T.s;
  }
  p.sy = sy;
  for (const v of ticks) {
    const Y = Math.round(sy(v)) + 0.5;
    if (Y < p.top - 1 || Y > p.bottom + 1) continue;
    if (Math.abs(Y - p.bottom - 0.5) > 1) svgEl("line", { x1: M.l, x2: p.W - M.r, y1: Y, y2: Y, class: "gridline" }, p.g.grid);
    svgEl("text", { x: M.l - 8, y: Y + 4, class: "tick", "text-anchor": "end" }, p.g.grid).textContent = tickVal(v, unit, rate, tscale);
  }
  drawMarkers(p, spec.markerLabels);
  const sx = p.sx;
  const X = (s, i) => sx(s.t0 + (i + 0.5) * s.step);
  const ends = [];
  if (spec.kind === "stack") {
    const s0 = visible[0];
    if (s0 && has) {
      const [a, b] = gridRange(s0, x0, x1);
      const pxw = (sx(s0.t0 + s0.step) - sx(s0.t0));
      const bw = Math.max(1, Math.min(24, pxw - (pxw >= 4 ? 2 : pxw >= 3 ? 1 : 0)));
      const base = new Float64Array(s0.v.length);
      for (const s of visible) {
        for (let i = a; i <= b; i++) {
          const x = s.v[i];
          if (!(x > 0)) continue;
          const y0 = sy(base[i]), y1 = sy(base[i] + x);
          base[i] += x;
          const top = base[i] >= stackTot[i] - 1e-9;
          const gap = !top && y0 - y1 > 4 ? 2 : 0;
          const xc = sx(s.t0 + (i + 0.5) * s.step);
          svgEl("rect", { x: (xc - bw / 2).toFixed(1), y: (y1 + gap).toFixed(1), width: bw.toFixed(1), height: Math.max(1, y0 - y1 - gap).toFixed(1), fill: s.color }, p.g.marks);
        }
      }
    }
  } else {
    const order = visible.slice().sort((a, b) => (a.faint ? 0 : 1) - (b.faint ? 0 : 1) || (a.sel ? 1 : 0) - (b.sel ? 1 : 0));
    for (const s of order) {
      const cls = s.sel ? "ln-sel" : s.faint ? "ln-faint" : "ln";
      const color = s.sel && s.faint ? "var(--ink)" : (s.color || grey());
      let d = "", last = null, n = 0, singles = [];
      if (s.v) {
        const [a, b] = gridRange(s, x0 - s.step, x1 + s.step);
        let run = 0, runStart = -1;
        for (let i = Math.max(0, a - 1); i <= Math.min(s.v.length - 1, b + 1); i++) {
          const v = s.v[i];
          const ok = v === v && (!log || (v > 0 && v >= floor));
          if (!ok) { if (run === 1) singles.push(runStart); run = 0; continue; }
          const px = X(s, i), py = sy(v);
          if (spec.kind === "step") {
            const xa = sx(s.t0 + i * s.step), xb = sx(s.t0 + (i + 1) * s.step);
            d += (run ? "L" : "M") + xa.toFixed(1) + " " + py.toFixed(1) + "L" + xb.toFixed(1) + " " + py.toFixed(1);
          } else d += (run ? "L" : "M") + px.toFixed(1) + " " + py.toFixed(1);
          if (run === 0) runStart = i;
          run++; n++;
          if (s.t0 + (i + 0.5) * s.step <= x1) last = [px, py];
        }
        if (run === 1) singles.push(runStart);
      } else if (s.runs) {
        let prev = null;
        for (const r of s.runs) {
          if (r[1] < x0 - 3600000 || r[0] > x1 + 3600000) { prev = r; continue; }
          const xa = sx(r[0]), xb = sx(Math.max(r[1], r[0] + 1000)), py = sy(r[2]);
          // a state (cluster size...) holds until the next one is seen: a step, not a ramp
          if (d && prev && r[0] - prev[1] <= 180000) d += "L" + xa.toFixed(1) + " " + sy(prev[2]).toFixed(1) + "L" + xa.toFixed(1) + " " + py.toFixed(1);
          else d += "M" + xa.toFixed(1) + " " + py.toFixed(1);
          d += "L" + xb.toFixed(1) + " " + py.toFixed(1);
          if (r[0] <= x1) last = [Math.min(xb, sx(x1)), py];
          prev = r; n++;
        }
      } else if (s.pts) {
        let prev = null;
        for (const q of s.pts) {
          if (q[0] < x0 - 600000 || q[0] > x1 + 600000) { prev = null; continue; }
          const px = sx(q[0]), py = sy(Math.max(q[1], log ? 1e-9 : -Infinity));
          d += (d && prev && !q[3] && q[0] - prev[0] <= (s.join || 60000) ? "L" : "M") + px.toFixed(1) + " " + py.toFixed(1);
          if (s.dots) svgEl("circle", { cx: px.toFixed(1), cy: py.toFixed(1), r: 3, fill: color, class: "dot" }, p.g.marks);
          prev = q; n++;
          if (q[0] <= x1) last = [px, py];
        }
      }
      if (d) { const path = svgEl("path", { d, class: cls }, p.g.marks); path.style.stroke = color; if (s.dim) path.style.opacity = "0.3"; }
      for (const i of singles) {
        const v = s.v[i];
        svgEl("circle", { cx: X(s, i).toFixed(1), cy: sy(v).toFixed(1), r: s.faint ? 1.5 : 2.5, fill: color }, p.g.marks);
      }
      if (direct && !s.faint && last) ends.push({ s, y: last[1], x: last[0] });
    }
  }
  if (direct && ends.length === colored.length) {
    ends.sort((a, b) => a.y - b.y);
    let ok = true;
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 13) ok = false;
    if (ok) for (const e of ends) {
      const t = svgEl("text", { x: p.W - M.r + 8, y: e.y + 4, class: "end-label" }, p.g.axes);
      t.textContent = trimText(e.s.short || e.s.label, 15);
      svgEl("title", null, t).textContent = e.s.label;
    }
  }
  if (!has) emptyNote(p, spec.empty || "No data for this selection");
  p.tipAt = (t) => {
    const rows = [];
    let a = null, b = null;
    const tol = (state.x1 - state.x0) / Math.max(1, p.W - M.l - M.r) * 8;
    for (const s of visible) {
      const r = valueAt(s, t, tol);
      if (!r) continue;
      rows.push([r.v, s, r]);
      if (a == null || (r.b - r.a) < (b - a)) { a = r.a; b = r.b; }
    }
    if (!rows.length) return null;
    if (spec.kind !== "stack" && !spec.ordered) rows.sort((x, y) => y[0] - x[0]);
    clear(p.hot);
    if (spec.kind !== "stack") for (const [v, s, r] of rows.slice(0, 12)) {
      if (log && !(v > 0 && v >= floor)) continue;
      const cx = s.v ? X(s, r.i) : sx(Math.min(Math.max(t, r.a), Math.max(r.a, r.b)));
      if (s.pts) { svgEl("circle", { cx: sx(r.a), cy: sy(v), r: 4.5, fill: s.color || grey(), class: "hot" }, p.hot); continue; }
      svgEl("circle", { cx, cy: sy(v), r: 4, fill: s.color || grey(), class: "hot" }, p.hot);
    }
    return box => {
      el("div", "tip-t", box, a === b ? logTime(a) : span(a, b));
      if (spec.tipSub) el("div", "tip-sub", box, spec.tipSub);
      const list = spec.kind === "stack" ? rows.slice().reverse() : rows;
      let total = 0;
      for (const [v, s, r] of list.slice(0, 14)) {
        total += v;
        tipRow(box, s.color || grey(), (s.fmt || spec.fmt || (x => fmtVal(x, unit, rate)))(v, r), s.label + (r.pt && r.pt[2] ? " · " + r.pt[2] : ""), spec.kind === "stack" ? false : true);
      }
      if (list.length > 14) el("div", "tip-foot", box, "+ " + (list.length - 14) + " more");
      if (spec.kind === "stack" && list.length > 1) el("div", "tip-foot", box, "Total " + (spec.fmt || (x => fmtVal(x, unit, rate)))(total));
      if (spec.tipFoot) { const f = spec.tipFoot(t); if (f) el("div", "tip-foot", box, f); }
    };
  };
  finish(p);
  buildLegend(p, spec);
  p.note.textContent = spec.note || "";
  p.foot.hidden = !spec.foot;
  if (spec.foot) { clear(p.foot); if (typeof spec.foot === "string") p.foot.textContent = spec.foot; else spec.foot(p.foot); }
  if (p.showTable) buildTwin(p, spec); else p.twin.hidden = true;
  measureReadout(p, spec, orig);
}
function trimText(t, n) { return t.length > n ? t.slice(0, n - 1) + "…" : t; }
function buildLegend(p, spec) {
  const lg = p.legend;
  clear(lg);
  const ser = spec.series;
  if (ser.length < 2 && !spec.legendAlways) { lg.hidden = true; return; }
  lg.hidden = false;
  const colored = ser.filter(s => !s.faint), faint = ser.filter(s => s.faint);
  for (const s of colored.slice(0, 24)) {
    const b = el("button", "lg" + (p.off.has(s.key) ? " off" : ""), lg);
    b.type = "button"; b.title = s.label + " · click to hide or show";
    const k = el("span", spec.kind === "stack" ? "sw" : "key", b); k.style.background = s.color || grey();
    el("span", null, b, s.label);
    b.addEventListener("click", () => { if (p.off.has(s.key)) p.off.delete(s.key); else p.off.add(s.key); renderPanel(p); });
  }
  if (faint.length) {
    const b = el("span", "lg lg-static", lg), k = el("span", "key", b); k.style.background = grey();
    el("span", null, b, spec.faintLabel || (fmtInt(faint.length) + " more in grey"));
  }
  if (spec.legendNote) el("span", "lg-note", lg, spec.legendNote);
}
function buildTwin(p, spec) {
  const box = p.twin;
  box.hidden = false;
  clear(box);
  const ser = spec.series.filter(s => !p.off.has(s.key) && (s.v || s.runs || s.pts)).slice(0, 16);
  if (!ser.length) { el("div", "empty-note", box, "No data"); return; }
  const times = new Set();
  for (const s of ser) {
    if (s.v) { const [a, b] = gridRange(s, state.x0, state.x1); for (let i = a; i <= b; i++) if (s.v[i] === s.v[i]) times.add(s.t0 + i * s.step); }
    else if (s.runs) { for (const r of s.runs) if (r[1] >= state.x0 && r[0] <= state.x1) times.add(Math.max(r[0], state.x0)); }
    else for (const q of s.pts) if (q[0] >= state.x0 && q[0] <= state.x1) times.add(q[0]);
  }
  let ts = Array.from(times).sort((a, b) => a - b);
  let note = "";
  if (ts.length > 400) { const k = Math.ceil(ts.length / 400); ts = ts.filter((t, i) => i % k === 0); note = "every " + k + "th row of " + fmtInt(times.size) + "; zoom in for all"; }
  const t = el("table", null, box), hr = el("tr", null, el("thead", null, t));
  el("th", null, hr, "Time");
  for (const s of ser) { const th = el("th", "num", hr, trimText(s.label, 28)); th.title = s.label; }
  const tb = el("tbody", null, t);
  const fmt = spec.fmt || (x => fmtVal(x, spec.unit, spec.rateStr));
  for (const tm of ts) {
    const tr = el("tr", null, tb);
    el("td", null, tr, shortTime(tm));
    for (const s of ser) {
      let r = null;
      if (s.v) r = valueAt(s, tm + 1, 0);
      else if (s.runs) r = valueAt(s, tm, 0);
      else { const q = s.pts.find(x => x[0] === tm); r = q ? { v: q[1] } : null; }
      el("td", "num", tr, r ? fmt(r.v, r) : "");
    }
  }
  if (note) el("div", "hint", box, note);
}

/* ---------- metrics */
const MREC = new Map(D.metrics.map(r => [r.n, r]));
function mSeries(r) { if (!r._m) r._m = r.m.map(a => toSeries(a, 1)); return r._m; }
function eSeries(r) { if (!r._e) r._e = (r.e || []).map((x, rank) => ({ label: x[0], rank, rows: x[1].map(a => toSeries(a, EF)) })); return r._e; }
const isRate = r => r.k === "c";
function binNote(r) {
  return isRate(r) ? "per second, average per {step}" : "highest per {step}";
}
// the lines for one metric under the current member / lines / find settings
function metricSpec(r, o) {
  o = o || {};
  const sel = selSet(), q = o.noFind ? "" : findQ();
  const nameHit = !q || r.n.toLowerCase().indexOf(q) >= 0;
  const ents = r.e && r.e.length ? eSeries(r) : null;
  const series = [];
  let note = binNote(r), foot = null;
  const grp = TABINFO[state.tab] && TABINFO[state.tab].item ? state.group : "member";
  const byItem = ents && !o.byMember && (o.byItem || grp === "name");
  const ef = o.ef !== undefined ? o.ef : entFilter(r);
  if (state.wanPub && r.c === "wan") note += ef ? " · publisher " + wanPubName(state.wanPub) : " · all publishers (not per publisher)";
  if (state.cpGroup && r.c === "cp") note += ef ? " · CP group " + state.cpGroup : " · all CP groups (not per group)";
  if (byItem) {
    let n = 0;
    for (const e of ents) {
      if (ef && !ef(e.label)) continue;
      if (o.only != null && e.label !== o.only && e.label.indexOf(o.only + " · ") !== 0) continue;
      if (!nameHit && e.label.toLowerCase().indexOf(q) < 0) continue;
      const rows = e.rows.filter(s => sel.has(s.m));
      if (!rows.length) continue;
      const s = combine(rows, r.am || r.a);
      n++;
      series.push(Object.assign({ key: "e:" + e.label, label: e.label, color: e.rank < 8 ? catColor(e.rank) : null, faint: e.rank >= 8,
        sel: o.hl != null && e.label === o.hl, dim: o.hl != null && e.label !== o.hl }, s));
    }
    if (r.ne > ents.length) foot = "Lines for the " + ents.length + " most active of " + fmtInt(r.ne) + " (raise --top to keep more).";
    const am = r.am || r.a;
    if (sel.size > 1) note += am === "max" ? " · highest across members" : am === "min" ? " · lowest across members" : " · summed over members";
  } else if (o.only != null && ents) {
    const e = ents.find(x => x.label === o.only);
    if (e) for (const s of e.rows) if (sel.has(s.m)) series.push(Object.assign({ key: "m:" + s.m, label: MEMBERS[s.m].label, color: memberColor(s.m), faint: s.m >= 8 }, s));
  } else if (ef && ents) {
    for (const [m, rows] of entRowsByMember(r, ef)) series.push(Object.assign({ key: "m:" + m, label: MEMBERS[m].label, color: memberColor(m), faint: m >= 8 }, combine(rows, r.a), { m }));
    if (r.ne > ents.length) foot = "From the " + ents.length + " most active of " + fmtInt(r.ne) + " items (raise --top to keep more).";
  } else {
    for (const s of mSeries(r)) if (sel.has(s.m)) series.push(Object.assign({ key: "m:" + s.m, label: MEMBERS[s.m].label, color: memberColor(s.m), faint: s.m >= 8 }, s));
    if (ents) note += " · " + (r.a === "max" ? "highest of all " : r.a === "min" ? "lowest of all " : "sum of all ") + itemWord(r, true);
  }
  const agg = isRate(r) ? "mean" : "max";
  for (const x of series) x.agg = agg;
  return { series, unit: r.u, rate: isRate(r), note, foot };
}
const NAME_WORDS = { map: "map", executor: "executor", cache: "cache", queue: "queue", topic: "topic", reliableTopic: "topic",
  ringbuffer: "ringbuffer", multiMap: "multimap", replicatedMap: "replicated map", list: "list", set: "set", flakeIdGenerator: "generator",
  pnCounter: "counter", cardinalityEstimator: "estimator", scheduledExecutor: "executor", durableExecutor: "executor", jcache: "cache" };
const TAG_WORDS = { thread: "thread", pipelineId: "pipeline", publisherId: "WAN publisher", replication: "WAN replication",
  endpoint: "endpoint", index: "index", partition: "partition", endpointQualifier: "endpoint", groupId: "Raft group",
  group: "CP group", address: "WAN target", map: "map", cache: "cache", discoveryStrategy: "discovery strategy" };
const CP_WORDS = { atomiclong: "AtomicLong", atomicref: "AtomicReference", lock: "FencedLock", semaphore: "semaphore",
  countdownlatch: "CountDownLatch", map: "CPMap", session: "CP session" };
function itemWord(r, plural) {
  const tags = r.tags || [], t = tags[0] || "", segs = r.n.split(".");
  let w;
  if (tags.indexOf("publisherId") >= 0) w = "WAN publisher";
  else if (tags.indexOf("groupId") >= 0) w = "Raft group";
  else if (t === "name") w = (segs[0] === "cp" ? CP_WORDS[segs[1]] : NAME_WORDS[segs[0]]) || "item";
  else w = TAG_WORDS[t] || "item";
  return plural ? (/(x|s|sh|ch)$/.test(w) ? w + "es" : w + "s") : w;
}
function metricPanel(tab, parent, name, title, extra) {
  const r = MREC.get(name);
  if (!r) return null;
  extra = extra || {};
  const p = addPanel(tab, parent, { title: title || prettyName(name), code: name, height: extra.height, render: pp => {
    const spec = metricSpec(r, extra.opts ? extra.opts() : null);
    if (extra.tweak) extra.tweak(spec);
    drawTS(pp, spec);
  } });
  p.metric = r;
  return p;
}
const PRETTY = {
  "memory.usedHeap": "Used heap", "memory.maxHeap": "Max heap", "memory.usedNative": "Used native memory",
  "memory.committedHeap": "Committed heap", "memory.freeHeap": "Free heap",
  "os.processCpuLoad": "Process CPU", "os.systemCpuLoad": "System CPU", "os.systemLoadAverage": "System load average",
  "os.openFileDescriptorCount": "Open file descriptors", "os.freePhysicalMemorySize": "Free physical memory",
  "os.freeSwapSpaceSize": "Free swap", "thread.threadCount": "Threads", "thread.peakThreadCount": "Peak threads",
  "runtime.usedMemory": "Runtime used memory", "gc.minorCount": "Minor GCs", "gc.majorCount": "Major GCs",
  "gc.minorTime": "Minor GC time", "gc.majorTime": "Major GC time", "gc.unknownCount": "Other GCs", "gc.unknownTime": "Other GC time",
  "operation.queueSize": "Operation queue", "operation.priorityQueueSize": "Priority operation queue",
  "operation.genericQueueSize": "Generic operation queue", "operation.responseQueueSize": "Response queue",
  "operation.invocations.pending": "Pending invocations", "operation.invocations.usedPercentage": "Invocation slots used",
  "operation.completedCount": "Operations completed", "operation.runningCount": "Running operations",
  "operation.thread.normalPendingCount": "Operations waiting, per operation thread",
  "operation.thread.priorityPendingCount": "Priority operations waiting, per operation thread",
  "operation.thread.completedTotalCount": "Operations completed, per operation thread",
  "tcp.inputThread.idleTimeMillis": "Time since the last I/O, IO input threads", "tcp.outputThread.idleTimeMillis": "Time since the last I/O, IO output threads",
  "tcp.inputThread.taskQueueSize": "Tasks waiting, IO input threads", "tcp.outputThread.taskQueueSize": "Tasks waiting, IO output threads",
  "tcp.inputThread.completedTaskCount": "Tasks completed, IO input threads", "tcp.outputThread.completedTaskCount": "Tasks completed, IO output threads",
  "tcp.inputThread.eventCount": "Selector events, IO input threads", "tcp.outputThread.eventCount": "Selector events, IO output threads",
  "threads.io.idleWhileTasksWait.max": "IO threads: time without I/O while tasks wait",
  "threads.op.noProgressWhileWaiting.max": "Operation threads: time without progress while operations wait",
  "executor.internal.queueSize": "Executor queues", "event.eventQueueSize": "Event queue",
  "tcp.bytesReceived": "Bytes received", "tcp.bytesSend": "Bytes sent", "tcp.bytesReceived.WAN": "WAN bytes received",
  "tcp.bytesSend.WAN": "WAN bytes sent", "tcp.connection.clientCount": "Client connections",
  "tcp.connection.activeCount": "Active connections", "tcp.connection.count": "Connections",
  "tcp.inputThread.bytesTransceived": "Bytes read by the IO input threads", "tcp.outputThread.bytesTransceived": "Bytes written by the IO output threads",
  "tcp.connection.in.bytesRead": "Bytes read per connection pipeline", "tcp.connection.out.bytesWritten": "Bytes written per connection pipeline",
  "tcp.connection.out.writeQueueSize": "Write queue per pipeline", "tcp.connection.out.priorityWriteQueueSize": "Priority write queue per pipeline",
  "partitions.localPartitionCount": "Partitions owned", "partitions.migrationQueueSize": "Migration queue",
  "partitions.activePartitionCount": "Active partitions", "map.getCount": "Gets", "map.putCount": "Puts", "map.setCount": "Sets",
  "map.removeCount": "Removes", "map.ownedEntryCount": "Entries", "map.backupEntryCount": "Backup entries", "map.heapCost": "Heap cost",
  "map.hits": "Hits", "map.queryCount": "Queries", "map.indexedQueryCount": "Indexed queries", "map.evictionCount": "Evictions",
  "map.expirationCount": "Expirations", "map.totalGetLatency": "Get time", "map.totalPutLatency": "Put time",
  "map.totalSetLatency": "Set time", "map.totalRemoveLatency": "Remove time", "map.lockedEntryCount": "Locked entries",
  "map.dirtyEntryCount": "Dirty entries", "map.numberOfOtherOperations": "Other operations", "map.numberOfEvents": "Events",
  "map.index.queryCount": "Index queries", "proxy.createdCount": "Proxies created", "client.endpoint.count": "Client endpoints",
  "wan.queueSize": "WAN queue size", "wan.outboundQueueSize": "WAN outbound queue", "wan.totalPublishedEventCount": "WAN events published",
  "wan.totalPublishLatency": "WAN publish time", "event.publicationCount": "Events published", "event.rejectedCount": "Events rejected"
};
function prettyName(n) {
  if (PRETTY[n]) return PRETTY[n];
  const last = n.slice(n.lastIndexOf(".") + 1).replace(/([a-z0-9])([A-Z])/g, "$1 $2").toLowerCase();
  return last.charAt(0).toUpperCase() + last.slice(1);
}

/* ---------- profilers: every interval in which an operation ran (end time, length, counts) */
// a store method ("MapStore\u0001orders\u0001load"): its data structure as shown (a map by its name, others with
// their kind: "queue jobs") and the method
const storeParts = op => { const x = op.split("\u0001"); return { svc: x[0], ds: x[1] || "", meth: x[2] || "" }; };
const storeName = (svc, ds) => svc === "MapStore" ? ds : svc.replace(/(Store|Loader|Writer)$/, "").toLowerCase() + " " + ds;
const opLabel = op => { if (op.indexOf("\u0001") < 0) return shortOp(op); const k = storeParts(op); return storeName(k.svc, k.ds) + " · " + k.meth; };
const opFull = op => { if (op.indexOf("\u0001") < 0) return op; const k = storeParts(op); return k.svc + " of " + storeName(k.svc, k.ds) + " · " + k.meth; };
const ALLROWS = D.prof.map(r => {
  const n = r.e.length, E = new Float64Array(n), DU = new Float64Array(n);
  let t = PT0;
  for (let k = 0; k < n; k++) { t += r.e[k]; E[k] = t; DU[k] = r.e[k]; }
  for (const x of r.dx) DU[x[0]] = x[1];
  let maxD = 0;
  for (let k = 0; k < n; k++) if (DU[k] > maxD) maxD = DU[k];
  const N = Float64Array.from(r.n), U = Float64Array.from(r.u), X = new Float64Array(n), XE = new Uint8Array(n);
  for (let k = 0; k < n; k++) { X[k] = Math.abs(r.x[k]); XE[k] = r.x[k] > 0 ? 1 : 0; }
  // the intervals with calls of 1 ms or more: interval index, first bucket, calls per bucket from there (flat)
  const ns = r.s.length, SI = new Int32Array(ns), SB = new Uint8Array(ns), SO = new Int32Array(ns + 1);
  let nc = 0;
  for (const x of r.s) nc += x.length - 2;
  const SC = new Float32Array(nc);         // whole counts are exact up to 16M; shared-out ones keep 2 decimals
  for (let j = 0, k = 0, o = 0; j < ns; j++) {
    const x = r.s[j];
    k += x[0]; SI[j] = k; SB[j] = x[1]; SO[j] = o;
    for (let i = 2; i < x.length; i++) SC[o++] = x[i];
    SO[j + 1] = o;
  }
  const op = OPS[r.o];
  return { m: r.m, p: r.p, op, short: opLabel(op), full: opFull(op), E, D: DU, maxD, N, U, X, XE, SI, SB, SO, SC, GC: null, SL: null, SW: null,
    key: "m:" + r.m + "|" + r.p + "|" + op, okey: "o:" + r.p + "|" + op };
});
const PROWS = ALLROWS.filter(r => r.p !== "S"), SROWS = ALLROWS.filter(r => r.p === "S");
// the calls of each interval in the latency groups in use: GC[g][j] for group g >= 1 (group 0 is the rest of
// the calls), SL[j] the calls at or above the slow threshold, SW[j] the slowest group with calls
function regroupRows() {
  const L = latGroups();
  if (L.rows) return;
  for (const r of ALLROWS) {
    const ns = r.SI.length, GC = [null];
    for (let g = 1; g < L.G; g++) GC.push(new Float32Array(ns));
    const SL = new Float32Array(ns), SW = new Int8Array(ns);
    for (let j = 0; j < ns; j++) {
      const e0 = r.SB[j];
      for (let o = r.SO[j], z = r.SO[j + 1]; o < z; o++) {
        const c = r.SC[o];
        if (!c) continue;
        const g = L.gof[Math.min(63, e0 + o - r.SO[j])];
        if (!g) continue;
        GC[g][j] += c;
        if (g >= L.tg) SL[j] += c;
        if (g > SW[j]) SW[j] = g;
      }
    }
    r.GC = GC; r.SL = SL; r.SW = SW;
  }
  L.rows = true;
}
const PNAME = { O: "Operations", I: "Invocation", S: "store latency" };
const PTAG = { O: "Op", I: "Inv", S: "Store" };
// the rows a latency block shows (its operations, the Find text, the operation picked in its list; with
// ignoreSel, also the other rows than the one picked in its table); ignoreOp: every operation of the block
function profRows(ignoreSel, blk, ignoreOp) {
  const sel = selSet(), q = findQ(), own = blk && blk.rows;
  return (own || PROWS).filter(r => sel.has(r.m) && (own || state.prof === "both" || r.p === state.prof) &&
    (!blk || !blk.pred || blk.pred(r.op)) &&
    (!q || r.short.toLowerCase().indexOf(q) >= 0 || r.op.toLowerCase().indexOf(q) >= 0 || MEMBERS[r.m].label.toLowerCase().indexOf(q) >= 0) &&
    (ignoreOp || !blk || !blk.op || r.op === blk.op) &&
    (ignoreSel || !blk || !blk.sel || r.okey === blk.sel || r.key === blk.sel));
}
// exact counts of the intervals that end inside [x0, x1]
function rowStats(r, x0, x1) {
  regroupRows();
  const L = LG, k0 = lowerBound(r.E, x0), k1 = upperBound(r.E, x1);
  const o = { r, n: 0, t: 0, g: new Array(L.G).fill(0), mx: 0, mxe: 0, slow: 0, slowFirst: null, slowLast: null };
  for (let k = k0; k < k1; k++) {
    o.n += r.N[k]; o.t += r.U[k];
    if (r.X[k] > o.mx || (r.X[k] === o.mx && r.XE[k] && !o.mxe)) { o.mx = r.X[k]; o.mxe = r.XE[k]; }
  }
  let rest = o.n;
  for (let j = lowerBound(r.SI, k0); j < r.SI.length && r.SI[j] < k1; j++) {
    for (let g = 1; g < L.G; g++) { const c = r.GC[g][j]; o.g[g] += c; rest -= c; }
    if (r.SL[j] > 0) {
      const k = r.SI[j];
      o.slow += r.SL[j];
      if (o.slowFirst == null) o.slowFirst = r.E[k] - r.D[k];
      o.slowLast = r.E[k];
    }
  }
  o.g[0] = rest;
  return o;
}
// bucket size for a chart: one profiler dump when zoomed in, coarser when the points outnumber the pixels
function pickBucket(plotW, pxPer, base) {
  const step = base || PSTEP, want = (state.x1 - state.x0) / Math.max(20, plotW / pxPer);
  if (want <= step) return step;
  for (const s of TSTEPS) if (s >= want && s % step === 0) return s;
  return Math.ceil(want / step) * step;
}
const plotWidth = p => p.width - tsMargins(p.width, false).l - 14;
// counts per bucket: an interval that straddles two buckets is shared out by overlap; the max goes
// to the bucket the interval ends in
function profBuckets(rows, bsz) {
  regroupRows();
  const G = LG.G, b0 = Math.floor(state.x0 / bsz) * bsz, nb = Math.max(1, Math.ceil((state.x1 - b0) / bsz) + 1), end = b0 + nb * bsz;
  const B = { b0, bsz, nb, n: new Float64Array(nb), u: new Float64Array(nb), g: [], mx: new Float64Array(nb), mxe: new Uint8Array(nb) };
  for (let g = 0; g < G; g++) B.g.push(new Float64Array(nb));
  const vals = new Float64Array(2 + G);       // calls, time, calls per group
  for (const r of rows) {
    const k0 = lowerBound(r.E, b0), k1 = lowerBound(r.E, end + r.maxD);
    let j = lowerBound(r.SI, k0);
    for (let k = k0; k < k1; k++) {
      const b = r.E[k], a = b - r.D[k];
      if (a >= end) continue;
      vals.fill(0);
      vals[0] = r.N[k]; vals[1] = r.U[k];
      while (j < r.SI.length && r.SI[j] < k) j++;
      let rest = vals[0];
      if (j < r.SI.length && r.SI[j] === k) {
        for (let g = 1; g < G; g++) { vals[2 + g] = r.GC[g][j]; rest -= vals[2 + g]; }
      }
      vals[2] = rest;
      spreadInto(B, a, b, vals);
      const ie = Math.ceil((b - b0) / bsz) - 1;
      if (ie >= 0 && ie < nb && (r.X[k] > B.mx[ie] || (r.X[k] === B.mx[ie] && r.XE[k] && !B.mxe[ie]))) { B.mx[ie] = r.X[k]; B.mxe[ie] = r.XE[k]; }
    }
  }
  return B;
}
function spreadInto(B, a, b, vals) {
  const G = B.g.length;
  const put = (i, f) => {
    if (i < 0 || i >= B.nb) return;
    B.n[i] += vals[0] * f; B.u[i] += vals[1] * f;
    for (let q = 0; q < G; q++) B.g[q][i] += vals[2 + q] * f;
  };
  if (!(b > a)) { put(Math.ceil((b - B.b0) / B.bsz) - 1, 1); return; }
  let i = Math.floor((a - B.b0) / B.bsz), x = a;
  const len = b - a;
  while (x < b) {
    const y = Math.min(b, B.b0 + (i + 1) * B.bsz);
    if (y > x) put(i, (y - x) / len);
    x = y; i++;
  }
}
// buckets the selected members have dumps for: 0 there, "no data" elsewhere
function coveredBuckets(B) {
  const mask = new Uint8Array(B.nb);
  for (const c of unionCover(selMembers())) {
    const a = Math.max(0, Math.floor((c[0] - B.b0) / B.bsz)), z = Math.min(B.nb - 1, Math.floor((c[1] - B.b0) / B.bsz));
    for (let i = a; i <= z; i++) mask[i] = 1;
  }
  return mask;
}
// calls in latency group g of these profiler rows, for the intervals that end in [a, b]
const profAmount = (rowsFn, g) => (a, b) => rowsFn().reduce((s, r) => s + rowStats(r, a, b).g[g], 0);
function profLatency(rowsFn) {
  return (a, b) => {
    let n = 0, t = 0, mx = 0, mxe = 0;
    for (const r of rowsFn()) { const o = rowStats(r, a, b); n += o.n; t += o.t; if (o.mx > mx || (o.mx === mx && o.mxe)) { mx = o.mx; mxe = o.mxe; } }
    if (!n) return [];
    return [{ label: "average", color: catColor(0), text: fmtUs(t / n) }, { label: "max", color: catColor(1), text: (mxe ? "" : "≤ ") + fmtUs(mx) },
      { label: "calls", color: null, text: fmtInt(n) }];
  };
}
function profSeries(arr, mask, B, label, color, key) {
  const v = new Float64Array(B.nb);
  for (let i = 0; i < B.nb; i++) v[i] = mask[i] ? arr[i] : NaN;
  return { key, label, color, t0: B.b0, step: B.bsz, v, o: 0, f: 1, noAgg: true };
}
const fmtCount = v => (Math.abs(v) >= 10 || Number.isInteger(v)) ? fmtInt(v) : sig3(v);

/* ---------- toolbar and tabs */
const syncers = [];
function seg(host, label, options, get, set) {
  const wrap = el("div", "ctl", host);
  el("span", "ctl-label", wrap, label);
  const g = el("div", "seg", wrap);
  g.setAttribute("role", "group"); g.setAttribute("aria-label", label);
  const btns = options.map(o => {
    const b = el("button", null, g, o[1]);
    b.type = "button";
    b.addEventListener("click", () => { set(o[0]); update(); });
    return [o[0], b];
  });
  syncers.push(() => btns.forEach(x => x[1].setAttribute("aria-pressed", String(get() === x[0]))));
  return { wrap, btns };
}
const TABINFO = {
  overview: { find: "operation or member" }, latency: { find: "operation or member", item: "By operation" },
  operations: { find: "metric or thread", item: "By name" }, network: { find: "metric, thread or pipeline", item: "By name" },
  maps: { find: "name or metric", item: "By name" }, system: { find: "metric" }, cluster: { find: "metric or member", item: "By name" },
  wan: { find: "metric, publisher or operation", item: "By name" }, cp: { find: "metric, group or operation", item: "By name" },
  threads: { find: "thread, member or metric", item: "By name" }, other: { find: "metric or name", item: "By name" },
  transfer: { find: "member, peer or kind" }, slowops: { find: "operation, name or member", item: "By operation" },
  logs: { find: "text, logger or member" }, "logs-health": { find: "value name" }, "logs-exceptions": { find: "exception, text or logger" },
  "logs-cp": { find: "group, member or text" }, gc: { find: "kind, cause or member" }, "logs-migrations": { find: "text or member" },
  mapstore: { find: "map, method or member", item: "By map" },
  invocations: { find: "operation, name or partition", item: "By operation" },
  config: { find: "property or value" }
};
const sections = {};
// what a tab shows (operation, WAN publisher, CP group, kind, metric...): picked in a row of the bar that stays at the top
const CTX = {};
function ctxRow(tab) {
  if (!CTX[tab]) { CTX[tab] = el("div", "tabctl ctxrow", $("ctxbar")); CTX[tab].hidden = true; }
  return CTX[tab];
}
function syncCtx() {
  let any = false;
  for (const k in CTX) {
    const show = k === state.tab && Array.from(CTX[k].children).some(c => !c.hidden);
    CTX[k].hidden = !show;
    any = any || show;
  }
  $("ctxbar").hidden = !any;
}
function buildTabs() {
  const nav = $("tabs");
  if (HASLOGS) {
    // two sources, two sets of tabs: what the diagnostics files say, and what the members' own logs say
    const bar = el("div", "srcbar", null);
    nav.parentNode.insertBefore(bar, nav);
    el("span", "ctl-label", bar, "Source");
    const mk = (cls, label, n, group) => {
      const b = el("button", cls, bar, label);
      el("span", "src-n", b, n);
      b.type = "button";
      b.addEventListener("click", () => setTab(state.last[group] || (group === "logs" ? "logs" : "overview")));
      syncers.push(() => b.setAttribute("aria-pressed", String(groupOf(state.tab) === group)));
      return b;
    };
    const d = mk(null, "Diagnostics", fmtInt(META.files) + " files", "diag");
    if (!HASDIAG) { d.disabled = true; d.title = "No diagnostics files in this report"; }
    mk("logs", "Member logs", fmtInt(META.logfiles) + " files", "logs");
  }
  for (const [id, label] of ALLTABS) {
    const b = el("button", null, nav, label);
    b.type = "button"; b.setAttribute("role", "tab"); b.id = "tab-" + id;
    const cnt = el("span", "cnt", b);
    b.addEventListener("click", () => setTab(id));
    syncers.push(() => {
      b.hidden = groupOf(id) !== groupOf(state.tab);
      b.setAttribute("aria-selected", String(state.tab === id));
      cnt.textContent = tabCount(id);
      cnt.classList.toggle("warn", (id === "threads" && STUCK_TOTAL > 0) || (id === "operations" && SAT_TOTAL > 0) ||
        ((id === "logs" || id === "logs-exceptions") && LOG_OOME.length > 0) || (id === "gc" && GCEV.some(e => e.cat === 3)) ||
        (id === "logs-migrations" && MIG.bins.some(b => MIG.kinds[b[0]][0] === "lost")));
    });
    const sec = el("div", "tabsec", $("main"));
    sec.setAttribute("role", "tabpanel"); sec.setAttribute("aria-labelledby", "tab-" + id);
    sec.hidden = true;
    sections[id] = sec;
  }
  syncers.push(() => nav.classList.toggle("logs", groupOf(state.tab) === "logs"));
}
function tabCount(id) {
  if (id === "maps") { const n = structCount(DS_KINDS, "maps"); return n ? String(n) : ""; }
  if (id === "latency") { const n = new Set(PROWS.map(r => r.op)).size; return n ? String(n) : ""; }
  if (id === "mapstore") { const n = new Set(ST_TOT.map(r => r.dsKey).concat(SROWS.map(r => r.op.split("\u0001").slice(0, 2).join("\u0001")))).size; return n ? String(n) : ""; }
  if (id === "slowops") return SO.inv.length ? String(SO.inv.length) : "";
  if (id === "invocations") return IV.inv.length ? String(IV.inv.length) : "";
  if (id === "config") { const n = cfDifferCount("ConfigProperties") + cfDifferCount("args"); return n ? n + " differ" : ""; }
  if (id === "wan") { const n = itemCount(r => r.c === "wan", ["replication", "publisherId"]); return n ? String(n) : ""; }
  if (id === "cp") { const n = itemCount(r => r.n.indexOf("raft.group.") === 0, ["groupId"]); return n ? String(n) : ""; }
  if (id === "threads") return STUCK_TOTAL ? STUCK_TOTAL + " stuck" : "";
  if (id === "operations") return SAT_TOTAL ? SAT_TOTAL + " saturated" : "";
  if (id === "transfer") return TR_PEERS.length ? String(TR_PEERS.length) : "";
  if (id === "logs") return LOG_OOME.length ? LOG_OOME.length + " OOME" : "";
  if (id === "gc") { const n = GCEV.filter(e => e.cat === 3).length, h = GCEV.filter(e => e.hum).length; return n ? n + " failure" + (n === 1 ? "" : "s") : h ? h + " humongous" : ""; }
  if (id === "logs-exceptions") return LOGX.length ? String(LOGX.length) : "";
  if (id === "logs-cp") { const n = new Set(LOGCP.filter(e => e[2]).map(e => e[2])).size; return n ? String(n) : ""; }
  if (id === "logs-migrations") {
    const parts = new Set(MIG.rows.filter(e => MIG.kinds[e[2]][0] === "lost").map(e => (LOST_PART_RE.exec(e[4]) || [])[1]).filter(x => x));
    const lost = parts.size || MIG.bins.filter(b => MIG.kinds[b[0]][0] === "lost").reduce((a, b) => a + b[2], 0);
    return lost ? lost + " lost" : MIG.procs.length ? String(MIG.procs.length) : "";
  }
  return "";
}
// distinct items (WAN publishers, Raft groups...): the values of these tags, over the metrics that have them all
function itemCount(metricTest, tags) {
  const set = new Set();
  for (const r of D.metrics) {
    const pos = tags.map(t => (r.tags || []).indexOf(t));
    if (!metricTest(r) || pos.some(i => i < 0)) continue;
    for (const e of (r.e || [])) { const parts = e[0].split(" · "); set.add(pos.map(i => parts[i]).join("|")); }
  }
  return set.size;
}
const hasLatency = tab => LATBLOCKS.some(b => b.tab === tab);
function setTab(id) {
  if (!sections[id]) id = DEFTAB;
  state.tab = id;
  state.last[groupOf(id)] = id;
  for (const k in sections) sections[k].hidden = k !== id;
  try { history.replaceState(null, "", "#" + id); } catch (e) { /* file:// in some browsers */ }
  clearHover();
  update();
}
function buildToolbar() {
  const tb = $("toolbar");
  if (NM > 1) {
    const wrap = el("label", "ctl", tb);
    el("span", "ctl-label", wrap, "Member");
    const sel = el("select", null, wrap);
    const nreal = MEMBERS.filter(m => !m.gcOnly).length;
    el("option", null, sel, "All " + (nreal === NM ? NM + " members" : (nreal ? nreal + " member" + (nreal === 1 ? "" : "s") + " + " : "") + (NM - nreal) + " GC log" + (NM - nreal === 1 ? "" : "s"))).value = "all";
    let grp = null;
    MEMBERS.forEach((m, i) => {
      if (m.gcOnly && !grp && nreal) { grp = el("optgroup", null, sel); grp.label = "GC logs not linked to a member"; }
      const o = el("option", null, grp || sel, m.label); o.value = String(i);
    });
    sel.addEventListener("change", () => { state.member = sel.value; update(); });
    syncers.push(() => { sel.value = state.member; });
  }
  const prof = seg(tb, "Profiler", [["both", "Both"], ["O", "Operations"], ["I", "Invocation"]], () => state.prof, v => { state.prof = v; LATBLOCKS.forEach(b => { b.sel = null; }); });
  const lines = seg(tb, "Lines", [["member", "By member"], ["name", "By name"]], () => state.group, v => { state.group = v; LATBLOCKS.forEach(b => { b.sel = null; }); });
  const scale = seg(tb, "Scale", [[true, "Log"], [false, "Linear"]], () => state.log, v => { state.log = v; });
  const sw = el("label", "ctl", tb);
  el("span", "ctl-label", sw, "Find");
  const inp = el("input", null, sw);
  inp.type = "search";
  let deb = null;
  inp.addEventListener("input", () => { clearTimeout(deb); deb = setTimeout(() => { state.q = inp.value; update(); }, 180); });
  const range = el("div", "range", tb), txt = el("span", null, range);
  const reset = el("button", "btn", range, "Zoom out");
  reset.type = "button";
  reset.addEventListener("click", resetView);
  const drag = seg(tb, "Drag to", [["zoom", "Zoom"], ["measure", "Measure"]], () => state.dragMode, v => { state.dragMode = v; });
  drag.wrap.title = "What dragging across a chart does; hold Shift for the other one";
  const mbar = el("div", "mbar", tb);
  el("span", "ctl-label", mbar, "Span");
  const fromIn = el("input", null, mbar), toIn = el("input", null, mbar);
  mbar.insertBefore(document.createTextNode("→"), toIn);
  for (const [inp2, lab] of [[fromIn, "Measured span from"], [toIn, "Measured span to"]]) {
    inp2.type = "text"; inp2.setAttribute("aria-label", lab); inp2.spellcheck = false; inp2.autocomplete = "off";
    inp2.placeholder = MULTI_DAY ? (UPTIME ? "Nd hh:mm:ss" : "dd-mm hh:mm:ss") : "hh:mm:ss";
    inp2.addEventListener("change", () => {
      const a = parseWhen(fromIn.value, state.msel ? state.msel[0] : X_MIN);
      let b = parseWhen(toIn.value, a != null ? a : (state.msel ? state.msel[1] : X_MIN));
      for (const x of [fromIn, toIn]) {            // red only for a time that can't be read (an empty one is still to come)
        if (!x.value.trim() || parseWhen(x.value, X_MIN) != null) x.removeAttribute("aria-invalid"); else x.setAttribute("aria-invalid", "true");
      }
      if (a == null || b == null) return;
      if (b <= a && !/\d+[-/.]\d+|\d\s*d/.test(toIn.value)) b += 86400000;      // a time-only end before the start: the next day
      if (b > a) setMeasure(a, b);
    });
  }
  const mdur = el("span", "mdur", mbar);
  const mclear = el("button", "btn btn-s", mbar, "Clear");
  mclear.type = "button";
  const clearSpan = () => {                       // the typed times too, even in the box being typed in
    for (const x of [fromIn, toIn]) { x.value = ""; x.removeAttribute("aria-invalid"); }
    clearMeasure();
  };
  mclear.addEventListener("click", clearSpan);
  const hint = el("span", "hint", tb);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && (state.msel || fromIn.value || toIn.value)) clearSpan(); });
  el("span", "spacer", tb);
  const theme = el("button", "btn", tb);
  theme.type = "button";
  const order = [null, "light", "dark"], names = { null: "Theme: auto", light: "Theme: light", dark: "Theme: dark" };
  theme.addEventListener("click", () => {
    const next = order[(order.indexOf(root.getAttribute("data-theme")) + 1) % order.length];
    if (next) root.setAttribute("data-theme", next); else root.removeAttribute("data-theme");
    update();
  });
  syncers.push(() => {
    const info = TABINFO[state.tab];
    prof.wrap.hidden = !(state.tab === "overview" || hasLatency(state.tab)) || state.tab === "mapstore" || !PROWS.length;
    scale.wrap.hidden = state.tab !== "gc" && (!hasLatency(state.tab) || !(PROWS.length || SROWS.length));
    lines.wrap.hidden = !info.item || !tabHasItems(state.tab);
    lines.btns[1][1].textContent = info.item || "By name";
    inp.placeholder = info.find;
    const full = state.x0 <= X_MIN && state.x1 >= X_MAX;
    clear(txt);
    txt.appendChild(document.createTextNode(full ? "All data: " : "Zoomed: "));
    el("b", null, txt, span(state.x0, state.x1));
    txt.appendChild(document.createTextNode(" (" + dur((state.x1 - state.x0) / 1000) + ")"));
    reset.disabled = full;
    theme.textContent = names[root.getAttribute("data-theme")];
    const ms = state.msel, measuring = state.dragMode === "measure";
    mbar.hidden = !ms && !measuring;
    if (document.activeElement !== fromIn) fromIn.value = ms ? shortTime(ms[0]) : "";
    if (document.activeElement !== toIn) toIn.value = ms ? shortTime(ms[1]) : "";
    mdur.textContent = ms ? dur((ms[1] - ms[0]) / 1000) : "drag across a chart, or type the times";
    mclear.hidden = !ms;
    hint.textContent = measuring ? "Shift-drag to zoom" : "Shift-drag to measure";
  });
}
function tabHasItems(tab) {
  if (tab === "latency") return PROWS.length > 0;
  if (tab === "mapstore") return HASSTORE;
  if (tab === "slowops") return SO_INV.length > 0;
  if (tab === "invocations") return HASINV;
  return D.metrics.some(r => r.c === tab && r.e && r.e.length) || hasLatency(tab);
}
function kpi(host, label, value, sub) {
  const k = el("div", "kpi", host);
  el("div", "kpi-label", k, label); el("div", "kpi-value", k, value);
  if (sub) el("div", "kpi-sub", k, sub);
  return k;
}
const tabUpdaters = [];
function update() {
  syncers.forEach(f => f());
  for (const f of tabUpdaters) if (f.tab === state.tab) f();
  // Find hides the charts that don't match (not on Overview and Latency, where it picks operations)
  const q = findQ();
  for (const p of panels) {
    if (p.tab === "overview" || p.tab === "latency" || p.mapDetail || p.metricLink || p.fixed) continue;
    let hit = !q || (p.o.title + " " + (p.o.code || "")).toLowerCase().indexOf(q) >= 0;
    if (!hit && p.metric) hit = (p.metric.e || []).some(e => e[0].toLowerCase().indexOf(q) >= 0);
    if (hit && p.metric && (state.wanPub || state.cpGroup)) {         // a WAN or CP metric with nothing for the publisher or group picked
      const ef = entFilter(p.metric);
      if (ef && !(p.metric.e || []).some(e => ef(e[0]))) hit = false;
    }
    p.card.hidden = !hit;
  }
  invalidate();
}

/* ---------- generic tab: headline charts, every other metric of the category, the unchanged ones */
function moreSection(tab, parent, used, label) {
  const recs = D.metrics.filter(r => r.c === tab && !used.has(r.n));
  if (recs.length) {
    el("h2", null, parent, label || "All other metrics here");
    el("p", "lead", parent, recs.length + " metric" + (recs.length === 1 ? "" : "s") + ", by name. Find filters them.");
    for (const r of recs.sort((a, b) => a.n < b.n ? -1 : 1)) {
      metricPanel(tab, parent, r.n, prettyName(r.n), { height: 170 });
    }
  }
  unchangedSection(tab, parent);
}
function unchangedSection(tab, parent) {
  const st = D.static.filter(s => s.c === tab), fl = D.flat.filter(s => s.c === tab);
  if (!st.length && !fl.length) return;
  const det = el("details", "card sect", parent);
  el("summary", null, det, "Unchanged: " + (st.length + fl.length) + " metric" + (st.length + fl.length === 1 ? "" : "s") + " that never changed");
  el("p", null, det, "Same value from the first dump to the last on each member, so they are listed here instead of drawn as flat lines.");
  const wrap = el("div", "table-wrap", det), t = el("table", null, wrap);
  const hr = el("tr", null, el("thead", null, t));
  ["Metric", "Value"].forEach(h => el("th", null, hr, h));
  const tb = el("tbody", null, t);
  const render = () => {
    clear(tb);
    const sel = selSet(), q = findQ();
    for (const s of st) {
      if (q && s.n.toLowerCase().indexOf(q) < 0) continue;
      const vals = s.v.filter(x => sel.has(x[0]));
      if (!vals.length) continue;
      const tr = el("tr", null, tb);
      const td = el("td", null, tr); el("span", null, td, prettyName(s.n) + " "); el("code", null, td, s.n);
      const same = vals.every(x => x[1] === vals[0][1]);
      el("td", "wide", tr, same ? fmtVal(vals[0][1], s.u, false) + (vals.length > 1 ? " on all " + vals.length + " members" : "")
        : vals.map(x => MEMBERS[x[0]].label + ": " + fmtVal(x[1], s.u, false)).join(" · "));
    }
    for (const s of fl) {
      if (q && s.n.toLowerCase().indexOf(q) < 0) continue;
      const tr = el("tr", null, tb);
      const td = el("td", null, tr); el("span", null, td, prettyName(s.n) + " "); el("code", null, td, s.n);
      el("td", "wide", tr, fmtInt(s.s) + " series" + (s.v != null ? ", all " + fmtVal(s.v, s.u, false) : ", each constant"));
    }
  };
  render();
  tabUpdaters.push(Object.assign(render, { tab }));
}
function headline(tab, parent, list, used) {
  for (const it of list) {
    const name = it[0];
    if (!MREC.has(name)) continue;
    used.add(name);
    metricPanel(tab, parent, name, it[1], it[2]);
  }
}

/* ---------- derived charts */
function staticValue(name, m) {
  const s = D.static.find(x => x.n === name);
  if (!s) return null;
  const v = s.v.find(x => x[0] === m);
  return v ? v[1] : null;
}
function heapPctSeries() {
  const used = MREC.get("memory.usedHeap"), max = MREC.get("memory.maxHeap");
  if (!used) return [];
  const sel = selSet(), out = [];
  for (const s of mSeries(used)) {
    if (!sel.has(s.m)) continue;
    const ms = max ? mSeries(max).find(x => x.m === s.m) : null;
    const smax = ms ? null : staticValue("memory.maxHeap", s.m);
    if (!ms && !(smax > 0)) continue;
    const v = new Float64Array(s.v.length);
    for (let i = 0; i < v.length; i++) {
      let mx = smax;
      if (ms) { const j = s.o - ms.o + i; mx = j >= 0 && j < ms.v.length ? ms.v[j] : NaN; }
      v[i] = mx > 0 ? 100 * s.v[i] / mx : NaN;
    }
    out.push({ key: "m:" + s.m, label: MEMBERS[s.m].label, color: memberColor(s.m), faint: s.m >= 8, t0: s.t0, step: s.step, v, o: s.o, f: 1, agg: "max" });
  }
  return out;
}
function sumSeries(names, scale) {
  // per member: the sum of several metrics (same grid), times scale
  const sel = selSet(), per = new Map();
  for (const n of names) {
    const r = MREC.get(n);
    if (!r) continue;
    for (const s of mSeries(r)) {
      if (!sel.has(s.m)) continue;
      if (!per.has(s.m)) per.set(s.m, []);
      per.get(s.m).push(s);
    }
  }
  const out = [];
  for (const [m, rows] of per) {
    const c = combine(rows, "sum");
    const v = c.v.map(x => x * scale);
    out.push({ key: "m:" + m, label: MEMBERS[m].label, color: memberColor(m), faint: m >= 8, t0: c.t0, step: c.step, v, o: c.o, f: 1, agg: "mean" });
  }
  return out.sort((a, b) => a.label < b.label ? -1 : 1);
}
function clusterRuns() {
  const sel = selSet(), per = new Map();
  for (const r of D.cluster) {
    if (!sel.has(r[0])) continue;
    if (!per.has(r[0])) per.set(r[0], []);
    per.get(r[0]).push([r[1], r[2], r[3], r[4], r[5]]);
  }
  // HazelcastInstance is logged every minute or so; a member's MemberAdded / MemberRemoved events say exactly
  // when its view of the cluster size changed
  for (const e of D.events) {
    if ((e[2] !== "MemberAdded" && e[2] !== "MemberRemoved") || !(e[5] > 0) || !per.has(e[1])) continue;
    per.get(e[1]).push([e[0], e[0], e[5], null, null]);
  }
  const out = [];
  for (const [m, list] of per) {
    list.sort((a, b) => a[0] - b[0] || (a[3] == null ? 1 : 0) - (b[3] == null ? 1 : 0));
    const runs = [];
    for (const r of list) {
      const p = runs[runs.length - 1];
      if (r[3] == null) { r[3] = p ? p[3] : 0; r[4] = p ? p[4] : null; }
      if (p && p[2] === r[2] && p[3] === r[3] && p[4] === r[4] && r[0] - p[1] <= 180000) { p[1] = Math.max(p[1], r[1]); continue; }
      runs.push(r.slice());
    }
    out.push({ key: "m:" + m, label: MEMBERS[m].label, color: memberColor(m), faint: m >= 8, runs });
  }
  return out.sort((a, b) => a.label < b.label ? -1 : 1);
}
function clusterPanel(tab, parent) {
  if (!D.cluster.length) return null;
  return addPanel(tab, parent, { title: "Cluster size, as seen by each member", height: 180, render: p => {
    const series = clusterRuns();
    drawTS(p, { series, unit: "count", kind: "line", note: "from HazelcastInstance and the membership events", markerLabels: true,
      fmt: (v, r) => fmtInt(v) + " member" + (v === 1 ? "" : "s") + (r && r.run && r.run[3] ? " · master" : ""),
      tipFoot: t => { const e = MARKERS.find(mk => Math.abs(mk.t - t) < (state.x1 - state.x0) / 80); return e ? (e.kind === "MemberAdded" ? "Joined: " : "Left: ") + e.who + " · seen by " + e.seen.size + " member" + (e.seen.size === 1 ? "" : "s") : null; } });
  } });
}

/* ---------- Overview */
function buildOverview(sec) {
  const kp = el("div", "kpis", sec);
  const renderK = () => {
    clear(kp);
    const sel = selMembers(), ss = new Set(sel);
    const dsel = sel.filter(i => !MEMBERS[i].logsOnly), gonly = sel.filter(i => MEMBERS[i].gcOnly).length, lonly = sel.length - dsel.length - gonly;
    const lts = dsel.reduce((a, i) => a + MEMBERS[i].lifetimes.length, 0);
    kpi(kp, dsel.length > 1 ? "Members" : "Member", dsel.length > 1 ? String(dsel.length) : dsel.length ? MEMBERS[dsel[0]].label : MEMBERS[sel[0]].label,
      (!dsel.length ? (MEMBERS[sel[0]].gcOnly ? "a GC log only, no diagnostics" : "member logs only, no diagnostics") : lts > dsel.length ? lts + " lifetimes: " + (lts - dsel.length) + " restart" + (lts - dsel.length === 1 ? "" : "s") : (dsel.length > 1 ? "no restarts" : "no restart")) +
      (lonly && dsel.length ? " · " + lonly + " more with member logs only" : "") + (gonly && dsel.length ? " · " + gonly + " GC log" + (gonly === 1 ? "" : "s") + " with no member" : ""));
    kpi(kp, "Time range", dur((state.x1 - state.x0) / 1000), span(state.x0, state.x1));
    const mk = MARKERS.filter(e => e.t >= state.x0 && e.t <= state.x1);
    const left = mk.filter(e => e.kind === "MemberRemoved").length, joined = mk.length - left;
    kpi(kp, "Membership changes", String(mk.length), mk.length ? left + " left, " + joined + " joined" : "none in this range");
    if (PROWS.length) {
      const L = latGroups();
      let top = 0, more = 0;
      for (const r of profRows(true)) { const o = rowStats(r, state.x0, state.x1); top += o.g[L.G - 1]; more += o.slow - o.g[L.G - 1]; }
      linkKpi(kpi(kp, "Executions " + L.labels[L.G - 1], fmtInt(top), (L.tg < L.G - 1 ? fmtInt(more) + " more at " + rangeLabel(state.lat.t, L.edges[L.G - 2]) + " · " : "") +
        (state.prof === "both" ? "both profilers" : PNAME[state.prof])), "latency", "Open the Latency tab, where the latency groups and the slow threshold are set");
    }
    if (SO.dumps.length) {
      const rows = soRows(true), w = rows.length ? rows.reduce((a, b) => b.dur > a.dur ? b : a) : null, run = rows.filter(r => r.running).length;
      const k = linkKpi(kpi(kp, "Slow operations", fmtInt(rows.length), w ? "longest " + dur(w.dur / 1000) + ": " + w.short + " · " + MEMBERS[w.m].label + (run ? " · " + run + " still running" : "")
        : "none reported by the detector"), "slowops", "Open the Slow operations tab");
      if (run) { k.classList.add("alert"); k.querySelector(".kpi-value").classList.add("bad"); }
    }
    if (HASINV) {
      const sk = ivPeak("s"), rows = ivRows(true, true), w = rows.length ? rows.reduce((a, b) => b.dur > a.dur ? b : a) : null, late = rows.filter(ivLate).length;
      const k = linkKpi(kpi(kp, "Slow pending invocations", sk ? fmtCount(sk.v) : fmtInt(rows.length), (sk ? (sk.v > 0 ? "the most at once, " + shortTime(sk.t) + " · " + mShort(sk.m) : "at any sample") + " · " : "listed · ") +
        (w ? "longest " + dur(w.dur / 1000) + ": " + w.short + (w.name ? " " + w.name : "") : "none listed by the sampler") +
        (late ? " · " + fmtInt(late) + " past their call timeout" : "")), "invocations", "Open the Invocations tab");
      if (late) { k.classList.add("alert"); k.querySelector(".kpi-value").classList.add("bad"); }
    }
    if (HASSTORE) {
      // the store calls at or above the slow threshold: in the time range in view, or since the members started (one dump)
      const L = latGroups();
      let n = 0, slow = 0, mx = 0, w = null;
      if (SROWS.length) {
        for (const r of SROWS) { if (!ss.has(r.m)) continue; const o = rowStats(r, state.x0, state.x1); n += o.n; slow += o.slow; if (o.mx > mx) { mx = o.mx; w = r.short; } }
      } else {
        for (const r of ST_TOT) { if (!ss.has(r.m)) continue; n += r.n; slow += slowOf(Object.fromEntries(r.b), state.lat.t); if (r.mx > mx) { mx = r.mx; w = r.name + " · " + r.meth; } }
      }
      linkKpi(kpi(kp, (ST_ALLMAP ? "MapStore" : "Store") + " calls " + L.slowLabel, fmtInt(slow),
        (w ? "slowest " + fmtUs(mx) + ": " + w + " · " : "") + fmtInt(n) + " calls " + (SROWS.length ? "in the time range in view" : "since the members started")), "mapstore", "Open the MapStore tab");
    }
    if (THREADS.length) {
      const eps = stuckInView(false), n = new Set(eps.map(e => e.th.i)).size;
      const w = eps.length ? eps.reduce((a, b) => epDur(b) > epDur(a) ? b : a) : null;
      const k = kpi(kp, "Stuck threads", fmtInt(n), w ? "longest ≈ " + dur(epDur(w) / 1000) + ": " + thLabel(w.th) : "none of the " + fmtInt(threadsChecked().length) + " IO and operation threads");
      if (n) { k.classList.add("alert"); k.querySelector(".kpi-value").classList.add("bad"); }
      k.classList.add("link"); k.tabIndex = 0; k.setAttribute("role", "link"); k.title = "Open the Threads tab";
      k.addEventListener("click", () => setTab("threads"));
      k.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setTab("threads"); } });
    }
    if (POOLS.list.length) {
      const eps = satInView(), n = new Set(eps.map(e => e.xi)).size;
      const w = eps.length ? eps.reduce((a, b) => satDur(b) > satDur(a) ? b : a) : null;
      const k = linkKpi(kpi(kp, "Saturated executors", fmtInt(n), w ? "longest ≈ " + dur(satDur(w) / 1000) + ": " + satLabel(w) : "none of the " + fmtInt(POOLS.list.length) + " executors"), "operations", "Open the Operations tab");
      if (n) { k.classList.add("alert"); k.querySelector(".kpi-value").classList.add("bad"); }
    }
    if (HASLOGS) {
      const oo = LOG_OOME.filter(o => ss.has(o[1]) && inView(o[0])), last = oo[oo.length - 1];
      const k = linkKpi(kpi(kp, "OutOfMemoryError in member logs", fmtInt(oo.length), last ? "last: " + MEMBERS[last[1]].label + " at " + shortTime(last[0]) + (last[2] ? " · " + last[2] : "") : "none in " + fmtInt(META.logfiles) + " log file" + (META.logfiles === 1 ? "" : "s")),
        "logs-exceptions", "Open the member logs: Exceptions");
      if (oo.length) { k.classList.add("alert"); k.querySelector(".kpi-value").classList.add("bad"); }
    }
    if (HASLOGS && MIG.bins.some(b => MIG.kinds[b[0]][0] === "lost")) {
      const lost = migLost(), last = lost[lost.length - 1], parts = new Set();
      for (const e of lost) { const m = LOST_PART_RE.exec(e[4]); if (m) parts.add(+m[1]); }
      const k = linkKpi(kpi(kp, "Partitions lost in member logs", fmtInt(parts.size || lost.length), last ? (parts.size ? "partition " +
        Array.from(parts).sort((a, b) => a - b).slice(0, 8).join(", ") + (parts.size > 8 ? ", …" : "") + " · " : "") + "last at " + shortTime(last[0]) + " · " + mShort(last[1]) :
        "none in this time range"), "logs-migrations", "Open the member logs: Migrations");
      if (lost.length) { k.classList.add("alert"); k.querySelector(".kpi-value").classList.add("bad"); }
    }
    if (wanLogHas()) wanLogKpi(kp);
    if (HASGC) {
      const rows = gcRows(true), w = rows.length ? rows.reduce((a, e) => e.p > a.p ? e : a) : null;
      const hum = rows.filter(e => e.hum).length, bad = rows.filter(e => e.cat === 3).length;
      const k = linkKpi(kpi(kp, "Longest GC pause", w ? fmtMs(w.p) : "–", w ? fmtInt(rows.length) + " pauses in the GC logs · " + fmtInt(hum) + " caused by humongous allocations" +
        (bad ? " · " + fmtInt(bad) + " full GC or evacuation failure" + (bad === 1 ? "" : "s") : "") : "no pause in the GC logs here"), "gc", "Open the GC tab");
      if (bad) { k.classList.add("alert"); k.querySelector(".kpi-value").classList.add("bad"); }
    }
    const hp = heapPctSeries();
    if (hp.length) {
      let best = null;
      for (const s of hp) { const e = seriesExtent(s, state.x0, state.x1); if (e.hi > -Infinity && (!best || e.hi > best[0])) best = [e.hi, s]; }
      if (best) kpi(kp, "Highest heap use", fmtPct(best[0]), "of max heap · " + best[1].label);
    }
    const hb = D.hb.filter(h => ss.has(h[1]) && h[0] >= state.x0 && h[0] <= state.x1);
    if (D.hb.length) {
      let w = null;
      for (const h of hb) if (h[4] != null && (!w || h[4] > w[4])) w = h;
      kpi(kp, "Late heartbeats", fmtInt(hb.length), w ? "worst: " + MEMBERS[w[1]].label + " had none from " + w[3] + " for " + fmtMs(w[4]) : "none in this range");
    }
  };
  tabUpdaters.push(Object.assign(renderK, { tab: "overview" }));
  clusterPanel("overview", sec);
  if (MREC.has("memory.usedHeap")) addPanel("overview", sec, { title: "Heap used, % of max heap", code: "memory.usedHeap / memory.maxHeap", render: p => {
    drawTS(p, { series: heapPctSeries(), unit: "pct", note: "highest per {step}" });
  } });
  metricPanel("overview", sec, "os.processCpuLoad", "Process CPU");
  if (PROWS.length) addPanel("overview", sec, { title: "Slow executions", code: "OperationsProfiler + InvocationProfiler", render: p => {
    const B = profBuckets(profRows(true), pickBucket(plotWidth(p), 4)), mask = coveredBuckets(B), L = LG;
    p.h3.textContent = "Slow executions, " + L.slowLabel;
    const series = [];
    for (let g = L.tg; g < L.G; g++) series.push(Object.assign(profSeries(B.g[g], mask, B, L.labels[g], latColor(g), "g" + g), { amount: profAmount(() => profRows(true), g) }));
    drawTS(p, { kind: "stack", unit: "count", baseStep: PSTEP, series, ordered: true,
      note: "per {step} · " + (state.prof === "both" ? "both profilers" : PNAME[state.prof]) + ", all operations · slow threshold set on the Latency tab", legendAlways: true, fmt: fmtCount,
      tipFoot: t => topOpsAt(t, B.bsz) });
  } });
  metricPanel("overview", sec, "operation.queueSize", "Operation queue");
  metricPanel("overview", sec, "operation.invocations.pending", "Pending invocations");
  if (MREC.has("gc.minorTime") || MREC.has("gc.majorTime")) addPanel("overview", sec, { title: "Time in GC, % of wall time", code: "gc.minorTime + gc.majorTime + gc.unknownTime", render: p => {
    drawTS(p, { series: sumSeries(["gc.minorTime", "gc.majorTime", "gc.unknownTime"], 0.1), unit: "pct", note: "average per {step}" });
  } });
  const evCard = el("section", "card", sec);
  const cap = el("div", "cap", evCard);
  el("h3", null, cap, "What happened");
  const evNote = el("span", "note", cap);
  const wrap = el("div", "table-wrap", evCard);
  const render = () => {
    clear(wrap);
    const ss = selSet();
    const kinds = new Set(["MemberAdded", "MemberRemoved", "Lifecycle", "ClusterVersionChanged", "MigrationFailed"]);
    const groups = [];
    for (const e of D.events) {
      if (!kinds.has(e[2]) || !ss.has(e[1]) || e[0] < state.x0 || e[0] > state.x1) continue;
      const g = groups.find(x => x.kind === e[2] && x.text === e[3] && Math.abs(x.t - e[0]) < 60000);
      if (g) { g.seen.push(e[1]); continue; }
      groups.push({ t: e[0], kind: e[2], text: e[3], seen: [e[1]] });
    }
    evNote.textContent = groups.length ? fmtInt(groups.length) + " membership, lifecycle and version events · the Cluster tab lists every event" : "";
    if (!groups.length) { el("div", "empty-note", wrap, "No membership or lifecycle events in this range."); return; }
    const t = el("table", null, wrap), hr = el("tr", null, el("thead", null, t));
    ["Time", "Event", "Details", "Seen by"].forEach(h => el("th", null, hr, h));
    const tb = el("tbody", null, t);
    for (const g of groups.slice(0, 200)) {
      const tr = el("tr", null, tb);
      el("td", "num", tr, shortTime(g.t)); el("td", null, tr, g.kind); el("td", "wide", tr, g.text);
      el("td", "wide", tr, g.seen.length === NDIAG && NDIAG > 1 ? "all members" : g.seen.map(m => MEMBERS[m].label).join(", "));
    }
    if (groups.length > 200) el("div", "hint", wrap, "First 200 of " + fmtInt(groups.length) + "; zoom in to see the rest.");
  };
  tabUpdaters.push(Object.assign(render, { tab: "overview" }));
}
function topOpsAt(t, bsz) {
  const a = Math.floor(t / bsz) * bsz, top = [];
  regroupRows();
  for (const r of profRows(true)) {
    const k0 = lowerBound(r.E, a), k1 = upperBound(r.E, a + bsz);
    let s = 0;
    for (let j = lowerBound(r.SI, k0); j < r.SI.length && r.SI[j] < k1; j++) s += r.SL[j];
    if (s > 0) top.push([s, r]);
  }
  top.sort((x, y) => y[0] - x[0]);
  if (!top.length) return null;
  return "Most: " + top.slice(0, 3).map(x => x[1].short + " (" + PTAG[x[1].p] + ", " + MEMBERS[x[1].m].label + ") " + fmtCount(x[0])).join(" · ") + (top.length > 3 ? " …" : "");
}

/* ---------- Latency: one block per tab (Latency: every operation; WAN, CP Subsystem: theirs) */
const LATBLOCKS = [];
const isWanOp = op => /\.wan\./i.test(op) || /(^|[.$])Wan[A-Z]/.test(op);
const isCpOp = op => /\.(cp|raft)\./i.test(op);
function latGroupKey(r) { return state.group === "name" ? r.okey : r.key; }
function latViews(blk) {
  const rows = profRows(true, blk), by = new Map(), G = latGroups().G;
  for (const r of rows) {
    const o = rowStats(r, state.x0, state.x1);
    if (!o.n) continue;
    const k = latGroupKey(r);
    let v = by.get(k);
    if (!v) { v = { key: k, p: r.p, op: r.op, short: r.short, full: r.full, m: state.group === "name" ? null : r.m, members: new Set(), n: 0, t: 0, g: new Array(G).fill(0), slow: 0, mx: 0, mxe: 0, rows: [], slowFirst: null, slowLast: null }; by.set(k, v); }
    v.members.add(r.m); v.rows.push(r);
    v.n += o.n; v.t += o.t; v.slow += o.slow;
    for (let q = 0; q < G; q++) v.g[q] += o.g[q];
    if (o.mx > v.mx || (o.mx === v.mx && o.mxe && !v.mxe)) { v.mx = o.mx; v.mxe = o.mxe; }
    if (o.slowFirst != null) { v.slowFirst = v.slowFirst == null ? o.slowFirst : Math.min(v.slowFirst, o.slowFirst); v.slowLast = Math.max(v.slowLast || 0, o.slowLast); }
  }
  const out = Array.from(by.values());
  for (const v of out) { v.avg = v.n ? v.t / v.n : 0; v.share = v.n ? v.slow / v.n : 0; }
  return out;
}
function buildLatency(sec) {
  if (!PROWS.length) { el("p", "lead", sec, "No OperationsProfiler or InvocationProfiler sections in these files (hazelcast.diagnostics.operation-profiler / invocation-profiler period.seconds > 0 turns them on)."); return; }
  el("p", "lead", sec, "From each profiler dump (every " + stepName(PSTEP) + "): zoom in to see every one. Pick an operation in the list, or click a row of the table to chart only that one. Groups faster than the slow threshold are blue, the slow ones red; the further from the threshold, the stronger the colour.");
  latencyBlock("latency", sec, { pred: null, title: "Operations", what: "operation" });
}
// the operations a latency block can show with the filters in use: [[operation, label]] by label
function blockOps(blk) {
  const by = new Map();
  for (const r of profRows(true, blk, true)) if (!by.has(r.op)) by.set(r.op, r.short);
  const count = new Map();
  for (const s of by.values()) count.set(s, (count.get(s) || 0) + 1);
  const out = [];
  for (const [op, s] of by) out.push([op, count.get(s) > 1 ? s + " · " + op.slice(0, Math.max(0, op.lastIndexOf("."))).replace(/^com\.hazelcast\./, "") : s]);
  return out.sort((a, b) => { const x = a[1].toLowerCase(), y = b[1].toLowerCase(); return x < y ? -1 : x > y ? 1 : 0; });
}
// the slow threshold is always a group boundary: moving it past the fastest or the slowest group moves that too
function setLat(k, v) {
  const L = state.lat;
  L[k] = v;
  if (k === "f" && L.t < v) L.t = v;
  if (k === "s" && L.t > v) L.t = v;
  if (k === "t") { if (v < L.f) L.f = v; if (v > L.s) L.s = v; }
}
function latControls(sec, blk) {
  const ol = el("label", "ctl", ctxRow(blk.tab)), what = blk.what || "operation";
  ol.hidden = !!blk.noOps;
  el("span", "ctl-label", ol, what.charAt(0).toUpperCase() + what.slice(1));
  const os = el("select", "opsel latop", ol);
  os.addEventListener("change", () => { blk.op = os.value || null; blk.sel = null; update(); });
  const ctl = el("div", "tabctl latctl", sec);
  const grp = el("div", "ctl", ctl);
  grp.setAttribute("role", "group"); grp.setAttribute("aria-label", "Latency groups");
  grp.title = "The profilers count calls in buckets that double in size (powers of 2 µs): 1.05 s and 2.1 s are the boundaries nearest to 1 s and 2 s, 32.8 ms the one nearest to 32 ms.";
  el("span", "ctl-label", grp, "Latency groups: fastest <");
  const fs = el("select", null, grp);
  el("span", "ctl-label", grp, "slowest ≥");
  const ss = el("select", null, grp);
  const tl = el("label", "ctl", ctl);
  el("span", "ctl-label", tl, "Slow from");
  const ts = el("select", null, tl);
  fs.setAttribute("aria-label", "Fastest latency group: calls faster than");
  ss.setAttribute("aria-label", "Slowest latency group: calls of at least");
  ts.title = "Calls at or above this are slow: the red groups, the slow-execution timeline, the table's slow columns and the Overview";
  const fill = (s, list, k) => {
    for (const b of list) { const o = el("option", null, s, edgeLabel(b)); o.value = String(b); }
    s.addEventListener("change", () => { setLat(k, +s.value); update(); });
  };
  fill(fs, LAT_FAST, "f"); fill(ss, LAT_SLOWEST, "s"); fill(ts, LAT_ALL, "t");
  let opsKey = null;
  syncers.push(() => {
    fs.value = String(state.lat.f); ss.value = String(state.lat.s); ts.value = String(state.lat.t);
    if (state.tab !== blk.tab || blk.noOps) return;
    const ops = blockOps(blk);
    if (blk.op && !ops.some(o => o[0] === blk.op)) { blk.op = null; blk.sel = null; }    // Find or the filters left it out
    const q = findQ(), key = q + "\n" + ops.map(o => o[0]).join("\n");
    if (key !== opsKey) {
      opsKey = key;
      clear(os);
      const m = q ? " matching Find" : "";
      el("option", null, os, ops.length === 1 ? "The only operation" + m : ops.length ? "All " + fmtInt(ops.length) + " operations" + m : "No operation" + m).value = "";
      for (const [op, label] of ops) { const o = el("option", null, os, label); o.value = op; o.title = op; }
    }
    os.value = blk.op || "";
  });
}
function latencyBlock(tab, sec, blk) {
  blk.tab = tab;
  blk.sel = null;
  blk.op = null;
  blk.sort = { key: "slow", dir: -1 };
  LATBLOCKS.push(blk);
  latControls(sec, blk);
  const selbar = el("div", "selbar", sec); selbar.hidden = true;
  const selName = () => {
    if (!blk.sel) return null;
    const parts = blk.sel.slice(2).split("|");
    const tag = pp => blk.rows ? "" : " · " + PNAME[pp];
    return blk.sel[0] === "o" ? opLabel(parts.slice(1).join("|")) + tag(parts[0])
      : opLabel(parts.slice(2).join("|")) + tag(parts[1]) + " · " + MEMBERS[+parts[0]].label;
  };
  tabUpdaters.push(Object.assign(() => {
    clear(selbar);
    const n = selName();
    selbar.hidden = !n;
    if (!n) return;
    selbar.appendChild(document.createTextNode("Latency charts show "));
    el("b", null, selbar, n);
    const b = el("button", "btn btn-s", selbar, "Show all");
    b.type = "button"; b.addEventListener("click", () => { blk.sel = null; update(); });
  }, { tab }));
  const word = blk.what || "operation";
  addPanel(tab, sec, { title: blk.pred ? "Calls per latency group, " + word + "s" : "Calls per latency group", height: 230, render: p => {
    const B = profBuckets(profRows(false, blk), pickBucket(plotWidth(p), 2, blk.step)), mask = coveredBuckets(B), L = LG;
    const series = [];
    for (let g = 0; g < L.G; g++) series.push(Object.assign(profSeries(B.g[g], mask, B, L.labels[g], latColor(g), "g" + g), { amount: profAmount(() => profRows(false, blk), g) }));
    drawTS(p, { series, unit: "count", log: state.log, logMin: 1, baseStep: blk.step || PSTEP, note: "calls per {step}" + (state.log ? " · log scale" : "") + " · slow (" + L.slowLabel + ") in red", fmt: fmtCount, legendAlways: true, noDirect: true, measure: "sum", ordered: true });
  } });
  addPanel(tab, sec, { title: blk.pred ? "Average and max latency, " + word + "s" : "Average and max latency", height: 210, render: p => {
    const B = profBuckets(profRows(false, blk), pickBucket(plotWidth(p), 2, blk.step)), mask = coveredBuckets(B), n = B.nb;
    const avg = new Float64Array(n), mx = new Float64Array(n);
    for (let i = 0; i < n; i++) { avg[i] = B.n[i] > 0 ? B.u[i] / B.n[i] : NaN; mx[i] = B.mx[i] > 0 ? B.mx[i] : NaN; }
    const sa = profSeries(avg, mask, B, "average", catColor(0), "avg"), sm = profSeries(mx, mask, B, "max", catColor(1), "max");
    sm.fmt = (v, r) => (r && !B.mxe[r.i] ? "≤ " : "") + fmtUs(v);
    drawTS(p, { series: [sm, sa], unit: "us", log: state.log, baseStep: blk.step || PSTEP, note: "per {step} · a max marked ≤ is an upper bound", fmt: v => fmtUs(v), noDirect: true,
      measure: profLatency(() => profRows(false, blk)) });
  } });
  // timeline: when each operation had slow executions
  addPanel(tab, sec, { title: "When " + word + "s were slow", height: 120, noTable: true, render: p => {
    p.h3.textContent = "When " + word + "s were slow (" + latGroups().slowLabel + ")";
    renderSlowTimeline(p, blk);
  } });
  const card = el("section", "card", sec);
  const cap = el("div", "cap", card);
  el("h3", null, cap, blk.title);
  const tnote = el("span", "note", cap);
  const wrap = el("div", "table-wrap", card);
  const columns = () => {
    const L = latGroups(), cols = [
      { key: "member", label: "Member", val: v => v.m == null ? v.members.size : MEMBERS[v.m].label, fmt: v => v.m == null ? v.members.size + " member" + (v.members.size === 1 ? "" : "s") : mShort(v.m) },
      { key: "op", label: blk.opLabel || "Operation", val: v => v.short + " " + v.p },
      { key: "n", label: "Calls", num: true, val: v => v.n, fmt: v => fmtInt(v.n) },
      { key: "avg", label: "Avg", num: true, val: v => v.avg, fmt: v => fmtUs(v.avg) },
      { key: "max", label: "Max", num: true, val: v => v.mx, fmt: v => (v.mxe ? "" : "≤ ") + fmtUs(v.mx) },
      // the slow calls (from the threshold, every red group) and, when there are more red groups, the slowest one
      { key: "slow", label: L.slowLabel, g: L.tg, num: true, val: v => v.slow, fmt: v => v.slow ? fmtInt(v.slow) : "", tip: "calls at or above the slow threshold" }];
    if (L.G - 1 > L.tg) cols.push({ key: "g" + (L.G - 1), label: L.labels[L.G - 1], g: L.G - 1, num: true, val: v => v.g[L.G - 1], fmt: v => v.g[L.G - 1] ? fmtInt(v.g[L.G - 1]) : "", tip: "calls in the slowest group" });
    cols.push({ key: "share", label: "Slow %", num: true, val: v => v.share, fmt: v => v.slow ? fmtPct(100 * v.share) : "", tip: "share of the calls that were slow" },
      { key: "last", label: "Last slow", num: true, val: v => v.slowLast || 0, fmt: v => v.slowLast ? shortTime(v.slowLast) : "" });
    return cols;
  };
  const render = () => {
    clear(wrap);
    const views = latViews(blk), COLS = columns(), both = new Set(views.map(v => v.p)).size > 1 || state.prof === "both" && new Set(PROWS.map(r => r.p)).size > 1;
    const col = COLS.find(c => c.key === blk.sort.key) || COLS.find(c => c.key === "slow");
    views.sort((a, b) => { const x = col.val(a), y = col.val(b); return (x < y ? -1 : x > y ? 1 : 0) * blk.sort.dir || b.slow - a.slow || b.n - a.n; });
    tnote.textContent = fmtInt(views.length) + " row" + (views.length === 1 ? "" : "s") + " for the time range in view · " + (state.group === "name" ? "one row per " + (blk.unit || "operation") : "one row per member and " + (blk.unit || "operation")) + " · slow: " + latGroups().slowLabel + " · click to select";
    const t = el("table", null, wrap), hr = el("tr", null, el("thead", null, t));
    for (const c of COLS) {
      const th = el("th", c.num ? "num" : null, hr);
      const b = el("button", null, th);
      b.type = "button";
      if (c.g != null) { const sw = el("span", "sw", b); sw.style.background = latColor(c.g); }
      if (c.tip) b.title = c.tip;
      b.appendChild(document.createTextNode(c.label + (col === c ? (blk.sort.dir < 0 ? " ↓" : " ↑") : "")));
      if (col === c) th.setAttribute("aria-sort", blk.sort.dir < 0 ? "descending" : "ascending");
      b.addEventListener("click", () => { blk.sort = { key: c.key, dir: col === c ? -blk.sort.dir : (c.num ? -1 : 1) }; render(); });
    }
    const tb = el("tbody", null, t);
    for (const v of views.slice(0, 500)) {
      const tr = el("tr", "click" + (blk.sel === v.key ? " sel" : ""), tb);
      tr.tabIndex = 0;
      for (const c of COLS) {
        const td = el("td", c.num ? "num" : c.key === "op" ? "op" : null, tr);
        if (c.key === "op" && v.p === "S") { const k = storeParts(v.op); td.textContent = storeName(k.svc, k.ds); el("span", "meth", td, " · " + k.meth); td.title = v.full; }
        else if (c.key === "op") { td.textContent = v.short; td.title = v.op + " · " + PNAME[v.p] + " profiler"; if (both) el("span", "ptag", td, PTAG[v.p]); }
        else td.textContent = c.fmt ? c.fmt(v) : c.val(v);
        if (c.key === "member" && v.m != null) td.title = MEMBERS[v.m].label;
      }
      const pick = () => { blk.sel = blk.sel === v.key ? null : v.key; update(); };
      tr.addEventListener("click", pick);
      tr.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    }
    if (views.length > 500) el("div", "hint", wrap, "First 500 of " + fmtInt(views.length) + " rows; Find or the Operation list narrows them.");
    if (!views.length) el("div", "empty-note", wrap, "No profiler data in this selection.");
  };
  tabUpdaters.push(Object.assign(render, { tab }));
}
function renderSlowTimeline(p, blk) {
  const L = latGroups();
  const views = latViews(blk).filter(v => v.slow > 0).sort((a, b) => b.slow - a.slow);
  const LIMIT = 30, rows = views.slice(0, LIMIT);
  const W = p.width, M = { l: W < 640 ? 120 : Math.min(340, Math.max(250, Math.round(W * 0.27))), r: 16, t: 8, b: 36 }, rowH = 22;
  const H = M.t + M.b + Math.max(1, rows.length) * rowH + (rows.length ? 0 : 40);
  p.host.style.minHeight = H + "px";
  frame(p, H, M);
  drawMarkers(p, false);
  const plotW = W - M.l - M.r, nd = Math.max(10, Math.floor(plotW / 9)), w = (state.x1 - state.x0) / nd;
  let maxN = 1;
  // one dot per slot of about 9 px: the slow calls in it, by group; its colour is the slowest group reached
  const dots = rows.map(v => {
    const map = new Map();
    for (const r of v.rows) {
      const k0 = lowerBound(r.E, state.x0), k1 = upperBound(r.E, state.x1);
      for (let j = lowerBound(r.SI, k0); j < r.SI.length && r.SI[j] < k1; j++) {
        const s = r.SL[j];
        if (!(s > 0)) continue;
        const k = r.SI[j], t1 = r.E[k], t0 = t1 - r.D[k], t = (t0 + t1) / 2;
        const d = Math.max(0, Math.min(nd - 1, Math.floor((t - state.x0) / w)));
        let g = map.get(d);
        if (!g) { g = { n: 0, ng: new Float64Array(L.G), worst: 0, wt: 0, t0, t1, calls: 0, dumps: 0 }; map.set(d, g); }
        g.n += s; g.wt += t * s; g.calls += r.N[k]; g.dumps++;
        for (let q = L.tg; q < L.G; q++) g.ng[q] += r.GC[q][j];
        if (r.SW[j] > g.worst) g.worst = r.SW[j];
        g.t0 = Math.min(g.t0, t0); g.t1 = Math.max(g.t1, t1);
      }
    }
    const arr = Array.from(map.values());
    for (const g of arr) if (g.n > maxN) maxN = g.n;
    return arr;
  });
  rows.forEach((v, ri) => {
    const cy = M.t + ri * rowH + rowH / 2;
    const label = (v.p === "S" ? "" : PTAG[v.p] + " · ") + v.short + (state.group === "name" ? "" : " · " + mShort(v.m));
    const t = svgEl("text", { x: M.l - 10, y: cy + 4, class: "row-label", "text-anchor": "end" }, p.g.axes);
    t.textContent = trimText(label, Math.max(6, Math.floor((M.l - 14) / 6.4)));
    svgEl("title", null, t).textContent = (v.p === "S" ? v.full : v.op + " · " + PNAME[v.p]) + (v.m != null ? " · " + MEMBERS[v.m].label : "");
    svgEl("line", { x1: M.l, x2: W - M.r, y1: Math.round(cy) + 0.5, y2: Math.round(cy) + 0.5, class: "cover" }, p.g.events);
    for (const g of dots[ri]) {
      g.cx = p.sx(g.wt / g.n); g.cy = cy; g.r = 4 + 5 * Math.sqrt(g.n / maxN);
      svgEl("circle", { cx: g.cx.toFixed(1), cy: cy.toFixed(1), r: g.r.toFixed(1), fill: latColor(Math.max(L.tg, g.worst)), class: "dot" }, p.g.marks);
    }
  });
  if (!rows.length) emptyNote(p, "No execution " + L.slowLabel + " in this selection");
  p.note.textContent = views.length > LIMIT ? "the " + LIMIT + " with the most slow executions, of " + views.length : views.length ? views.length + " " + (blk.unit || "operation") + (views.length === 1 ? "" : "s") : "";
  clear(p.legend);
  p.legend.hidden = !rows.length;
  if (L.G - L.tg > 1) el("span", "lg-note", p.legend, "slowest call in the dot:");
  for (let g = L.tg; g < L.G; g++) {
    const it = el("span", "lg lg-static", p.legend), s = el("span", "sw", it);
    s.style.background = latColor(g);
    el("span", null, it, L.labels[g]);
  }
  el("span", "lg-note", p.legend, "dot size = number of slow executions");
  oomeLegend(p.legend);
  p.tipAt = (t, py, px) => {
    const ri = Math.floor((py - M.t) / rowH);
    if (ri < 0 || ri >= rows.length) return null;
    let best = null, bd = 14;
    for (const g of dots[ri]) { const d = Math.abs(g.cx - px); if (d < bd) { bd = d; best = g; } }
    if (!best) return null;
    clear(p.hot);
    svgEl("circle", { cx: best.cx, cy: best.cy, r: best.r + 2.5, class: "dot-hot" }, p.hot);
    const v = rows[ri];
    return box => {
      el("div", "tip-t", box, v.short + (v.p === "S" ? "" : " · " + PNAME[v.p]) + (v.m != null ? " · " + MEMBERS[v.m].label : ""));
      el("div", "tip-sub", box, span(best.t0, best.t1));
      for (let g = L.G - 1; g >= L.tg; g--) if (best.ng[g] > 0) tipRow(box, latColor(g), fmtCount(best.ng[g]), L.labels[g], false);
      el("div", "tip-foot", box, fmtPct(100 * best.n / Math.max(1, best.calls)) + " of the " + fmtInt(best.calls) + " calls in this time were " + L.slowLabel);
      el("div", "tip-sub", box, v.p === "S" ? v.full : v.op);
    };
  };
  finish(p);
}

/* ---------- Maps */
/* ---------- Slow operations: what the slow operation detector reported (SlowOperations sections) */
const SO = D.slowops || { ops: [], stacks: [], inv: [], logs: [], dumps: [] };
const SO_INV = SO.inv.map((x, i) => ({ i, m: x[0], oi: x[1], op: SO.ops[x[1]], short: shortOp(SO.ops[x[1]]), start: x[2], dur: x[3], end: x[2] + x[3],
  st: x[4], first: x[5], last: x[6], running: !!x[7], part: x[8], name: x[9] || "", det: x[10] || "" }));
const soCounted = (m, oi, st) => { const l = SO.logs.find(x => x[0] === m && x[1] === oi && x[2] === st); return l ? l[3] : null; };
const soState = { op: null, sel: null, sort: { key: "dur", dir: -1 }, osort: { key: "tot", dir: -1 } };
// the slow invocations in view: they overlap the time range, on the selected members, matching Find and the list
function soRows(ignoreOp) {
  const ss = selSet(), q = findQ();
  return SO_INV.filter(r => ss.has(r.m) && r.start <= state.x1 && r.end >= state.x0 && (ignoreOp || !soState.op || r.op === soState.op) &&
    (!q || r.op.toLowerCase().indexOf(q) >= 0 || r.name.toLowerCase().indexOf(q) >= 0 || MEMBERS[r.m].label.toLowerCase().indexOf(q) >= 0 || r.det.toLowerCase().indexOf(q) >= 0));
}
const soEnd = r => r.running ? "running at " + shortTime(r.last) : shortTime(r.end);
function soLog() { return HASLOGS ? LOGS.events.filter(e => LOGCATS[e[2]][0] === "slowop" && selSet().has(e[1]) && inView(e[0])) : []; }
// rows of bars on a time axis, one per operation (and member), the bars that overlap in sub-rows
function renderSlowLanes(p, rows, o) {
  o = o || {};
  const W = p.width, M = laneMargins(W), BAR = 6, GAP = 3, MAXSUB = 6;
  let y = M.t;
  for (const r of rows) {
    const ends = [];
    for (const e of r.items.slice().sort((a, b) => a.start - b.start)) {
      let k = ends.findIndex(t => t <= e.start);
      if (k < 0) k = ends.length < MAXSUB ? ends.length : ends.indexOf(Math.min.apply(null, ends));
      ends[k] = Math.max(ends[k] || 0, e.end);
      e._sub = k;
    }
    r._n = Math.max(1, ends.length);
    r._h = Math.max(22, 10 + r._n * (BAR + GAP));
    r._y = y;
    y += r._h;
  }
  const H = y + M.b + (rows.length ? 0 : 40);
  p.host.style.minHeight = H + "px";
  frame(p, H, M);
  drawMarkers(p, false);
  for (const r of rows) {
    const cy = r._y + r._h / 2;
    const t = svgEl("text", { x: M.l - 10, y: cy + 4, class: "row-label", "text-anchor": "end" }, p.g.axes);
    t.textContent = trimText(r.label, Math.max(6, Math.floor((M.l - 14) / 6.4)));
    svgEl("title", null, t).textContent = r.title;
    svgEl("line", { x1: M.l, x2: W - M.r, y1: Math.round(r._y + r._h) + 0.5, y2: Math.round(r._y + r._h) + 0.5, class: "cover" }, p.g.events);
    const top = cy - (r._n * (BAR + GAP) - GAP) / 2;
    for (const e of r.items) {
      const A = p.sx(Math.max(e.start, state.x0)), B = p.sx(Math.min(e.end, state.x1)), w = Math.max(3, B - A), yy = top + e._sub * (BAR + GAP);
      e._x0 = A; e._x1 = A + w; e._y0 = yy; e._row = r;
      const rect = svgEl("rect", { x: A.toFixed(1), y: yy.toFixed(1), width: w.toFixed(1), height: BAR, rx: 2 }, p.g.marks);
      rect.style.fill = memberColor(e.m);
      if (e.running && e.end <= state.x1) svgEl("path", { d: "M" + (A + w + 1).toFixed(1) + " " + (yy - 1).toFixed(1) + "l5 " + (BAR / 2 + 1) + "l-5 " + (BAR / 2 + 1) + "z", class: "run-mark" }, p.g.marks).style.fill = memberColor(e.m);
    }
  }
  if (!rows.length) emptyNote(p, o.empty || "No slow invocation in this selection");
  clear(p.legend);
  const ms = Array.from(new Set(rows.reduce((a, r) => a.concat(r.items.map(e => e.m)), []))).sort((a, b) => a - b);
  for (const m of ms) { const it = el("span", "lg lg-static", p.legend), s = el("span", "sw", it); s.style.background = memberColor(m); el("span", null, it, MEMBERS[m].label); }
  if (rows.some(r => r.items.some(e => e.running))) el("span", "lg-note", p.legend, o.run || "▸ still running at the member's last dump");
  el("span", "lg-note", p.legend, o.bars || "one bar per invocation, from its start to its end");
  oomeLegend(p.legend);
  p.legend.hidden = !rows.length;
  p.tipAt = (t, py, px) => {
    const r = rows.find(x => py >= x._y && py < x._y + x._h);
    if (!r) return null;
    let best = null, bd = 12;
    for (const e of r.items) {
      const dx = px < e._x0 ? e._x0 - px : px > e._x1 ? px - e._x1 : 0, dy = py < e._y0 ? e._y0 - py : py > e._y0 + BAR ? py - e._y0 - BAR : 0;
      const d = dx + dy * 2;
      if (d < bd) { bd = d; best = e; }
    }
    if (!best) return null;
    clear(p.hot);
    svgEl("rect", { x: best._x0 - 2, y: best._y0 - 3, width: best._x1 - best._x0 + 4, height: BAR + 6, rx: 3, class: "bar-hot" }, p.hot);
    return box => (o.tip || soTip)(box, best);
  };
  finish(p);
}
function soTip(box, e) {
  el("div", "tip-t", box, e.short + " · " + MEMBERS[e.m].label);
  el("div", "tip-sub", box, shortTime(e.start) + " → " + soEnd(e));
  tipRow(box, memberColor(e.m), dur(e.dur / 1000), e.running ? "so far, still running at the member's last dump" : "running", false);
  if (e.name) el("div", "tip-sub", box, "name: " + e.name + (e.part != null && e.part >= 0 ? " · partition " + e.part : ""));
  else if (e.part != null && e.part >= 0) el("div", "tip-sub", box, "partition " + e.part);
  el("div", "tip-foot", box, "Seen in the detector's dumps from " + shortTime(e.first) + " to " + shortTime(e.last) + " · click the table row for its stack trace");
}
function buildSlowOps(sec) {
  if (!SO.dumps.length) {
    el("p", "lead", sec, "No SlowOperations sections in these files. The slow operation detector writes them every minute " +
      "(hazelcast.diagnostics.slowoperations.period.seconds) while hazelcast.slow.operation.detector.enabled is true, the default; " +
      "it lists the operations that ran longer than hazelcast.slow.operation.detector.threshold.millis (10 s) on an operation thread.");
    if (HASLOGS && LOGS.events.some(e => LOGCATS[e[2]][0] === "slowop")) {
      const p = el("p", "lead", sec, "The member logs have “Slow operation detected” warnings: ");
      const b = el("button", "btn btn-s", p, "Open them"); b.type = "button";
      b.addEventListener("click", () => { state.logCat = "slowop"; setTab("logs"); });
    }
    return;
  }
  const per = SO.dumps.length ? Math.round((SO.dumps[0][3] - SO.dumps[0][2]) / Math.max(1, SO.dumps[0][1] - 1) / 1000) : 60;
  if (!SO_INV.length) {
    const n = SO.dumps.reduce((s, d) => s + d[1], 0), a = Math.min.apply(null, SO.dumps.map(d => d[2])), z = Math.max.apply(null, SO.dumps.map(d => d[3]));
    el("p", "lead", sec, "The slow operation detector reported no slow operation: " + fmtInt(n) + " empty SlowOperations section" + (n === 1 ? "" : "s") +
      " from " + fmtInt(SO.dumps.length) + " member" + (SO.dumps.length === 1 ? "" : "s") + " (every " + (per > 0 ? dur(per) : "minute") + ", " + span(a, z) +
      "): no operation ran longer than the detector's threshold (hazelcast.slow.operation.detector.threshold.millis, 10 s by default) on an operation thread.");
    return;
  }
  el("p", "lead", sec, "From the SlowOperations sections (every " + (per > 0 ? dur(per) : "minute") + "): operations that ran on a partition or generic " +
    "operation thread for longer than the detector's threshold (hazelcast.slow.operation.detector.threshold.millis, 10 s by default), with the stack " +
    "trace they were caught in. The detector lists each one again at every dump for an hour after it last ran, so each is shown once here, with the " +
    "longest duration seen; one still running at its member's last dump is marked ▸.");
  const ol = el("label", "ctl", ctxRow("slowops"));
  el("span", "ctl-label", ol, "Operation");
  const os = el("select", "opsel", ol);
  os.addEventListener("change", () => { soState.op = os.value || null; soState.sel = null; update(); });
  let opsKey = null;
  syncers.push(() => {
    if (state.tab !== "slowops") return;
    const ops = Array.from(new Set(soRows(true).map(r => r.op))).sort((a, b) => shortOp(a) < shortOp(b) ? -1 : 1);
    if (soState.op && ops.indexOf(soState.op) < 0) { soState.op = null; soState.sel = null; }
    const key = ops.join("\n");
    if (key !== opsKey) {
      opsKey = key;
      clear(os);
      el("option", null, os, ops.length === 1 ? "The only operation" : "All " + ops.length + " operations").value = "";
      for (const op of ops) { const o = el("option", null, os, shortOp(op)); o.value = op; o.title = op; }
    }
    os.value = soState.op || "";
  });
  const kp = el("div", "kpis", sec);
  tabUpdaters.push(Object.assign(() => {
    clear(kp);
    const rows = soRows();
    kpi(kp, "Slow invocations", fmtInt(rows.length), "in the time range in view" + (SO.capped ? " · the longest " + fmtInt(SO.inv.length) + " kept" : ""));
    kpi(kp, "Operations", fmtInt(new Set(rows.map(r => r.op)).size), fmtInt(new Set(rows.map(r => r.m)).size) + " member" + (new Set(rows.map(r => r.m)).size === 1 ? "" : "s"));
    if (rows.length) {
      const w = rows.reduce((a, b) => b.dur > a.dur ? b : a);
      kpi(kp, "Longest", dur(w.dur / 1000), w.short + " · " + MEMBERS[w.m].label + (w.running ? " · still running" : ""));
      const tot = rows.reduce((s, r) => s + r.dur, 0);
      kpi(kp, "Operation thread time", dur(tot / 1000), "spent in these invocations");
    }
    const run = rows.filter(r => r.running);
    if (run.length) { const k = kpi(kp, "Still running", fmtInt(run.length), "at their member's last dump"); k.classList.add("alert"); k.querySelector(".kpi-value").classList.add("bad"); }
    const lg = soLog();
    if (HASLOGS && LOGS.events.some(e => LOGCATS[e[2]][0] === "slowop")) {
      const k = kpi(kp, "Warnings in member logs", fmtInt(lg.length), "“Slow operation detected” · open them");
      k.classList.add("link"); k.tabIndex = 0; k.setAttribute("role", "link"); k.title = "Open the member logs on these lines";
      const go = () => { state.logCat = "slowop"; setTab("logs"); };
      k.addEventListener("click", go);
      k.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    }
  }, { tab: "slowops" }));
  const lanes = addPanel("slowops", sec, { title: "Slow invocations", height: 140, noTable: true, render: p => {
    const by = new Map(), byName = state.group === "name";
    for (const r of soRows()) {
      const k = byName ? r.op : r.op + "\u0001" + r.m;
      let g = by.get(k);
      if (!g) { g = { label: byName ? r.short : r.short + " · " + mShort(r.m), title: r.op + (byName ? "" : " · " + MEMBERS[r.m].label), items: [], tot: 0 }; by.set(k, g); }
      g.items.push(r); g.tot += r.dur;
    }
    const all = Array.from(by.values()).sort((a, b) => b.tot - a.tot), rows = all.slice(0, 40);
    p.note.textContent = (all.length > rows.length ? "the " + rows.length + " with the most slow time, of " + all.length : all.length + " row" + (all.length === 1 ? "" : "s")) +
      (byName ? " · one row per operation" : " · one row per operation and member");
    renderSlowLanes(p, rows);
  } });
  lanes.fixed = true;
  // per operation
  const ocard = el("section", "card", sec);
  const ocap = el("div", "cap", ocard);
  el("h3", null, ocap, "Operations");
  const onote = el("span", "note", ocap);
  const owrap = el("div", "table-wrap", ocard);
  tabUpdaters.push(Object.assign(() => {
    clear(owrap);
    const by = new Map();
    for (const r of soRows(true)) {
      let g = by.get(r.op);
      if (!g) { g = { op: r.op, short: r.short, members: new Set(), n: 0, tot: 0, mx: 0, first: r.start, last: r.end, stacks: new Set(), counted: 0, running: 0 }; by.set(r.op, g); }
      g.members.add(r.m); g.n++; g.tot += r.dur; g.mx = Math.max(g.mx, r.dur); g.first = Math.min(g.first, r.start); g.last = Math.max(g.last, r.end);
      g.stacks.add(r.st); if (r.running) g.running++;
    }
    for (const g of by.values()) { const seen = new Set(); for (const r of SO_INV) if (r.op === g.op && !seen.has(r.m + ":" + r.st)) { seen.add(r.m + ":" + r.st); g.counted += soCounted(r.m, r.oi, r.st) || 0; } }
    const rows = Array.from(by.values());
    onote.textContent = fmtInt(rows.length) + " operation" + (rows.length === 1 ? "" : "s") + " · click one to show only it";
    sortableTable(owrap, [
      { key: "op", label: "Operation", val: g => g.short, cls: "op", title: g => g.op },
      { key: "mem", label: "Members", num: true, val: g => g.members.size },
      { key: "n", label: "Slow invocations", num: true, val: g => g.n, fmt: g => fmtInt(g.n) + (g.running ? " (" + g.running + " running)" : "") },
      { key: "counted", label: "Counted by the detector", num: true, val: g => g.counted, fmt: g => g.counted ? fmtInt(g.counted) : "" },
      { key: "mx", label: "Longest", num: true, val: g => g.mx, fmt: g => dur(g.mx / 1000) },
      { key: "tot", label: "Total time", num: true, val: g => g.tot, fmt: g => dur(g.tot / 1000) },
      { key: "st", label: "Stack traces", num: true, val: g => g.stacks.size },
      { key: "first", label: "First", num: true, val: g => g.first, fmt: g => shortTime(g.first) },
      { key: "last", label: "Last", num: true, val: g => g.last, fmt: g => shortTime(g.last) }
    ], rows, soState.osort, () => update(), g => { soState.op = soState.op === g.op ? null : g.op; soState.sel = null; update(); },
    "No slow invocation in this selection.", g => soState.op === g.op);
  }, { tab: "slowops" }));
  // every invocation
  const card = el("section", "card", sec);
  const cap = el("div", "cap", card);
  el("h3", null, cap, "Slow invocations");
  const tnote = el("span", "note", cap);
  const wrap = el("div", "table-wrap", card);
  const detail = el("div", null, sec);
  let shown = null;
  tabUpdaters.push(Object.assign(() => {
    clear(wrap); clear(detail);
    const rows = soRows();
    tnote.textContent = fmtInt(rows.length) + " invocation" + (rows.length === 1 ? "" : "s") + " · click one for its stack trace and details";
    sortableTable(wrap, [
      { key: "m", label: "Member", val: r => MEMBERS[r.m].label, fmt: r => mShort(r.m), title: r => MEMBERS[r.m].label },
      { key: "op", label: "Operation", val: r => r.short, cls: "op", title: r => r.op },
      { key: "name", label: "Name", val: r => r.name },
      { key: "part", label: "Partition", num: true, val: r => r.part == null ? -2 : r.part, fmt: r => r.part == null || r.part < 0 ? "" : String(r.part) },
      { key: "start", label: "Started", num: true, val: r => r.start, fmt: r => shortTime(r.start) },
      { key: "end", label: "Ended", num: true, val: r => r.end, fmt: r => soEnd(r) },
      { key: "dur", label: "Duration", num: true, val: r => r.dur, fmt: r => (r.running ? "≥ " : "") + dur(r.dur / 1000) }
    ], rows, soState.sort, () => update(), r => { soState.sel = soState.sel === r.i ? null : r.i; update(); }, "No slow invocation in this selection.", r => soState.sel === r.i, 1000);
    const r = soState.sel != null ? SO_INV[soState.sel] : null;
    if (!r || rows.indexOf(r) < 0) { shown = null; return; }
    const c = el("section", "card", detail), cc = el("div", "cap", c);
    el("h3", null, cc, r.short + " · " + MEMBERS[r.m].label);
    el("span", "note", cc, shortTime(r.start) + " → " + soEnd(r) + " · " + (r.running ? "≥ " : "") + dur(r.dur / 1000));
    const n = soCounted(r.m, r.oi, r.st);
    el("p", "lead", c, (n ? "The detector counted " + fmtInt(n) + " slow invocation" + (n === 1 ? "" : "s") + " with this stack trace on this member. " : "") +
      "Seen in its dumps from " + shortTime(r.first) + " to " + shortTime(r.last) + ".");
    el("div", "ctl-label", c, "Stack trace (where the operation thread was when the detector caught it)");
    el("pre", "sample", c, SO.stacks[r.st] || "");
    el("div", "ctl-label", c, "Operation details");
    el("pre", "sample wrappre", c, r.det);
    if (shown !== r.i) { shown = r.i; c.scrollIntoView({ block: "nearest" }); }
  }, { tab: "slowops" }));
}

/* ---------- Invocations: what the members were waiting on (the invocation sampler's Invocations sections) */
const ivShort = d => { const i = d.indexOf("("); return i > 0 ? shortOp(d.slice(0, i)) + "(" + shortOp(d.slice(i + 1).replace(/\)$/, "")) + ")" : shortOp(d); };
const IV_PER = new Map(IV.dumps.map(d => [d[0], d[5] || 60000]));
const IV_COVER = new Map(IV.dumps.map(d => [d[0], d[6] || [[d[2], d[3]]]]));
const IV_SER = IV.ser.map(c => {
  const n = c.t.length, t = new Float64Array(n);
  let x = 0;
  for (let i = 0; i < n; i++) { x += c.t[i]; t[i] = x; }
  const dec = list => list.map(([o, flat]) => {
    const idx = new Int32Array(flat.length / 2), val = new Float64Array(flat.length / 2);
    for (let j = 0; j < flat.length; j += 2) { idx[j / 2] = flat[j]; val[j / 2] = flat[j + 1]; }
    return [o, idx, val];
  });
  return { m: c.m, t, p: dec(c.p), s: dec(c.s) };
});
const IV_INV = IV.inv.map((x, i) => ({ i, m: x[0], oi: x[1], op: IV.ops[x[1]], short: ivShort(IV.ops[x[1]]), name: x[2] || "", part: x[3], target: x[4] || "",
  local: !!x[5], tm: x[6], start: x[7], dur: x[8], first: x[9], last: x[10], end: x[10], n: x[11], timeout: x[12], invokes: x[13], bexp: x[14], brecv: x[15],
  resp: x[16] || "", conn: x[17] || "", hb: x[18], state: x[19], running: x[19] === 2, raw: x[20] >= 0 ? IV.raw[x[20]] : null }));
const IV_TRUNC = IV.dumps.reduce((a, d) => a + d[4], 0);
const ivState = { op: null, part: null, sel: null, sort: { key: "dur", dir: -1 }, osort: { key: "slow", dir: -1 }, psort: { key: "n", dir: -1 } };
const ivTarget = e => e.local ? "this member" : e.tm >= 0 ? MEMBERS[e.tm].label : e.target || "unknown";
const ivLate = e => e.timeout > 0 && e.dur > e.timeout;
const ivStateText = e => e.state === 2 ? "still pending at the member's last dump" : e.state === 1 ? "gone by the next dump (answered, failed or timed out)" :
  "not known after its last listing: the next dump listed only the first 99";
// the partition thread that runs a partition's operations on a member: partition id modulo operation.partitionThreadCount
function ivPartThread(part, m) {
  if (part == null || part < 0 || m == null || m < 0) return "";
  let n = staticValue("operation.partitionThreadCount", m);
  if (!(n > 0)) {
    const r = MREC.get("operation.partitionThreadCount"), s = r ? mSeries(r).find(x => x.m === m) : null;
    if (s) for (let i = s.v.length - 1; i >= 0; i--) if (s.v[i] === s.v[i]) { n = s.v[i]; break; }
  }
  return n > 0 ? "partition-operation.thread-" + (part % n) : "";
}
// Find narrows the charts to the operations it matches (to none: it leaves them), the lists to what it matches
function ivOpFilter() {
  const q = findQ(), qs = q ? new Set(IV.ops.map((o, i) => o.toLowerCase().indexOf(q) >= 0 ? i : -1).filter(i => i >= 0)) : null;
  return oi => (!ivState.op || IV.ops[oi] === ivState.op) && (!qs || !qs.size || qs.has(oi));
}
function ivMatch(r, q) {
  return !q || r.op.toLowerCase().indexOf(q) >= 0 || r.name.toLowerCase().indexOf(q) >= 0 || MEMBERS[r.m].label.toLowerCase().indexOf(q) >= 0 ||
    r.target.toLowerCase().indexOf(q) >= 0 || (r.part >= 0 && (q === String(r.part) || q === "partition " + r.part));
}
// the invocations listed as slow in view: pending at some time in the range, on the members picked, matching the lists and Find
function ivRows(ignoreOp, ignorePart) {
  const ss = selSet(), q = findQ();
  return IV_INV.filter(r => ss.has(r.m) && r.start <= state.x1 && r.last >= state.x0 && (ignoreOp || !ivState.op || r.op === ivState.op) &&
    (ignorePart || ivState.part == null || r.part === ivState.part) && ivMatch(r, q));
}
// per member and sample in view: the invocations pending (kind "p") or pending longer than the slow threshold ("s"), the operations picked
function ivTotals(kind) {
  const ss = selSet(), ok = ivOpFilter(), out = [];
  for (const c of IV_SER) {
    if (!ss.has(c.m)) continue;
    const tot = new Float64Array(c.t.length);
    for (const [oi, idx, val] of c[kind]) if (ok(oi)) for (let j = 0; j < idx.length; j++) tot[idx[j]] += val[j];
    out.push({ m: c.m, t: c.t, v: tot });
  }
  return out;
}
// many points for the width: the highest of each pixel or two
function ivThin(pts, width) {
  if (pts.length <= 2 * width) return { pts, bw: 0 };
  const bw = (state.x1 - state.x0) / Math.max(100, width), out = [];
  let cur = null, key = null;
  for (const q of pts) { const k = Math.floor(q[0] / bw); if (k !== key) { if (cur) out.push(cur); cur = q; key = k; } else if (q[1] > cur[1]) cur = q; }
  if (cur) out.push(cur);
  return { pts: out, bw };
}
// the chart lines: one per member (the operations picked, added up) or, Lines: By operation, one per operation (the members picked, added up)
function ivSeries(kind, width) {
  const x0 = state.x0, x1 = state.x1, pad = (x1 - x0) * 0.02 + 120000, out = [];
  if (state.group !== "name") {
    for (const c of ivTotals(kind)) {
      const pts = [];
      for (let i = 0; i < c.t.length; i++) if (c.t[i] >= x0 - pad && c.t[i] <= x1 + pad) pts.push([c.t[i], c.v[i]]);
      const th = ivThin(pts, width), P = IV_PER.get(c.m) || 60000;
      out.push({ key: "m:" + c.m, label: MEMBERS[c.m].label, color: memberColor(c.m), faint: c.m >= 8, pts: th.pts, join: Math.max(2.5 * P, 2.5 * th.bw) });
    }
    return out;
  }
  const ss = selSet(), ok = ivOpFilter(), sers = IV_SER.filter(c => ss.has(c.m));
  if (!sers.length) return out;
  const P = Math.min.apply(null, sers.map(c => IV_PER.get(c.m) || 60000)), slots = new Set(), per = new Map();
  for (const c of sers) {
    for (let i = 0; i < c.t.length; i++) if (c.t[i] >= x0 - pad && c.t[i] <= x1 + pad) slots.add(Math.floor(c.t[i] / P));
    for (const [oi, idx, val] of c[kind]) {
      if (!ok(oi)) continue;
      const mine = new Map();                       // a member's value in each slot (the highest, should two samples share one)
      for (let j = 0; j < idx.length; j++) {
        const t = c.t[idx[j]];
        if (t < x0 - pad || t > x1 + pad) continue;
        const b = Math.floor(t / P);
        mine.set(b, Math.max(mine.get(b) || 0, val[j]));
      }
      let mp = per.get(oi);
      if (!mp) per.set(oi, mp = new Map());
      for (const [b, v] of mine) mp.set(b, (mp.get(b) || 0) + v);
    }
  }
  const sl = Array.from(slots).sort((a, b) => a - b);
  const ops = Array.from(per.keys()).filter(oi => Array.from(per.get(oi).values()).some(v => v > 0)).sort((a, b) => a - b);
  for (const oi of ops.slice(0, 12)) {
    const mp = per.get(oi), th = ivThin(sl.map(b => [b * P, mp.get(b) || 0]), width);
    out.push({ key: "o:" + IV.ops[oi], label: ivShort(IV.ops[oi]), color: oi < 8 ? catColor(oi) : null, faint: oi >= 8, pts: th.pts, join: Math.max(2.5 * P, 2.5 * th.bw) });
  }
  return out;
}
const ivCover = () => unionCover(selMembers().filter(m => IV_COVER.has(m)), m => IV_COVER.get(m));
// the highest value at one sample in view, over the members picked: [value, member, time], and the average per member and sample
function ivPeak(kind) {
  let best = null, sum = 0, n = 0;
  for (const c of ivTotals(kind)) for (let i = 0; i < c.t.length; i++) {
    if (c.t[i] < state.x0 || c.t[i] > state.x1) continue;
    sum += c.v[i]; n++;
    if (!best || c.v[i] > best[0]) best = [c.v[i], c.m, c.t[i]];
  }
  return best ? { v: best[0], m: best[1], t: best[2], avg: sum / n } : null;
}
function ivTip(box, e) {
  el("div", "tip-t", box, e.short + (e.name ? " · " + e.name : "") + " · " + MEMBERS[e.m].label);
  el("div", "tip-sub", box, "invoked " + shortTime(e.start) + " · last listed " + shortTime(e.last));
  tipRow(box, memberColor(e.m), "≥ " + dur(e.dur / 1000), "pending" + (ivLate(e) ? ", past its call timeout of " + dur(e.timeout / 1000) : ""), false);
  el("div", "tip-sub", box, (e.part >= 0 ? "partition " + e.part + " · " : "") + "target: " + ivTarget(e));
  el("div", "tip-foot", box, ivStateText(e) + " · listed in " + e.n + " dump" + (e.n === 1 ? "" : "s") + " · click the table row for its details");
}
function buildInvocations(sec) {
  const per = Math.min.apply(null, IV.dumps.map(d => d[5] || 60000)), once = IV.dumps.every(d => d[1] <= 1);
  el("p", "lead", sec, "From the Invocations sections of the invocation sampler (hazelcast.diagnostics.invocation.sample.period.seconds" +
    (once ? "" : ", every " + stepName(per) + " here") + "): at each sample, the invocations each member was waiting on, counted by operation, and " +
    "the ones pending for longer than the slow threshold (hazelcast.diagnostics.invocation.slow.threshold.seconds, 5 s by default), listed with " +
    "their data structure, partition, target and how long they had been pending. An invocation is pending from the call until its response and " +
    "backup acknowledgements arrive: a long one usually waits on a partition thread that is busy or blocked on its target (a slow MapStore, a " +
    "lock, a long query or entry processor), or on a target that is gone. Each listed invocation is shown once, with the longest it was seen pending." +
    (IV_TRUNC ? " " + fmtInt(IV_TRUNC) + " dump" + (IV_TRUNC === 1 ? " had" : "s had") + " more slow invocations than the sampler prints " +
      "(hazelcast.diagnostics.invocation.slow.max.count, 100): " + (IV_TRUNC === 1 ? "its list has" : "their lists have") + " the first 99, the counts have them all." : "") +
    (once ? " With one dump per member, the counts are the totals since the members started: the charts need two dumps or more." : ""));
  // the operation list (top bar) and the partition picked in the Partitions table
  const ol = el("label", "ctl", ctxRow("invocations"));
  el("span", "ctl-label", ol, "Operation");
  const os = el("select", "opsel ivop", ol);
  os.addEventListener("change", () => { ivState.op = os.value || null; ivState.sel = null; update(); });
  let opsKey = null;
  syncers.push(() => {
    if (state.tab !== "invocations") return;
    const ss = selSet(), seen = new Set();
    for (const c of IV_SER) if (ss.has(c.m)) for (const [oi] of c.p.concat(c.s)) seen.add(oi);
    for (const r of IV_INV) if (ss.has(r.m)) seen.add(r.oi);
    for (const x of IV.tot) if (ss.has(x[0])) seen.add(x[1]);
    const ops = Array.from(seen).map(oi => IV.ops[oi]).sort((a, b) => ivShort(a).toLowerCase() < ivShort(b).toLowerCase() ? -1 : 1);
    if (ivState.op && ops.indexOf(ivState.op) < 0) ivState.op = null;
    const key = ops.join("\n");
    if (key !== opsKey) {
      opsKey = key;
      clear(os);
      el("option", null, os, ops.length === 1 ? "The only operation" : "All " + ops.length + " operations").value = "";
      for (const op of ops) { const o = el("option", null, os, ivShort(op)); o.value = op; o.title = op; }
    }
    os.value = ivState.op || "";
  });
  const bar = el("div", "selbar", sec); bar.hidden = true;
  tabUpdaters.push(Object.assign(() => {
    clear(bar);
    bar.hidden = ivState.part == null;
    if (bar.hidden) return;
    bar.appendChild(document.createTextNode("Showing the invocations listed for "));
    el("b", null, bar, "partition " + ivState.part);
    const b = el("button", "btn btn-s", bar, "Show all"); b.type = "button";
    b.addEventListener("click", () => { ivState.part = null; ivState.sel = null; update(); });
  }, { tab: "invocations" }));
  const kp = el("div", "kpis", sec);
  tabUpdaters.push(Object.assign(() => {
    clear(kp);
    const pk = ivPeak("p"), sk = ivPeak("s"), rows = ivRows();
    const at = x => x.v > 0 ? "at " + shortTime(x.t) + " · " + mShort(x.m) + " · " : "";
    if (pk) kpi(kp, "Pending at a sample", fmtCount(pk.v), at(pk) + "average " + fmtCount(pk.avg));
    if (sk) {
      const k = kpi(kp, "Pending longer than the slow threshold", fmtCount(sk.v), at(sk) + "average " + fmtCount(sk.avg));
      if (sk.v > 0) k.querySelector(".kpi-value").classList.add("bad");
    }
    if (!pk && !sk) {
      const tot = IV.tot.filter(x => selSet().has(x[0]) && (!ivState.op || IV.ops[x[1]] === ivState.op));
      kpi(kp, "Pending, counted since start", fmtInt(tot.reduce((a, x) => a + x[2], 0)), fmtInt(tot.reduce((a, x) => a + x[3], 0)) + " of them slow · one dump: no change to chart");
    }
    if (rows.length) {
      const w = rows.reduce((a, b) => b.dur > a.dur ? b : a);
      const k = kpi(kp, "Longest pending", dur(w.dur / 1000), w.short + (w.name ? " · " + w.name : "") + (w.part >= 0 ? " · partition " + w.part : "") + " · " + mShort(w.m) + (w.running ? " · still pending" : ""));
      if (ivLate(w)) k.querySelector(".kpi-value").classList.add("bad");
      const late = rows.filter(ivLate).length;
      const kl = kpi(kp, "Past their call timeout", fmtInt(late), late ? "pending longer than their call timeout: the target still runs or queues them" : "none of the " + fmtInt(rows.length) + " listed");
      if (late) { kl.classList.add("alert"); kl.querySelector(".kpi-value").classList.add("bad"); }
    }
    kpi(kp, "Listed as slow", fmtInt(rows.length), (IV.capped ? "the longest " + fmtInt(IV.inv.length) + " kept · " : "") + "invocations in the time range in view");
    const parts = new Map();
    for (const r of rows) if (r.part >= 0) parts.set(r.part, (parts.get(r.part) || 0) + 1);
    if (parts.size) {
      const top = Array.from(parts.entries()).sort((a, b) => b[1] - a[1]).slice(0, 3);
      kpi(kp, "Partitions", fmtInt(parts.size), "most listed: " + top.map(x => x[0] + " (" + fmtInt(x[1]) + ")").join(", "));
    }
  }, { tab: "invocations" }));
  const chart = (kind, title, code, note) => {
    const p = addPanel("invocations", sec, { title, code, height: 210, render: p => {
      const series = ivSeries(kind, plotWidth(p));
      drawTS(p, { series, unit: "count", fmt: fmtCount, cover: ivCover, coverLabel: "no sampler dump",
        note: note + (state.group === "name" ? " · one line per operation" : " · one line per member") });
    } });
    p.fixed = true;
    return p;
  };
  chart("p", "Pending invocations", "History", "at each sample");
  chart("s", "Pending longer than the slow threshold", "SlowHistory", "at each sample, pending for 5 s or more by default");
  const lanes = addPanel("invocations", sec, { title: "Invocations listed as slow", code: "Pending", height: 140, noTable: true, render: p => {
    const by = new Map(), byName = state.group === "name";
    for (const r of ivRows()) {
      const k = r.op + "\u0001" + r.name + (byName ? "" : "\u0001" + r.m);
      let g = by.get(k);
      if (!g) { g = { label: r.short + (r.name ? " · " + r.name : "") + (byName ? "" : " · " + mShort(r.m)), title: r.op + (r.name ? " · " + r.name : "") + (byName ? "" : " · " + MEMBERS[r.m].label), items: [], tot: 0 }; by.set(k, g); }
      g.items.push(r); g.tot += r.dur;
    }
    const all = Array.from(by.values()).sort((a, b) => b.tot - a.tot), rows = all.slice(0, 40);
    p.note.textContent = (all.length > rows.length ? "the " + rows.length + " with the most pending time, of " + all.length : all.length + " row" + (all.length === 1 ? "" : "s")) +
      (byName ? " · one row per operation and data structure" : " · one row per operation, data structure and member");
    renderSlowLanes(p, rows, { tip: ivTip, run: "▸ still pending at the member's last dump", bars: "one bar per invocation, from its call to the last dump that listed it",
      empty: "No invocation listed as slow in this selection" });
  } });
  lanes.fixed = true;
  // per operation
  const ocard = el("section", "card", sec), ocap = el("div", "cap", ocard);
  el("h3", null, ocap, "Operations");
  const onote = el("span", "note", ocap);
  const owrap = el("div", "table-wrap", ocard);
  tabUpdaters.push(Object.assign(() => {
    clear(owrap);
    const ss = selSet(), q = findQ(), by = new Map();
    const get = oi => { let g = by.get(oi); if (!g) { g = { oi, op: IV.ops[oi], short: ivShort(IV.ops[oi]), pmx: 0, psum: 0, smx: 0, n: 0, mx: 0, late: 0, th: 0, ts: 0, ms: new Set() }; by.set(oi, g); } return g; };
    let samples = 0;
    for (const c of IV_SER) {
      if (!ss.has(c.m)) continue;
      const lo = lowerBound(c.t, state.x0), hi = upperBound(c.t, state.x1);
      samples += Math.max(0, hi - lo);
      for (const [kind, list] of [["p", c.p], ["s", c.s]]) for (const [oi, idx, val] of list) {
        for (let j = 0; j < idx.length; j++) {
          const i = idx[j];
          if (i < lo || i >= hi) continue;
          const g = get(oi);
          g.ms.add(c.m);
          if (kind === "p") { g.psum += val[j]; g.pmx = Math.max(g.pmx, val[j]); } else g.smx = Math.max(g.smx, val[j]);
        }
      }
    }
    for (const r of ivRows(true, true)) { const g = get(r.oi); g.ms.add(r.m); g.n++; g.mx = Math.max(g.mx, r.dur); if (ivLate(r)) g.late++; }
    for (const x of IV.tot) if (ss.has(x[0])) { const g = get(x[1]); g.th += x[2]; g.ts += x[3]; }
    const rows = Array.from(by.values()).filter(g => !q || g.op.toLowerCase().indexOf(q) >= 0 || g.n);
    onote.textContent = fmtInt(rows.length) + " operation" + (rows.length === 1 ? "" : "s") + " · at a sample: on one member · click one to show only it";
    sortableTable(owrap, [
      { key: "op", label: "Operation", val: g => g.short, cls: "op", title: g => g.op },
      { key: "pmx", label: "Most pending", num: true, val: g => g.pmx, fmt: g => g.pmx ? fmtCount(g.pmx) : "" },
      { key: "avg", label: "Average pending", num: true, val: g => samples ? g.psum / samples : 0, fmt: g => samples && g.psum ? fmtCount(g.psum / samples) : "" },
      { key: "slow", label: "Most slow", num: true, val: g => g.smx * 1e6 + g.n, fmt: g => g.smx ? fmtCount(g.smx) : "" },
      { key: "n", label: "Listed", num: true, val: g => g.n, fmt: g => g.n ? fmtInt(g.n) + (g.late ? " (" + fmtInt(g.late) + " past timeout)" : "") : "" },
      { key: "mx", label: "Longest", num: true, val: g => g.mx, fmt: g => g.mx ? dur(g.mx / 1000) : "" },
      { key: "th", label: "Counted since start", num: true, val: g => g.th, fmt: g => fmtInt(g.th) + (g.ts ? " · " + fmtInt(g.ts) + " slow" : "") }
    ], rows, ivState.osort, () => update(), g => { ivState.op = ivState.op === g.op ? null : g.op; ivState.sel = null; update(); },
    "No invocation in this selection.", g => ivState.op === g.op);
  }, { tab: "invocations" }));
  // per partition
  const pcard = el("section", "card", sec), pcap = el("div", "cap", pcard);
  el("h3", null, pcap, "Partitions");
  const pnote = el("span", "note", pcap);
  const pwrap = el("div", "table-wrap", pcard);
  tabUpdaters.push(Object.assign(() => {
    clear(pwrap);
    const by = new Map();
    for (const r of ivRows(false, true)) {
      let g = by.get(r.part);
      if (!g) { g = { part: r.part, n: 0, mx: 0, late: 0, ops: new Set(), names: new Set(), tg: new Map(), ms: new Set() }; by.set(r.part, g); }
      g.n++; g.mx = Math.max(g.mx, r.dur); if (ivLate(r)) g.late++;
      g.ops.add(r.short); if (r.name) g.names.add(r.name); g.ms.add(r.m);
      const tk = r.local ? r.m : r.tm >= 0 ? r.tm : r.target;
      const c = g.tg.get(tk) || { n: 0, local: 0 };
      c.n++; if (r.local) c.local++;
      g.tg.set(tk, c);
    }
    const rows = Array.from(by.values());
    for (const g of rows) {
      const tg = Array.from(g.tg.entries()).sort((a, b) => b[1].n - a[1].n);
      g.target = tg.map(x => (typeof x[0] === "number" ? mShort(x[0]) : x[0]) + (x[1].local === x[1].n ? " (local calls)" : "")).join(", ");
      g.thread = typeof tg[0][0] === "number" ? ivPartThread(g.part, tg[0][0]) : "";
    }
    const anyThread = rows.some(g => g.thread);
    pnote.textContent = fmtInt(rows.length) + " partition" + (rows.length === 1 ? "" : "s") + " with invocations listed as slow · click one to show only its invocations";
    sortableTable(pwrap, [
      { key: "part", label: "Partition", num: true, val: g => g.part, fmt: g => g.part >= 0 ? String(g.part) : "none (generic)" },
      { key: "n", label: "Listed", num: true, val: g => g.n, fmt: g => fmtInt(g.n) + (g.late ? " (" + fmtInt(g.late) + " past timeout)" : "") },
      { key: "mx", label: "Longest", num: true, val: g => g.mx, fmt: g => dur(g.mx / 1000) },
      { key: "ops", label: "Operations", val: g => Array.from(g.ops).join(", "), cls: "wrapcell" },
      { key: "names", label: "Data structures", val: g => Array.from(g.names).join(", "), cls: "wrapcell" },
      { key: "tg", label: "Target", val: g => g.target, cls: "wrapcell" },
      anyThread ? { key: "th", label: "Partition thread", val: g => g.thread } : null
    ].filter(Boolean), rows, ivState.psort, () => update(), g => { ivState.part = ivState.part === g.part ? null : g.part; ivState.sel = null; update(); },
    "No invocation listed as slow in this selection.", g => ivState.part === g.part);
  }, { tab: "invocations" }));
  // every invocation listed
  const card = el("section", "card", sec), cap = el("div", "cap", card);
  el("h3", null, cap, "Listed invocations");
  const tnote = el("span", "note", cap);
  const wrap = el("div", "table-wrap", card);
  const detail = el("div", null, sec);
  let shown = null;
  tabUpdaters.push(Object.assign(() => {
    clear(wrap); clear(detail);
    const rows = ivRows();
    tnote.textContent = fmtInt(rows.length) + " invocation" + (rows.length === 1 ? "" : "s") + " · ▸ still pending at the member's last dump · click one for its details";
    sortableTable(wrap, [
      { key: "m", label: "Member", val: r => MEMBERS[r.m].label, fmt: r => mShort(r.m), title: r => MEMBERS[r.m].label },
      { key: "op", label: "Operation", val: r => r.short, cls: "op", title: r => r.op },
      { key: "name", label: "Name", val: r => r.name },
      { key: "part", label: "Partition", num: true, val: r => r.part, fmt: r => r.part >= 0 ? String(r.part) : "" },
      { key: "tg", label: "Target", val: r => ivTarget(r), fmt: r => r.local ? "this member" : r.tm >= 0 ? mShort(r.tm) : r.target, title: r => ivTarget(r) },
      { key: "start", label: "Invoked", num: true, val: r => r.start, fmt: r => shortTime(r.start) },
      { key: "last", label: "Last listed", num: true, val: r => r.last, fmt: r => shortTime(r.last) + (r.running ? " ▸" : "") },
      { key: "dur", label: "Pending for", num: true, val: r => r.dur, fmt: r => "≥ " + dur(r.dur / 1000) },
      { key: "to", label: "Call timeout", num: true, val: r => r.timeout || 0, fmt: r => r.timeout > 0 ? dur(r.timeout / 1000) + (ivLate(r) ? " · past" : "") : "" },
      { key: "n", label: "Dumps", num: true, val: r => r.n }
    ], rows, ivState.sort, () => update(), r => { ivState.sel = ivState.sel === r.i ? null : r.i; update(); }, "No invocation listed as slow in this selection.", r => ivState.sel === r.i, 1000);
    const r = ivState.sel != null ? IV_INV[ivState.sel] : null;
    if (!r || rows.indexOf(r) < 0) { shown = null; return; }
    const c = el("section", "card", detail), cc = el("div", "cap", c);
    el("h3", null, cc, r.short + (r.name ? " · " + r.name : "") + " · " + MEMBERS[r.m].label);
    el("span", "note", cc, shortTime(r.start) + " → " + shortTime(r.last) + " · ≥ " + dur(r.dur / 1000));
    el("p", "lead", c, "Invoked at " + shortTime(r.start) + " and listed in " + fmtInt(r.n) + " dump" + (r.n === 1 ? "" : "s") + ", the last at " + shortTime(r.last) +
      ", when it had been pending for " + dur(r.dur / 1000) + ": " + ivStateText(r) + "." + (ivLate(r) ? " That is past its call timeout (" + dur(r.timeout / 1000) +
      "): the target kept answering the invocation's heartbeats, so the operation was still queued or running there." : ""));
    const dl = el("dl", "kv", c);
    const kv = (k, v) => { if (v === "" || v == null) return; el("dt", null, dl, k); el("dd", null, dl, v); };
    kv("Operation", r.op);
    kv("Data structure", r.name);
    kv("Partition", r.part >= 0 ? String(r.part) + (ivPartThread(r.part, r.local ? r.m : r.tm) ? " · " + ivPartThread(r.part, r.local ? r.m : r.tm) + " on the target" : "") : "none: a generic operation");
    kv("Target", ivTarget(r) + (r.local ? " (" + MEMBERS[r.m].label + ")" : ""));
    kv("Call timeout", r.timeout > 0 ? dur(r.timeout / 1000) : "");
    kv("Invoked", r.invokes != null ? r.invokes + " time" + (r.invokes === 1 ? "" : "s") + (r.invokes > 1 ? " (retried)" : "") : "");
    kv("Response", r.resp === "VOID" ? "none yet" : r.resp ? "received (" + r.resp + "): waiting for backup acknowledgements" : "");
    kv("Backup acks", r.bexp != null && r.bexp >= 0 ? fmtInt(r.brecv || 0) + " of " + fmtInt(r.bexp) : "");
    kv("Last heartbeat", r.hb != null ? (r.hb < 60000 ? fmtMs(r.hb) : dur(r.hb / 1000)) + " before the last listing" : "none yet");
    kv("Connection", r.conn === "null" ? "none: the target is this member" : r.conn);
    if (r.raw) { el("div", "ctl-label", c, "As listed the last time"); el("pre", "sample wrappre", c, r.raw); }
    if (shown !== r.i) { shown = r.i; c.scrollIntoView({ block: "nearest" }); }
  }, { tab: "invocations" }));
}

/* ---------- Member logs: Migrations (what the master logged about migration plans and the partition table) */
const MIG = HASLOGS && D.logs.mig ? D.logs.mig : { kinds: [], rows: [], bins: [], procs: [] };
const MIG_PROCS = MIG.procs.map((x, i) => {
  const pts = [];
  for (let j = 0; j + 1 < x[10].length; j += 2) pts.push([x[10][j], x[10][j + 1]]);
  const last = pts.length ? pts[pts.length - 1][0] : x[1];
  return { i, m: x[0], start: x[1], endT: x[2], end: x[2] != null ? x[2] : last, rt: x[3], planned: x[4], completed: x[5], remaining: x[6],
    total: x[7], outcome: x[8], el: x[9], pts, running: !x[8] };
});
const migState = { kind: "notable", sort: { key: "start", dir: 1 }, lsort: { key: "t", dir: 1 } };
const MIG_OUTCOME = { completed: "completed", failed: "failed: a new plan follows", aborted: "aborted: a new plan follows", interrupted: "interrupted", "": "no end in the logs" };
const migBad = e => e.outcome === "failed" || e.outcome === "aborted" || e.outcome === "interrupted";
const migColor = e => migBad(e) ? stuckColor() : e.outcome === "completed" ? catColor(0) : grey();
const migTook = e => e.end - e.start;
// the plans in view, on the members picked
const migProcs = () => { const ss = selSet(); return MIG_PROCS.filter(e => ss.has(e.m) && e.start <= state.x1 && e.end >= state.x0); };
// the kinds of lines, with their times (rows for renderDotLanes)
function migKindRows() {
  const ss = selSet(), by = new Map();
  for (const [ki, mi, , bins] of MIG.bins) {
    if (!ss.has(mi)) continue;
    let r = by.get(ki);
    if (!r) { const k = MIG.kinds[ki]; r = { key: k[0], ki, label: k[1], sev: k[2], color: sevColor(k[2]), bins: [], per: new Map() }; by.set(ki, r); }
    for (const b of bins) r.bins.push(b);
    r.per.set(mi, (r.per.get(mi) || 0) + binsInView(bins));
  }
  for (const r of by.values()) { r.n = 0; for (const v of r.per.values()) r.n += v; }
  return Array.from(by.values()).filter(r => r.n > 0).sort((a, b) => a.ki - b.ki);
}
const migLines = kind => { const ss = selSet(), q = findQ(); return MIG.rows.filter(e => ss.has(e[1]) && inView(e[0]) &&
  (kind === "all" || (kind === "notable" ? MIG.kinds[e[2]][0] !== "progress" : MIG.kinds[e[2]][0] === kind)) &&
  (!q || e[4].toLowerCase().indexOf(q) >= 0 || MEMBERS[e[1]].label.toLowerCase().indexOf(q) >= 0)); };
const migLost = () => migLines("lost");
const LOST_PART_RE = /partitionId=(\d+)/;
function migTip(box, e) {
  el("div", "tip-t", box, "Migration plan · " + MEMBERS[e.m].label);
  el("div", "tip-sub", box, shortTime(e.start) + " → " + (e.endT != null ? shortTime(e.endT) : "last seen " + shortTime(e.end)) + " · " + dur(migTook(e) / 1000));
  tipRow(box, migColor(e), fmtInt(e.completed) + " of " + fmtInt(e.planned), "migrations done · " + (MIG_OUTCOME[e.outcome] || e.outcome), false);
  if (e.remaining && e.outcome !== "completed") el("div", "tip-sub", box, fmtInt(e.remaining) + " left" + (e.running ? " at its last line" : " when it ended"));
  if (e.rt) el("div", "tip-foot", box, "repartitionTime " + e.rt + (e.total != null ? " · " + fmtInt(e.total) + " completed since the master started" : ""));
}
function buildLogsMigrations(sec) {
  logBanner(sec, "migrations and the partition table");
  if (!MIG.rows.length && !MIG.procs.length) {
    el("p", "lead", sec, "No migration line in these logs: the master logs its migration plans (Repartitioning cluster data…, All migration tasks have been completed…) at INFO, so either there was none, the master's log is not here, or com.hazelcast.internal.partition is logged above INFO.");
    return;
  }
  el("p", "lead", sec, "What the master member logged about migrations: each migration plan (Repartitioning cluster data. Migration tasks count: N), its progress " +
    "(Remaining migration tasks…), and how it ended: All migration tasks have been completed, or Rebalance process was failed or aborted, when the master " +
    "drops the rest and makes a new plan (a member joined or left meanwhile, or a migration failed). And the problems: partitions lost (partitionId=N is " +
    "completely lost!: no replica of it is left, so its data is gone), failed promotions and migrations, and changes to the partition table. Only the master " +
    "logs the plans: after a master change, they are in the new master's log.");
  const kp = el("div", "kpis", sec);
  tabUpdaters.push(Object.assign(() => {
    clear(kp);
    const pr = migProcs(), done = pr.filter(e => e.outcome === "completed").length, bad = pr.filter(migBad).length, open = pr.filter(e => e.running).length;
    kpi(kp, "Migration plans", fmtInt(pr.length), pr.length ? fmtInt(done) + " completed" + (bad ? " · " + fmtInt(bad) + " failed or aborted" : "") +
      (open ? " · " + fmtInt(open) + " with no end in the logs" : "") : "none in view");
    if (pr.length) {
      kpi(kp, "Migrations planned", fmtInt(pr.reduce((a, e) => a + e.planned, 0)), fmtInt(pr.reduce((a, e) => a + e.completed, 0)) + " done");
      const w = pr.reduce((a, b) => migTook(b) > migTook(a) ? b : a);
      kpi(kp, "Longest plan", dur(migTook(w) / 1000), fmtInt(w.planned) + " migrations · " + mShort(w.m) + " · " + (MIG_OUTCOME[w.outcome] || w.outcome));
    }
    const lost = migLost(), parts = new Set();
    for (const e of lost) { const m = LOST_PART_RE.exec(e[4]); if (m) parts.add(+m[1]); }
    const k = kpi(kp, "Partitions lost", fmtInt(parts.size || lost.length), lost.length ? (parts.size ? "partition " + Array.from(parts).sort((a, b) => a - b).slice(0, 12).join(", ") +
      (parts.size > 12 ? ", …" : "") + " · " : "") + "last at " + shortTime(lost[lost.length - 1][0]) + " · " + mShort(lost[lost.length - 1][1]) : "no 'is completely lost!' line");
    if (lost.length) { k.classList.add("alert"); k.querySelector(".kpi-value").classList.add("bad"); }
    const fails = migLines("migfail").length + migLines("promotion").length;
    const kf = kpi(kp, "Failed migrations and promotions", fmtInt(fails), fails ? "warnings and errors about them" : "none logged");
    if (fails) kf.querySelector(".kpi-value").classList.add("bad");
  }, { tab: "logs-migrations" }));
  if (MIG_PROCS.length) {
    const rem = addPanel("logs-migrations", sec, { title: "Migrations left", height: 190, render: p => {
      const ss = selSet(), by = new Map();
      for (const e of MIG_PROCS) {
        if (!ss.has(e.m) || e.end < state.x0 - 3600000 || e.start > state.x1 + 3600000) continue;
        if (!by.has(e.m)) by.set(e.m, []);
        const pts = by.get(e.m);
        e.pts.forEach((q, j) => pts.push(j ? q : [q[0], q[1], "", true]));         // a new plan: a new line
        if (e.outcome === "completed" && (!e.pts.length || e.pts[e.pts.length - 1][1] !== 0)) pts.push([e.end, 0]);
      }
      const series = Array.from(by.entries()).sort((a, b) => a[0] - b[0]).map(([m, pts]) =>
        ({ key: "m:" + m, label: MEMBERS[m].label, color: memberColor(m), faint: m >= 8, pts, join: 600000, dots: true }));
      drawTS(p, { series, unit: "count", fmt: v => fmtInt(v) + " migration" + (v === 1 ? "" : "s"), shade: false,
        note: "remaining migrations of each plan, from its first line to its end (Remaining migration tasks lines between)" });
    } });
    rem.fixed = true;
    const lanes = addPanel("logs-migrations", sec, { title: "Migration plans", height: 120, noTable: true, render: p => {
      const ss = selSet(), by = new Map();
      for (const e of migProcs()) { if (!by.has(e.m)) by.set(e.m, []); by.get(e.m).push(Object.assign(e, { a: e.start, l: Math.max(e.end, e.start + 1000) })); }
      const rows = Array.from(by.entries()).sort((a, b) => a[0] - b[0]).map(([m, eps]) => ({ label: MEMBERS[m].label, eps }));
      renderBarLanes(p, rows, { color: migColor, tip: migTip, empty: "No migration plan in this selection" });
      p.legend.hidden = false;
      for (const [c, t] of [[catColor(0), "completed"], [stuckColor(), "failed, aborted or interrupted"], [grey(), "no end in the logs"]]) {
        if (!rows.some(r => r.eps.some(e => migColor(e) === c))) continue;
        const it = el("span", "lg lg-static", p.legend), sw = el("span", "sw", it); sw.style.background = c; el("span", null, it, t);
      }
      el("span", "lg-note", p.legend, "one bar per plan, from its first line to its end, on the master that made it");
      p.note.textContent = ss.size > 1 ? "one row per member" : "";
    } });
    lanes.fixed = true;
  }
  const kinds = addPanel("logs-migrations", sec, { title: "When migration and partition table lines were logged", height: 120, noTable: true, render: p => {
    const rows = migKindRows();
    renderDotLanes(p, rows, { empty: "No migration line in this selection", what: "lines", selected: r => migState.kind === r.key, legend: sevLegend,
      pickHint: "Click to list them below", onPick: r => { migState.kind = migState.kind === r.key ? "notable" : r.key; update(); } });
    p.note.textContent = rows.length ? "click a row to list its lines below" : "";
  } });
  kinds.fixed = true;
  if (MIG_PROCS.length) {
    const pcard = el("section", "card", sec), pcap = el("div", "cap", pcard);
    el("h3", null, pcap, "Migration plans");
    const pnote = el("span", "note", pcap);
    const pwrap = el("div", "table-wrap", pcard);
    tabUpdaters.push(Object.assign(() => {
      clear(pwrap);
      const rows = migProcs();
      pnote.textContent = fmtInt(rows.length) + " plan" + (rows.length === 1 ? "" : "s");
      sortableTable(pwrap, [
        { key: "m", label: "Master", val: e => MEMBERS[e.m].label, fmt: e => mShort(e.m), title: e => MEMBERS[e.m].label },
        { key: "start", label: "Started", num: true, val: e => e.start, fmt: e => shortTime(e.start) },
        { key: "end", label: "Ended", num: true, val: e => e.end, fmt: e => e.endT != null ? shortTime(e.endT) : "–" },
        { key: "took", label: "Took", num: true, val: migTook, fmt: e => (e.running ? "≥ " : "") + dur(migTook(e) / 1000) },
        { key: "p", label: "Planned", num: true, val: e => e.planned, fmt: e => fmtInt(e.planned) },
        { key: "c", label: "Done", num: true, val: e => e.completed, fmt: e => fmtInt(e.completed) },
        { key: "r", label: "Left", num: true, val: e => e.remaining, fmt: e => e.remaining ? fmtInt(e.remaining) : "" },
        { key: "o", label: "Outcome", val: e => MIG_OUTCOME[e.outcome] || e.outcome, lv: e => migBad(e) ? "E" : "" },
        { key: "tot", label: "Completed since start", num: true, val: e => e.total || 0, fmt: e => e.total != null ? fmtInt(e.total) : "" }
      ], rows, migState.sort, () => update(), null, "No migration plan in this selection.");
    }, { tab: "logs-migrations" }));
  }
  const card = el("section", "card", sec), cap = el("div", "cap", card);
  card.id = "mig-lines";
  el("h3", null, cap, "Migration and partition table lines");
  const note = el("span", "note", cap);
  const fl = el("div", "tabctl", card), chips = el("div", "seg", fl);
  chips.setAttribute("role", "group"); chips.setAttribute("aria-label", "Kind of line");
  const wrap = el("div", "table-wrap", card);
  tabUpdaters.push(Object.assign(() => {
    clear(chips); clear(wrap);
    const counts = new Map();
    for (const e of migLines("all")) { const k = MIG.kinds[e[2]][0]; counts.set(k, (counts.get(k) || 0) + 1); }
    if (migState.kind !== "all" && migState.kind !== "notable" && !counts.get(migState.kind)) migState.kind = "notable";
    for (const [k, label] of [["notable", "All but progress"], ["all", "All"]].concat(MIG.kinds.filter(x => counts.get(x[0])).map(x => [x[0], x[1]]))) {
      const b = el("button", null, chips, label + " " + fmtInt(k === "all" || k === "notable" ? migLines(k).length : counts.get(k)));
      b.type = "button"; b.setAttribute("aria-pressed", String(migState.kind === k));
      b.addEventListener("click", () => { migState.kind = k; update(); });
    }
    const rows = migLines(migState.kind);
    note.textContent = fmtInt(rows.length) + " line" + (rows.length === 1 ? "" : "s") + (findQ() ? " matching Find" : "");
    sortableTable(wrap, [
      { key: "t", label: "Time", num: true, val: e => e[0], fmt: e => shortTime(e[0]) },
      { key: "m", label: "Member", val: e => MEMBERS[e[1]].label, fmt: e => mShort(e[1]), title: e => MEMBERS[e[1]].label },
      { key: "k", label: "Kind", val: e => MIG.kinds[e[2]][1], lv: e => MIG.kinds[e[2]][2] >= 2 ? "E" : MIG.kinds[e[2]][2] >= 1 ? "W" : "" },
      { key: "lv", label: "Level", val: e => e[3], lv: e => e[3] },
      { key: "x", label: "Line", val: e => e[4], cls: "wrapcell", lv: e => MIG.kinds[e[2]][2] >= 2 ? "E" : "" }
    ], rows, migState.lsort, () => update(), null, "No line of this kind in this selection.", null, 1000);
  }, { tab: "logs-migrations" }));
}

/* ---------- Configuration: the build, the JVM and the properties each member wrote (BuildInfo, SystemProperties,
   ConfigProperties sections, at the top of each diagnostics file) */
// "-Xmx4g -Dx=y -XX:+UseG1GC -javaagent:/a b/agent.jar=1" -> Map(name -> value); a flag given twice keeps both values
function jvmArgs(text) {
  const out = new Map();
  if (!text) return out;
  for (const a of (" " + text.trim()).split(/ (?=-)/).map(x => x.trim()).filter(x => x)) {
    let k = a, v = "", m;
    if ((m = /^(-X(?:ms|mx|ss|mn))(.+)$/.exec(a))) { k = m[1]; v = m[2]; }
    else if ((m = /^-XX:([+-])([\w.]+)$/.exec(a))) { k = "-XX:" + m[2]; v = m[1] === "+" ? "on" : "off"; }
    else if ((m = /^(-XX:[\w.]+)=(.*)$/.exec(a))) { k = m[1]; v = m[2]; }
    else if ((m = /^(-D[^=]+)=(.*)$/.exec(a))) { k = m[1]; v = m[2]; }
    else if ((m = /^(-(?:javaagent|agentlib|agentpath|Xlog|Xbootclasspath\/a|Xbootclasspath|verbose)):?(.*)$/.exec(a))) { k = m[1]; v = m[2]; }
    else if ((m = /^(--[\w-]+)[= ](.*)$/.exec(a))) { k = m[1]; v = m[2]; }
    out.set(k, out.has(k) ? out.get(k) + "\n" + v : v);
  }
  return out;
}
// per member: the values of its last lifetime that wrote them; the values its other lifetimes had, where they differ
const PR_MEM = (() => {
  const out = new Map(), last = new Map(), start = new Map();
  for (const l of PR.lives) if (!last.has(l[0]) || l[1] > last.get(l[0])) { last.set(l[0], l[1]); start.set(l[0], l[3] != null ? l[3] : l[2]); }
  for (const [mi, ui, si, ki, vi] of PR.rows) {
    let o = out.get(mi);
    if (!o) { o = { secs: [new Map(), new Map(), new Map()], older: new Map(), start: start.get(mi), lives: PR.lives.filter(l => l[0] === mi).length }; out.set(mi, o); }
    if (ui === last.get(mi)) o.secs[si].set(PR.keys[ki], PR.vals[vi]);
    else { const k = si + "\u0001" + PR.keys[ki]; if (!o.older.has(k)) o.older.set(k, new Set()); o.older.get(k).add(PR.vals[vi]); }
  }
  for (const o of out.values()) {
    o.args = jvmArgs(o.secs[1].get("java.vm.args"));
    for (const [k, vs] of o.older) {                 // keep only the values that differ from the last lifetime's
      const [si, key] = k.split("\u0001"), cur = o.secs[+si].get(key);
      for (const v of Array.from(vs)) if (v === cur) vs.delete(v);
      if (!vs.size) o.older.delete(k);
    }
  }
  return out;
})();
const CF_SECS = [["ConfigProperties", "Hazelcast properties"], ["SystemProperties", "System properties"], ["args", "JVM arguments"], ["BuildInfo", "Build"]];
const cfState = { sec: "ConfigProperties", diff: false, sel: null, sort: { key: "k", dir: 1 }, msort: { key: "m", dir: 1 } };
const cfMap = (o, sec) => sec === "args" ? o.args : o.secs[PR.secs.indexOf(sec)] || new Map();
const cfMembers = () => selMembers().filter(m => PR_MEM.has(m));
const cfHz = o => { const b = o.secs[0]; return b.get("Version") ? b.get("Version") + (b.get("Enterprise") === "true" ? " Enterprise" : "") : ""; };
const cfJava = o => {
  const s = o.secs[1], vv = s.get("java.vendor.version"), vendor = vv && vv !== "N/A" ? vv : s.get("java.vendor");
  return s.get("java.version") ? s.get("java.version") + (vendor ? " · " + vendor : "") : "";
};
const cfGc = o => { for (const k of ["UseG1GC", "UseZGC", "UseShenandoahGC", "UseParallelGC", "UseSerialGC", "UseConcMarkSweepGC"]) if (o.args.get("-XX:" + k) === "on") return k.replace(/^Use|GC$/g, "") + (k === "UseConcMarkSweepGC" ? "" : " GC"); return ""; };
// the rows of a section for the members picked: [key, values per member]; differ: not the same on all of them
function cfRows(sec) {
  const ms = cfMembers(), keys = new Set(), q = findQ();
  for (const m of ms) for (const k of cfMap(PR_MEM.get(m), sec).keys()) keys.add(k);
  const rows = [];
  for (const k of keys) {
    const vals = ms.map(m => { const v = cfMap(PR_MEM.get(m), sec).get(k); return v == null ? null : v; });
    const differ = ms.length > 1 && vals.some(v => v !== vals[0]);
    const older = ms.some(m => PR_MEM.get(m).older.has(PR.secs.indexOf(sec) + "\u0001" + k));
    if (q && k.toLowerCase().indexOf(q) < 0 && !vals.some(v => v != null && v.toLowerCase().indexOf(q) >= 0)) continue;
    rows.push({ k, vals, differ, older, ms });
  }
  return rows;
}
const cfDifferCount = sec => cfRows(sec).filter(r => r.differ).length;
const cfShort = v => v == null ? "–" : v === "" ? "(empty)" : trimText(v.replace(/\n/g, " · "), 90);
function buildConfig(sec) {
  el("p", "lead", sec, "What each member wrote at the top of its diagnostics files: its build (BuildInfo), the JVM's system properties (SystemProperties, " +
    "with the JVM arguments in java.vm.args) and the Hazelcast properties set in its configuration or as system properties (ConfigProperties). A member " +
    "with several lifetimes shows its last one; a value that was different before a restart is marked. Find filters the properties and their values.");
  const ctx = ctxRow("config");
  const sl = el("label", "ctl", ctx);
  el("span", "ctl-label", sl, "Show");
  const ss = el("select", "cfsec", sl);
  for (const [k, l] of CF_SECS) { const o = el("option", null, ss, l); o.value = k; }
  ss.addEventListener("change", () => { cfState.sec = ss.value; cfState.sel = null; update(); });
  const dl = el("label", "ctl chk", ctx), dc = el("input", null, dl);
  dc.type = "checkbox";
  el("span", null, dl, "Only those that differ");
  dc.addEventListener("change", () => { cfState.diff = dc.checked; cfState.sel = null; update(); });
  syncers.push(() => { ss.value = cfState.sec; dc.checked = cfState.diff; });
  const kp = el("div", "kpis", sec);
  tabUpdaters.push(Object.assign(() => {
    clear(kp);
    const ms = cfMembers();
    const distinct = f => { const by = new Map(); for (const m of ms) { const v = f(PR_MEM.get(m)); if (!v) continue; if (!by.has(v)) by.set(v, []); by.get(v).push(m); } return by; };
    for (const [label, f] of [["Hazelcast", cfHz], ["Java", cfJava]]) {
      const by = distinct(f);
      if (!by.size) continue;
      const k = kpi(kp, label, by.size === 1 ? Array.from(by.keys())[0] : fmtInt(by.size) + " versions",
        by.size === 1 ? (label === "Hazelcast" && PR_MEM.get(ms[0]).secs[0].get("Build") ? "build " + PR_MEM.get(ms[0]).secs[0].get("Build") + " · " : "") +
          (ms.length > 1 ? "on all " + ms.length + " members" : MEMBERS[ms[0]].label)
          : Array.from(by.entries()).map(([v, mm]) => v + " on " + mm.map(m => mShort(m)).join(", ")).join(" · "));
      if (by.size > 1) { k.classList.add("alert"); k.querySelector(".kpi-value").classList.add("bad"); }
    }
    if (ms.length > 1) {
      const n = cfDifferCount("ConfigProperties"), na = cfDifferCount("args"), ns = cfDifferCount("SystemProperties");
      kpi(kp, "Settings that differ", fmtInt(n + na), "Hazelcast properties " + fmtInt(n) + " · JVM arguments " + fmtInt(na) + " · system properties " + fmtInt(ns));
    }
    let changed = 0;
    for (const m of ms) changed += PR_MEM.get(m).older.size;
    if (changed) kpi(kp, "Changed at a restart", fmtInt(changed), "properties with another value in an earlier lifetime");
  }, { tab: "config" }));
  // the members
  const mcard = el("section", "card", sec), mcap = el("div", "cap", mcard);
  el("h3", null, mcap, "Members");
  const mwrap = el("div", "table-wrap", mcard);
  tabUpdaters.push(Object.assign(() => {
    clear(mwrap);
    const rows = cfMembers().map(m => ({ m, o: PR_MEM.get(m) }));
    sortableTable(mwrap, [
      { key: "m", label: "Member", val: r => MEMBERS[r.m].label, fmt: r => mShort(r.m), title: r => MEMBERS[r.m].label },
      { key: "hz", label: "Hazelcast", val: r => cfHz(r.o) },
      { key: "b", label: "Build", val: r => [r.o.secs[0].get("Build"), r.o.secs[0].get("Revision")].filter(x => x).join(" · ") },
      { key: "j", label: "Java", val: r => cfJava(r.o), cls: "wrapcell" },
      { key: "os", label: "OS", val: r => [r.o.secs[1].get("os.name"), r.o.secs[1].get("os.version"), r.o.secs[1].get("os.arch")].filter(x => x).join(" "), cls: "wrapcell" },
      { key: "heap", label: "Heap", val: r => [r.o.args.get("-Xms") ? "-Xms" + r.o.args.get("-Xms") : "", r.o.args.get("-Xmx") ? "-Xmx" + r.o.args.get("-Xmx") : ""].filter(x => x).join(" ") ||
        (r.o.args.get("-XX:MaxRAMPercentage") ? "MaxRAMPercentage " + r.o.args.get("-XX:MaxRAMPercentage") : "") },
      { key: "gc", label: "GC", val: r => cfGc(r.o) },
      { key: "s", label: "Started", num: true, val: r => r.o.start || 0, fmt: r => r.o.start ? shortTime(r.o.start) + (r.o.lives > 1 ? " (" + r.o.lives + " lifetimes)" : "") : "" }
    ], rows, cfState.msort, () => update(), null, "No BuildInfo, SystemProperties or ConfigProperties section for these members.");
  }, { tab: "config" }));
  // the properties of the section picked
  const card = el("section", "card", sec), cap = el("div", "cap", card);
  const h3 = el("h3", null, cap, "Properties");
  const note = el("span", "note", cap);
  const wrap = el("div", "table-wrap", card);
  const detail = el("div", null, sec);
  tabUpdaters.push(Object.assign(() => {
    clear(wrap); clear(detail);
    h3.textContent = CF_SECS.find(x => x[0] === cfState.sec)[1];
    const all = cfRows(cfState.sec), rows = cfState.diff ? all.filter(r => r.differ || r.older) : all, ms = cfMembers(), wide = ms.length <= 6;
    note.textContent = fmtInt(rows.length) + " of " + fmtInt(all.length) + " · " + fmtInt(all.filter(r => r.differ).length) + " differ between the members · click one for its full values";
    const cols = [{ key: "k", label: "Property", val: r => r.k, cls: "wrapcell", lv: r => r.differ ? "W" : "" }];
    if (wide) ms.forEach((m, j) => cols.push({ key: "v" + j, label: mShort(m), val: r => r.vals[j] == null ? "" : r.vals[j], fmt: r => cfShort(r.vals[j]),
      title: r => (r.vals[j] || "") + (PR_MEM.get(m).older.has(PR.secs.indexOf(cfState.sec) + "\u0001" + r.k) ? "\nbefore a restart: " + Array.from(PR_MEM.get(m).older.get(PR.secs.indexOf(cfState.sec) + "\u0001" + r.k)).join(" | ") : ""),
      cls: "wrapcell", lv: r => r.differ && r.vals[j] !== r.vals[0] ? "W" : "" }));
    else cols.push({ key: "vs", label: "Values", cls: "wrapcell", val: r => { const by = new Map(); r.vals.forEach(v => by.set(v, (by.get(v) || 0) + 1));
      return Array.from(by.entries()).sort((a, b) => b[1] - a[1]).map(([v, n]) => cfShort(v) + (by.size > 1 ? " (" + n + ")" : "")).join(" · "); } });
    if (rows.some(r => r.older)) cols.push({ key: "o", label: "Before a restart", val: r => r.older ? "changed" : "", lv: r => r.older ? "W" : "" });
    sortableTable(wrap, cols, rows, cfState.sort, () => update(), r => { cfState.sel = cfState.sel === r.k ? null : r.k; update(); },
      cfState.diff ? "No property differs between these members." : "No property in this section for these members.", r => cfState.sel === r.k, 2000);
    const r = cfState.sel != null ? rows.find(x => x.k === cfState.sel) : null;
    if (!r) return;
    const c = el("section", "card", detail), cc = el("div", "cap", c);
    el("h3", null, cc, r.k);
    el("span", "note", cc, r.differ ? "differs between the members" : ms.length > 1 ? "the same on every member" : "");
    r.ms.forEach((m, j) => {
      el("div", "ctl-label", c, MEMBERS[m].label);
      const v = r.vals[j];
      const shown = v == null ? "(not set)" : /(?:\.jar|\/classes)[:;]/.test(v) ? v.split(/[:;](?=\/|[A-Za-z]:\\)/).join("\n") : v;
      el("pre", "sample wrappre", c, shown);
      const old = PR_MEM.get(m).older.get(PR.secs.indexOf(cfState.sec) + "\u0001" + r.k);
      if (old) el("p", "hint", c, "Before a restart: " + Array.from(old).join(" | "));
    });
    c.scrollIntoView({ block: "nearest" });
  }, { tab: "config" }));
}

/* ---------- Data structures (maps, queues, topics...) and CP objects: one kind at a time, the way the Maps tab was */
const execCols = p => [[p + ".pending", "Pending"], [p + ".started", "Started"], [p + ".completed", "Completed"], [p + ".cancelled", "Cancelled"]];
const DS_KINDS = [
  { id: "map", label: "Maps", one: "map", pre: "map.", cols: [["map.getCount", "Gets"], ["map.putCount", "Puts"], ["map.setCount", "Sets"],
    ["map.removeCount", "Removes"], ["map.ownedEntryCount", "Entries"], ["map.heapCost", "Heap cost"]] },
  { id: "multiMap", label: "MultiMaps", one: "multimap", pre: "multiMap.", cols: [["multiMap.getCount", "Gets"], ["multiMap.putCount", "Puts"],
    ["multiMap.removeCount", "Removes"], ["multiMap.ownedEntryCount", "Entries"]] },
  { id: "replicatedMap", label: "Replicated maps", one: "replicated map", pre: "replicatedMap.", cols: [["replicatedMap.getCount", "Gets"],
    ["replicatedMap.putCount", "Puts"], ["replicatedMap.removeCount", "Removes"], ["replicatedMap.ownedEntryCount", "Entries"]] },
  { id: "cache", label: "Caches", one: "cache", pre: "cache.", cols: [["cache.cacheGets", "Gets"], ["cache.cachePuts", "Puts"], ["cache.cacheRemovals", "Removals"],
    ["cache.cacheHits", "Hits"], ["cache.cacheMisses", "Misses"], ["cache.cacheEvictions", "Evictions"], ["cache.ownedEntryCount", "Entries"]] },
  { id: "queue", label: "Queues", one: "queue", pre: "queue.", cols: [["queue.numberOfOffers", "Offers"], ["queue.numberOfPolls", "Polls"],
    ["queue.numberOfRejectedOffers", "Rejected offers"], ["queue.ownedItemCount", "Items"], ["queue.maxAge", "Oldest item"]] },
  { id: "topic", label: "Topics", one: "topic", pre: "topic.", cols: [["topic.totalPublishes", "Published"], ["topic.totalReceivedMessages", "Received"]] },
  { id: "reliableTopic", label: "Reliable topics", one: "reliable topic", pre: "reliableTopic.", cols: [["reliableTopic.totalPublishes", "Published"],
    ["reliableTopic.totalReceivedMessages", "Received"]] },
  { id: "list", label: "Lists", one: "list", pre: "list." },
  { id: "set", label: "Sets", one: "set", pre: "set." },
  { id: "ringbuffer", label: "Ringbuffers", one: "ringbuffer", pre: "ringbuffer." },
  { id: "executor", label: "Executors", one: "executor", test: n => n.indexOf("executor.") === 0 && n.split(".").length === 2, cols: execCols("executor") },
  { id: "scheduledExecutor", label: "Scheduled executors", one: "scheduled executor", pre: "scheduledExecutor.", cols: execCols("scheduledExecutor") },
  { id: "durableExecutor", label: "Durable executors", one: "durable executor", pre: "durableExecutor.", cols: execCols("durableExecutor") },
  { id: "flakeIdGenerator", label: "Flake ID generators", one: "flake ID generator", many: "flake ID generators", pre: "flakeIdGenerator.", cols: [["flakeIdGenerator.batchCount", "Batches"],
    ["flakeIdGenerator.idCount", "IDs"]] },
  { id: "pnCounter", label: "PN counters", one: "PN counter", many: "PN counters", pre: "pnCounter.", cols: [["pnCounter.value", "Value"],
    ["pnCounter.totalIncrementOperationCount", "Increments"], ["pnCounter.totalDecrementOperationCount", "Decrements"]] },
  { id: "cardinalityEstimator", label: "Cardinality estimators", one: "cardinality estimator", pre: "cardinalityEstimator." },
  { id: "vectorCollection", label: "Vector collections", one: "vector collection", pre: "vectorCollection." }
];
// CP objects: tagged id=name@group by every CP member of their group (the per-group .summary. counts are not objects)
const CP_KINDS = [
  // Raft groups: tagged groupId and name by every CP member of the group; an item is the whole "groupId · name"
  { id: "raftgroup", label: "Raft groups", one: "Raft group", many: "Raft groups", pre: "raft.group.", tag: "groupId", whole: true,
    cols: [["raft.group.term", "Term"], ["raft.group.commitIndex", "Commits", "total"], ["raft.group.lastLogIndex", "Log entries appended", "total"],
      ["raft.group.availableLogCapacity", "Available log capacity"], ["raft.group.snapshotIndex", "Snapshot index"],
      ["raft.group.leadership.electedCount", "Leader elections", "total"]] },
  { id: "atomiclong", label: "Atomic longs", one: "AtomicLong", many: "AtomicLongs", pre: "cp.atomiclong.", cols: [["cp.atomiclong.value", "Value"],
    ["cp.atomiclong.value", "Change", "delta"], ["cp.atomiclong.value", "Change per second", "rate"]], change: "cp.atomiclong.value" },
  { id: "atomicref", label: "Atomic references", one: "AtomicReference", many: "AtomicReferences", pre: "cp.atomicref." },
  { id: "lock", label: "Fenced locks", one: "FencedLock", many: "FencedLocks", pre: "cp.lock.", cols: [["cp.lock.lockCount", "Lock count"]] },
  { id: "semaphore", label: "Semaphores", one: "semaphore", pre: "cp.semaphore.", cols: [["cp.semaphore.available", "Available permits"]] },
  { id: "countdownlatch", label: "Count-down latches", one: "CountDownLatch", many: "CountDownLatches", pre: "cp.countdownlatch.", cols: [["cp.countdownlatch.count", "Count"],
    ["cp.countdownlatch.remaining", "Remaining"], ["cp.countdownlatch.round", "Round"]] },
  { id: "cpmap", label: "CP maps", one: "CPMap", many: "CPMaps", pre: "cp.map.", cols: [["cp.map.size", "Entries"], ["cp.map.sizeBytes", "Size"]] },
  { id: "session", label: "CP sessions", one: "CP session", many: "CP sessions", pre: "cp.session.", cols: [["cp.session.version", "Version"]] }
];
const kindHas = (k, r, cat) => r.c === cat && (k.test ? k.test(r.n) : r.n.indexOf(k.pre) === 0) &&
  (cat !== "cp" || ((r.tags || []).indexOf(k.tag || "id") >= 0 && r.n.indexOf(".summary.") < 0));
const itemOf = label => { const i = label.indexOf(" · "); return i < 0 ? label : label.slice(0, i); };
const SVIEWS = {};
// the kinds that have metrics here, each with its records and items (names), most items first after maps
// a change per second in the unit that reads best: per second, minute or hour
function fmtPerTime(x) {
  const a = Math.abs(x);
  if (a === 0) return "0";
  if (a >= 1) return (x > 0 ? "+" : "") + fmtNum(x) + "/s";
  if (a * 60 >= 1) return (x > 0 ? "+" : "") + sig3(x * 60) + "/min";
  return (x > 0 ? "+" : "") + sig3(x * 3600) + "/h";
}
// a gauge's change per second between neighbouring points (a counter kept as a value: AtomicLong)
function changeSeries(s) {
  const v = new Float64Array(s.v.length).fill(NaN);
  let prev = -1;
  for (let i = 0; i < s.v.length; i++) {
    const x = s.v[i];
    if (x !== x) continue;
    if (prev >= 0 && i - prev <= 3) v[i] = (x - s.v[prev]) / ((i - prev) * s.step / 1000);
    prev = i;
  }
  return Object.assign({}, s, { v });
}
function structKinds(kinds, cat) {
  const out = [];
  for (const k of kinds) {
    const recs = D.metrics.filter(r => kindHas(k, r, cat)).sort((a, b) => a.n < b.n ? -1 : 1);
    if (!recs.length) continue;
    const names = new Set(), hidden = new Set();
    for (const r of recs) { for (const e of (r.e || [])) names.add(k.whole ? e[0] : itemOf(e[0])); for (const h of (r.hidden || [])) hidden.add(h); }
    const left = Array.from(hidden).filter(h => !names.has(h)).sort();
    out.push(Object.assign({}, k, { recs, names: Array.from(names).sort(), left, cols: (k.cols || []).filter(c => MREC.has(c[0])) }));
  }
  return out;
}
function structCount(kinds, cat) { return structKinds(kinds, cat).reduce((s, k) => s + k.names.length, 0); }
function structView(tab, sec, kinds, cat, o) {
  o = o || {};
  const K = structKinds(kinds, cat);
  if (!K.length) return null;
  const v = SVIEWS[tab] = { tab, K, kind: K[0].id, metric: {}, sel: null, sort: { key: null, dir: -1 } };
  const kindOf = () => K.find(k => k.id === v.kind) || K[0];
  const metricOf = k => { if (!v.metric[k.id] || !MREC.has(v.metric[k.id])) v.metric[k.id] = (k.cols.find(c => MREC.has(c[0])) || [k.recs[0].n])[0]; return MREC.get(v.metric[k.id]); };
  const ctl = ctxRow(tab);
  const nameOk = o.nameOk || (() => true);
  let ksel = null, kopts = null;
  if (K.length > 1) {
    const kl = el("label", "ctl", ctl);
    el("span", "ctl-label", kl, o.kindText || "Kind");
    ksel = el("select", "kindsel", kl);
    ksel.setAttribute("aria-label", o.kindLabel || "Kind of data structure");
    kopts = K.map(k => { const op = el("option", null, ksel); op.value = k.id; return [k, op]; });
    ksel.addEventListener("change", () => { if (v.kind !== ksel.value) { v.kind = ksel.value; v.sel = null; v.sort = { key: null, dir: -1 }; } update(); });
  }
  const lab = el("label", "ctl", ctl);
  el("span", "ctl-label", lab, "Metric");
  const msel = el("select", "opsel", lab);
  msel.addEventListener("change", () => { v.metric[kindOf().id] = msel.value; update(); });
  let optsFor = null;
  syncers.push(() => {
    const k = kindOf();
    if (ksel) {
      for (const [kk, op] of kopts) { const n = kk.names.filter(nameOk).length; op.textContent = kk.label + " (" + fmtInt(n) + ")"; }
      ksel.value = k.id;
    }
    if (optsFor !== k.id) {
      optsFor = k.id;
      clear(msel);
      for (const r of k.recs) { const op = el("option", null, msel, prettyName(r.n) + "  (" + r.n + ")"); op.value = r.n; }
    }
    msel.value = metricOf(k).n;
  });
  const lead = el("p", "lead", sec);
  const selbar = el("div", "selbar", sec); selbar.hidden = true;
  const plural = (k, n) => n === 1 ? k.one : (k.many || k.label.toLowerCase());
  const main = addPanel(tab, sec, { title: "Metric", height: 240, render: p => {
    const k = kindOf(), r = metricOf(k);
    p.h3.textContent = prettyName(r.n) + (v.sel ? " · " + v.sel : "");
    const spec = metricSpec(r, state.group === "name" ? { byItem: true, hl: v.sel } : { only: v.sel });
    const hid = r.hidden || [];
    const topNote = r.ne > (r.e || []).length ? "Lines for the " + (r.e || []).length + " most active of " + fmtInt(r.ne) + " " + plural(k, 2) + " (raise --top to keep more). " : "";
    spec.foot = (hid.length || topNote) ? box => {
      if (topNote) box.appendChild(document.createTextNode(topNote));
      if (hid.length) {
        box.appendChild(document.createTextNode(fmtInt(r.nh) + " " + plural(k, r.nh) + " left out of this metric because the value never changed on any member: "));
        el("span", "list-inline", box, hid.slice(0, 60).join(", ") + (hid.length > 60 ? ", …" : ""));
      }
    } : null;
    drawTS(p, spec);
  } });
  main.metricLink = true;
  const card = el("section", "card", sec);
  const cap = el("div", "cap", card);
  const h3 = el("h3", null, cap);
  const tnote = el("span", "note", cap);
  const wrap = el("div", "table-wrap", card);
  const detail = el("div", null, sec);
  let detailFor = null;
  // in the time range in view: the average of a rate, the highest of a size; "delta": last minus first value, "rate": that per second
  const statOf = (r, name, sel, mode) => {
    const e = eSeries(r).find(x => x.label === name);
    if (!e) return null;
    const rows = e.rows.filter(s => sel.has(s.m));
    if (!rows.length) return null;
    const s = combine(rows, r.am || r.a);
    const [a, b] = gridRange(s, state.x0, state.x1);
    if (mode === "total") {
      let t = 0;
      for (let i = a; i <= b; i++) { const x = s.v[i]; if (x === x) t += x * s.step / 1000; }
      return t;
    }
    if (mode) {
      let i0 = -1, i1 = -1;
      for (let i = a; i <= b; i++) if (s.v[i] === s.v[i]) { if (i0 < 0) i0 = i; i1 = i; }
      if (i0 < 0) return null;
      const d = s.v[i1] - s.v[i0];
      return mode === "delta" ? d : i1 > i0 ? d / ((i1 - i0) * s.step / 1000) : null;
    }
    let sum = 0, n = 0, mx = -Infinity;
    for (let i = a; i <= b; i++) { const x = s.v[i]; if (x === x) { sum += x; n++; if (x > mx) mx = x; } }
    if (!n) return null;
    return isRate(r) ? sum / n : mx;
  };
  const render = () => {
    const k = kindOf(), cur = metricOf(k);
    clear(wrap);
    if (v.sel && !nameOk(v.sel)) v.sel = null;
    const shownN = k.names.filter(nameOk).length;
    lead.textContent = fmtInt(shownN) + " " + plural(k, shownN) + (o.nameNote ? o.nameNote() : "") + " with values that changed" +
      (k.left.length ? "; " + fmtInt(k.left.length) + " left out because nothing about them ever changed (cleaning rule): " + k.left.slice(0, 20).join(", ") + (k.left.length > 20 ? ", …" : "") : "") + "." + (o.leadMore ? " " + o.leadMore : "");
    h3.textContent = k.label;
    const ss = selSet(), q = findQ();
    const cols = k.cols.slice();
    if (!cols.some(c => c[0] === cur.n)) cols.push([cur.n, prettyName(cur.n)]);
    const rows = [];
    for (const name of k.names) {
      if ((q && name.toLowerCase().indexOf(q) < 0) || !nameOk(name)) continue;
      const vals = cols.map(c => { const r = MREC.get(c[0]); return r ? statOf(r, name, ss, c[2]) : null; });
      rows.push({ name, vals });
    }
    const si = v.sort.key == null ? cols.findIndex(c => c[0] === cur.n) : v.sort.key;
    rows.sort((a, b) => si < 0 || si >= cols.length ? (a.name < b.name ? -1 : 1) * v.sort.dir * -1 : ((a.vals[si] == null ? -Infinity : a.vals[si]) - (b.vals[si] == null ? -Infinity : b.vals[si])) * v.sort.dir || (a.name < b.name ? -1 : 1));
    tnote.textContent = fmtInt(rows.length) + " " + plural(k, rows.length) + " · in the time range in view: " +
      (cols.some(c => c[2] === "total") ? "counts in total, " : "") + "rates as averages per second, sizes the highest" +
      (cols.some(c => c[2] === "delta") ? ", change: last value minus first" : "") + " · click one for its charts";
    const t = el("table", null, wrap), hr = el("tr", null, el("thead", null, t));
    const head = (label, idx, num) => {
      const th = el("th", num ? "num" : null, hr), b = el("button", null, th, label + (si === idx ? (v.sort.dir < 0 ? " ↓" : " ↑") : ""));
      b.type = "button";
      if (si === idx) th.setAttribute("aria-sort", v.sort.dir < 0 ? "descending" : "ascending");
      b.addEventListener("click", () => { v.sort = { key: idx, dir: si === idx ? -v.sort.dir : (num ? -1 : 1) }; render(); });
    };
    head(k.one.charAt(0).toUpperCase() + k.one.slice(1), -1, false);
    cols.forEach((c, i) => { const r = MREC.get(c[0]); head(c[1] + (isRate(r) && !c[2] ? "/s" : ""), i, true); });
    const tb = el("tbody", null, t);
    for (const row of rows.slice(0, 1000)) {
      const tr = el("tr", "click" + (v.sel === row.name ? " sel" : ""), tb);
      tr.tabIndex = 0;
      el("td", "op", tr, row.name);
      row.vals.forEach((x, i) => {
        const r = MREC.get(cols[i][0]), mode = cols[i][2];
        el("td", "num", tr, x == null ? "" : mode === "delta" ? (x > 0 ? "+" : "") + fmtVal(x, r.u) : mode === "rate" ? fmtPerTime(x) : mode === "total" ? fmtInt(x) : fmtVal(x, r.u, isRate(r)));
      });
      const pick = () => { v.sel = v.sel === row.name ? null : row.name; update(); };
      tr.addEventListener("click", pick);
      tr.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    }
    if (!rows.length) el("div", "empty-note", wrap, "No " + k.one + " matches.");
    // selection bar and the selected item's charts
    clear(selbar);
    selbar.hidden = !v.sel;
    if (v.sel) {
      selbar.appendChild(document.createTextNode("Selected " + k.one + " "));
      el("b", null, selbar, v.sel);
      const b = el("button", "btn btn-s", selbar, "Clear"); b.type = "button";
      b.addEventListener("click", () => { v.sel = null; update(); });
    }
    const want = v.sel ? k.id + "\u0001" + v.sel : null;
    if (detailFor !== want) {
      detailFor = want;
      dropPanels(p => p.mapDetail && p.tab === tab);
      clear(detail);
      if (want) {
        const name = v.sel;
        el("h2", null, detail, "Every metric of " + k.one + " " + name);
        el("p", "lead", detail, "Lines by member; use the Member filter to pick one.");
        if (k.change && MREC.has(k.change)) {
          const r = MREC.get(k.change);
          const p = addPanel(tab, detail, { title: "How fast the value changes", code: k.change, height: 170, render: pp => {
            const e = eSeries(r).find(x => x.label === name), ss = selSet(), series = [];
            if (e) for (const s of e.rows) if (ss.has(s.m)) series.push(Object.assign({ key: "m:" + s.m, label: MEMBERS[s.m].label, color: memberColor(s.m), faint: s.m >= 8 }, changeSeries(s)));
            drawTS(pp, { series, unit: r.u, rate: true, note: "per second, average per {step} · from the value at each dump; every CP member reports the same value", noDirect: series.length > 1 });
          } });
          p.mapDetail = true;
        }
        for (const r of k.recs) {
          if (!(r.e || []).some(e => e[0] === name || e[0].indexOf(name + " · ") === 0)) continue;
          const p = addPanel(tab, detail, { title: prettyName(r.n), code: r.n, height: 170, render: pp => {
            const ents = (r.e || []).filter(e => e[0] === name || e[0].indexOf(name + " · ") === 0);
            const spec = ents.length > 1 || (ents.length === 1 && ents[0][0] !== name) ? metricSpec(r, { byItem: true, only: name, noFind: true }) : metricSpec(r, { byMember: true, only: name, noFind: true });
            drawTS(pp, spec);
          } });
          p.mapDetail = true;
        }
      }
    }
  };
  tabUpdaters.push(Object.assign(render, { tab }));
  return v;
}
function buildMaps(sec) {
  const v = structView("maps", sec, DS_KINDS, "maps");
  if (HASSTORE) {                       // how long the maps' MapStore calls took: the next tab
    const b = el("button", "btn btn-s", ctxRow("maps"), "MapStore latency →"); b.type = "button";
    b.title = "How long the maps' MapStore and MapLoader calls took";
    b.addEventListener("click", () => setTab("mapstore"));
  }
  if (!v) el("p", "lead", sec, "No data structure metrics in these files" + (D.flat.some(f => f.c === "maps") || D.static.some(f => f.c === "maps") ? " that changed." : "."));
  unchangedSection("maps", sec);
}

/* ---------- MapStore: how long the maps' MapStore / MapLoader methods took (MapStoreLatency sections; the queue,
   ringbuffer and JCache stores the same way when the diagnostics have them) */
const SSTEP = META.sstep || 60000;
const ST_TOT = ST.tot.map(x => {
  const op = ST.ops[x[1]], k = storeParts(op);
  return { m: x[0], op, svc: k.svc, meth: k.meth, dsKey: k.svc + "\u0001" + k.ds, name: storeName(k.svc, k.ds), n: x[2], t: x[3], mx: x[4], b: x[5] };
});
const ST_ALLMAP = ST_TOT.every(r => r.svc === "MapStore") && SROWS.every(r => r.op.indexOf("MapStore\u0001") === 0);
const stState = { ds: "", meth: "", sort: { key: "t", dir: -1 } };
const storeOk = op => { const k = storeParts(op); return (!stState.ds || stState.ds === k.svc + "\u0001" + k.ds) && (!stState.meth || stState.meth === k.meth); };
const fmtLongUs = us => us >= 6e7 ? dur(us / 1e6) : fmtUs(us);
// calls at or above 2^t us: the buckets whose exponent is above t (bucket e holds 2^(e-1) .. 2^e - 1 us)
const slowOf = (b, t) => { let s = 0; for (const e in b) if (+e > t) s += b[e]; return s; };
const bucketLabel = e => e <= 1 ? "0–1 µs" : fmtUs(Math.pow(2, e - 1)) + "–" + fmtUs(Math.pow(2, e));
// the totals since the members started, for the members, Find and lists in use: by member or by name
function storeTotRows() {
  const ss = selSet(), q = findQ(), by = new Map(), byName = state.group === "name";
  for (const r of ST_TOT) {
    if (!ss.has(r.m) || !storeOk(r.op)) continue;
    if (q && r.name.toLowerCase().indexOf(q) < 0 && r.meth.toLowerCase().indexOf(q) < 0 && MEMBERS[r.m].label.toLowerCase().indexOf(q) < 0) continue;
    const key = byName ? r.op : r.m + "|" + r.op;
    let v = by.get(key);
    if (!v) { v = { key, op: r.op, name: r.name, meth: r.meth, dsKey: r.dsKey, m: byName ? null : r.m, ms: new Set(), n: 0, t: 0, mx: 0, b: {} }; by.set(key, v); }
    v.ms.add(r.m); v.n += r.n; v.t += r.t; v.mx = Math.max(v.mx, r.mx);
    for (const [e, c] of r.b) v.b[e] = (v.b[e] || 0) + c;
  }
  return Array.from(by.values());
}
// calls by duration: one bar per latency bucket, its height on a log scale, in the colour of its latency group
function histCell(td, b, eMax) {
  const L = latGroups(), W = 4, G = 1, H = 16;
  const svg = svgEl("svg", { width: eMax * (W + G), height: H + 4, class: "hist", role: "img" }, td);
  let mx = 0;
  for (const e in b) mx = Math.max(mx, b[e]);
  const lines = [];
  for (const e of [11, 21]) if (e <= eMax) svgEl("rect", { x: (e - 1) * (W + G) - 1, y: H + 1, width: 1, height: 3, class: "hist-tick" }, svg);
  for (let e = 1; e <= eMax; e++) {
    const c = b[e] || 0;
    if (!c) continue;
    const h = Math.max(2, H * Math.log10(c + 1) / Math.log10(mx + 1));
    svgEl("rect", { x: (e - 1) * (W + G), y: (H - h).toFixed(1), width: W, height: h.toFixed(1), rx: 1, fill: latColor(L.gof[Math.min(63, e)]) }, svg);
    lines.push(bucketLabel(e) + ": " + fmtInt(c));
  }
  svg.setAttribute("aria-label", "Calls by duration");
  svgEl("title", null, svg).textContent = "Calls by duration (bar height on a log scale; ticks at 1 ms and 1 s)\n" + lines.join("\n");
}
function buildMapStore(sec) {
  const what = ST_ALLMAP ? "MapStore" : "store";
  const idle = ST.idle.map(x => storeName(x[0], x[1]));
  const once = ST.dumps.every(d => d[1] <= 1);
  el("p", "lead", sec, "From the " + (ST_ALLMAP ? "MapStoreLatency" : "store latency") + " sections (hazelcast.diagnostics.storeLatency.period.seconds" +
    (once ? "" : ", every " + stepName(SSTEP) + " here") + "): how long each map's MapStore and MapLoader methods took, timed around your implementation, so mostly the database. load and loadAll " +
    "(a get or getAll that misses) and, with write-through, store and delete run inside the operation, on its partition thread: a slow database there " +
    "holds up every other operation of that partition. loadAllKeys and loadAll during the initial load, and the batches of write-behind " +
    "(storeAll, deleteAll), run in the background: when they are slow, the entries waiting to be written (Dirty entries, on the Data structures tab) grow." + (idle.length ? " " + fmtInt(idle.length) + " map" + (idle.length === 1 ? " has a store that was" : "s have a store that was") +
    " never called: " + idle.slice(0, 12).join(", ") + (idle.length > 12 ? ", …" : "") + "." : ""));
  // the lists: a map (or other data structure) and a method
  const row = ctxRow("mapstore");
  const ml = el("label", "ctl", row);
  el("span", "ctl-label", ml, ST_ALLMAP ? "Map" : "Data structure");
  const msel = el("select", "opsel stmap", ml);
  const tl = el("label", "ctl", row);
  el("span", "ctl-label", tl, "Method");
  const tsel = el("select", "stmeth", tl);
  const names = new Map();
  for (const r of ST_TOT) names.set(r.dsKey, r.name);
  for (const r of SROWS) { const k = storeParts(r.op); names.set(k.svc + "\u0001" + k.ds, storeName(k.svc, k.ds)); }
  const dsList = Array.from(names.entries()).sort((a, b) => a[1].toLowerCase() < b[1].toLowerCase() ? -1 : 1);
  el("option", null, msel, "All " + fmtInt(dsList.length) + " " + (ST_ALLMAP ? "maps" : "data structures")).value = "";
  for (const [k, n] of dsList) { const o = el("option", null, msel, n); o.value = k; }
  msel.addEventListener("change", () => { stState.ds = msel.value; stState.meth = ""; update(); });
  tsel.addEventListener("change", () => { stState.meth = tsel.value; update(); });
  let methFor = null;
  syncers.push(() => {
    msel.value = stState.ds;
    if (methFor !== stState.ds) {
      methFor = stState.ds;
      const ms = new Set();
      for (const r of ST_TOT) if (!stState.ds || r.dsKey === stState.ds) ms.add(r.meth);
      for (const r of SROWS) { const k = storeParts(r.op); if (!stState.ds || k.svc + "\u0001" + k.ds === stState.ds) ms.add(k.meth); }
      clear(tsel);
      el("option", null, tsel, "All methods").value = "";
      for (const m of Array.from(ms).sort()) { const o = el("option", null, tsel, m); o.value = m; }
    }
    tsel.value = stState.meth;
  });
  const bar = el("div", "selbar", sec); bar.hidden = true;
  tabUpdaters.push(Object.assign(() => {
    clear(bar);
    bar.hidden = !stState.ds && !stState.meth;
    if (bar.hidden) return;
    bar.appendChild(document.createTextNode("Showing "));
    el("b", null, bar, (stState.ds ? names.get(stState.ds) : "every " + (ST_ALLMAP ? "map" : "data structure")) + (stState.meth ? " · " + stState.meth : ""));
    const b = el("button", "btn btn-s", bar, "Show all"); b.type = "button";
    b.addEventListener("click", () => { stState.ds = ""; stState.meth = ""; update(); });
  }, { tab: "mapstore" }));
  const blk = { rows: SROWS, pred: storeOk, step: SSTEP, title: "In the time range in view", what: what + " call", unit: "method",
    opLabel: ST_ALLMAP ? "Map · method" : "Data structure · method", noOps: true };
  const kp = el("div", "kpis", sec);
  tabUpdaters.push(Object.assign(() => {
    clear(kp);
    const L = latGroups();
    let n = 0, t = 0, slow = 0, mx = 0, w = null, since = "";
    if (SROWS.length) {
      for (const r of profRows(true, blk)) {
        const o = rowStats(r, state.x0, state.x1);
        n += o.n; t += o.t; slow += o.slow;
        if (o.mx > mx) { mx = o.mx; w = r.short + " · " + mShort(r.m); }
      }
      since = "in the time range in view";
    } else {
      for (const v of storeTotRows()) { n += v.n; t += v.t; slow += slowOf(v.b, state.lat.t); if (v.mx > mx) { mx = v.mx; w = v.name + " · " + v.meth + (v.m != null ? " · " + mShort(v.m) : ""); } }
      since = "since the members started";
    }
    kpi(kp, (ST_ALLMAP ? "MapStore" : "Store") + " calls", fmtInt(n), since);
    kpi(kp, "Time in these calls", fmtLongUs(t), n ? "average " + fmtUs(t / n) : "");
    if (w) kpi(kp, "Slowest call", fmtUs(mx), w);
    const k = kpi(kp, "Calls " + L.slowLabel, fmtInt(slow), n ? fmtPct(100 * slow / n) + " of the calls · slow threshold set with the latency groups" : "");
    if (slow) k.querySelector(".kpi-value").classList.add("bad");
    const nds = new Set(ST_TOT.map(r => r.dsKey)).size;
    kpi(kp, ST_ALLMAP ? "Maps with a store" : "Data structures with a store", fmtInt(nds + idle.length), idle.length ? fmtInt(idle.length) + " never called" : "every one called");
  }, { tab: "mapstore" }));
  if (SROWS.length) latencyBlock("mapstore", sec, blk);
  else el("p", "lead", sec, "One dump per member here: no change over time to show, so the table below has the totals since each member started. A longer " +
    "capture (more than one period of the store latency plugin) also gives the charts: calls per latency group, average and max, and when the " + what + " calls were slow.");
  // totals since the members started (the counters of each lifetime's last dump, and what was counted before a reset)
  const card = el("section", "card", sec), cap = el("div", "cap", card);
  el("h3", null, cap, "Since the members started");
  const note = el("span", "note", cap);
  const wrap = el("div", "table-wrap tall", card);
  tabUpdaters.push(Object.assign(() => {
    clear(wrap);
    const L = latGroups(), rows = storeTotRows(), byName = state.group === "name";
    const all = rows.reduce((a, v) => a + v.t, 0);
    let eMax = 12;
    for (const v of rows) for (const e in v.b) if (+e > eMax) eMax = +e;
    note.textContent = fmtInt(rows.length) + " row" + (rows.length === 1 ? "" : "s") + (byName ? " · one per " + (ST_ALLMAP ? "map" : "data structure") + " and method" : " · one per member, " +
      (ST_ALLMAP ? "map" : "data structure") + " and method") + " · as the members counted them, whatever the time range in view · click one to chart only it";
    const cols = [
      { key: "name", label: blk.opLabel, val: v => v.name.toLowerCase() + " " + v.meth, cls: "op", cellFn: v => td => { td.textContent = v.name; el("span", "meth", td, " · " + v.meth); td.title = opFull(v.op); } }];
    if (byName) cols.push({ key: "ms", label: "Members", num: true, val: v => v.ms.size });
    else cols.push({ key: "m", label: "Member", val: v => MEMBERS[v.m].label, fmt: v => mShort(v.m), title: v => MEMBERS[v.m].label });
    cols.push(
      { key: "n", label: "Calls", num: true, val: v => v.n, fmt: v => fmtInt(v.n) },
      { key: "t", label: "Total time", num: true, val: v => v.t, fmt: v => fmtLongUs(v.t), cellFn: v => td => { if (all > 0) el("span", "share", td, fmtPct(100 * v.t / all)); } },
      { key: "avg", label: "Avg", num: true, val: v => v.t / v.n, fmt: v => fmtUs(v.t / v.n) },
      { key: "mx", label: "Max", num: true, val: v => v.mx, fmt: v => fmtUs(v.mx) },
      { key: "slow", label: L.slowLabel, num: true, val: v => slowOf(v.b, state.lat.t), fmt: v => { const s = slowOf(v.b, state.lat.t); return s ? fmtInt(s) : ""; } },
      { key: "pct", label: "Slow %", num: true, val: v => slowOf(v.b, state.lat.t) / v.n, fmt: v => { const s = slowOf(v.b, state.lat.t); return s ? fmtPct(100 * s / v.n) : ""; } },
      { key: "hist", label: "Calls by duration", val: v => v.mx, fmt: () => "", cellFn: v => td => histCell(td, v.b, eMax) });
    sortableTable(wrap, cols, rows, stState.sort, () => update(), v => {
      const same = stState.ds === v.dsKey && stState.meth === v.meth;
      stState.ds = same ? "" : v.dsKey; stState.meth = same ? "" : v.meth;
      update();
    }, "No " + what + " call in this selection.", v => stState.ds === v.dsKey && stState.meth === v.meth, 1000);
  }, { tab: "mapstore" }));
}

/* ---------- Cluster */
function buildCluster(sec) {
  const used = new Set();
  clusterPanel("cluster", sec);
  // member list changes
  if (D.cluster.length) {
    const card = el("section", "card", sec), cap = el("div", "cap", card);
    el("h3", null, cap, "Member list changes");
    el("span", "note", cap, "from each member's HazelcastInstance section");
    const wrap = el("div", "table-wrap", card);
    const render = () => {
      clear(wrap);
      const ss = selSet(), rows = [];
      const per = new Map();
      for (const r of D.cluster) { if (!ss.has(r[0])) continue; if (!per.has(r[0])) per.set(r[0], []); per.get(r[0]).push(r); }
      for (const [m, runs] of per) {
        runs.sort((a, b) => a[1] - b[1]);
        let prev = null;
        for (const r of runs) {
          if (r[2] < state.x0 || r[1] > state.x1) { prev = r; continue; }
          if (!prev || prev[3] !== r[3] || prev[5] !== r[5] || prev[4] !== r[4]) rows.push({ t: r[1], m, size: r[3], master: r[4], list: D.lists[r[5]], first: !prev });
          prev = r;
        }
      }
      rows.sort((a, b) => a.t - b.t || a.m - b.m);
      const t = el("table", null, wrap), hr = el("tr", null, el("thead", null, t));
      ["From", "Seen by", "Size", "Master", "Members"].forEach((h, i) => el("th", i === 2 ? "num" : null, hr, h));
      const tb = el("tbody", null, t);
      for (const r of rows.slice(0, 300)) {
        const tr = el("tr", null, tb);
        el("td", "num", tr, shortTime(r.t) + (r.first ? " (first)" : ""));
        el("td", null, tr, MEMBERS[r.m].label); el("td", "num", tr, String(r.size)); el("td", null, tr, r.master ? "this member" : "");
        el("td", "wide", tr, r.list);
      }
      if (!rows.length) el("div", "empty-note", wrap, "No member list in this range.");
    };
    tabUpdaters.push(Object.assign(render, { tab: "cluster" }));
  }
  // heartbeats
  if (D.hb.length) {
    addPanel("cluster", sec, { title: "Late heartbeats", code: "OperationHeartbeat, MemberHeartbeats", height: 200, render: p => {
      const ss = selSet(), per = new Map();
      for (const h of D.hb) {
        if (!ss.has(h[1]) || h[4] == null) continue;
        const k = h[1] + "|" + h[2] + "|" + h[3];
        if (!per.has(k)) per.set(k, { m: h[1], kind: h[2], peer: h[3], pts: [] });
        per.get(k).pts.push([h[0], h[4] / 1000, h[5] != null ? "deviation " + sig3(h[5]) + "%" : ""]);
      }
      const series = Array.from(per.values()).sort((a, b) => a.m - b.m || (a.peer < b.peer ? -1 : 1)).map((x, i) => ({
        key: "h:" + x.m + x.kind + x.peer, label: MEMBERS[x.m].label + " → " + x.peer + " (" + (x.kind === "O" ? "operation" : "member") + ")",
        color: catColor(i), faint: i >= 8, pts: x.pts, join: 60000, dots: true }));
      drawTS(p, { series, unit: "s", note: "seconds without a heartbeat, when it was late (the plugins log only those)", fmt: v => sig3(v) + " s", noDirect: true });
    } });
  }
  // events
  const card = el("section", "card", sec), cap = el("div", "cap", card);
  el("h3", null, cap, "Events");
  const enote = el("span", "note", cap);
  const KG = [["all", "All"], ["member", "Membership"], ["life", "Lifecycle"], ["conn", "Connections"], ["mig", "Migrations"], ["other", "Other"]];
  const groupOf = k => k === "MemberAdded" || k === "MemberRemoved" ? "member" : k === "Lifecycle" ? "life" : k.indexOf("Connection") === 0 ? "conn" : k.indexOf("Migration") === 0 ? "mig" : "other";
  const fl = el("div", "tabctl", card);
  const chips = el("div", "seg", fl);
  const kbtns = KG.map(k => { const b = el("button", null, chips, k[1]); b.type = "button"; b.addEventListener("click", () => { state.evKind = k[0]; render(); }); return [k[0], b]; });
  const wrap = el("div", "table-wrap", card);
  const render = () => {
    kbtns.forEach(x => x[1].setAttribute("aria-pressed", String(state.evKind === x[0])));
    clear(wrap);
    const ss = selSet(), q = findQ();
    const rows = D.events.filter(e => ss.has(e[1]) && e[0] >= state.x0 && e[0] <= state.x1 && (state.evKind === "all" || groupOf(e[2]) === state.evKind) &&
      (!q || e[3].toLowerCase().indexOf(q) >= 0 || e[2].toLowerCase().indexOf(q) >= 0));
    const dropped = Object.keys(META.dropped || {}).length ? " · some kinds were capped at " + fmtInt(2000) + " per member" : "";
    enote.textContent = fmtInt(rows.length) + " event" + (rows.length === 1 ? "" : "s") + " in this range" + dropped;
    if (!rows.length) { el("div", "empty-note", wrap, "No events in this selection."); return; }
    const t = el("table", null, wrap), hr = el("tr", null, el("thead", null, t));
    ["Time", "Seen by", "Event", "Details"].forEach(h => el("th", null, hr, h));
    const tb = el("tbody", null, t);
    for (const e of rows.slice(0, 500)) {
      const tr = el("tr", null, tb);
      el("td", "num", tr, shortTime(e[0])); el("td", null, tr, MEMBERS[e[1]].label); el("td", null, tr, e[2]); el("td", "wide", tr, e[3]);
    }
    if (rows.length > 500) el("div", "hint", wrap, "First 500 of " + fmtInt(rows.length) + "; zoom in or filter to see the rest.");
  };
  tabUpdaters.push(Object.assign(render, { tab: "cluster" }));
  headline("cluster", sec, [["partitions.localPartitionCount", "Partitions owned"], ["partitions.activePartitionCount", "Active partitions"],
    ["partitions.migrationQueueSize", "Migration queue"], ["client.endpoint.count", "Client endpoints"]], used);
  moreSection("cluster", sec, used, "Other cluster metrics");
}

/* ---------- WAN and CP Subsystem */
// growth of a counter in the time range in view (from its rate per bin), over the selected members
function counterTotal(name, perItemMax) {
  const r = MREC.get(name);
  if (!r) return D.static.some(x => x.n === name) || D.flat.some(x => x.n === name) ? 0 : null;
  if (!isRate(r)) return null;
  const sel = selSet();
  const add = s => { let t = 0; const [a, b] = gridRange(s, state.x0, state.x1); for (let i = a; i <= b; i++) { const x = s.v[i]; if (x === x) t += x * s.step / 1000; } return t; };
  let tot = 0;
  const ef = entFilter(r);
  if (perItemMax && r.e && r.e.length) {
    // every CP member reports the same Raft group: per group the highest across members, then summed
    for (const e of eSeries(r)) { if (ef && !ef(e.label)) continue; const rows = e.rows.filter(x => sel.has(x.m)); if (rows.length) tot += add(combine(rows, "max")); }
    return tot;
  }
  if (ef) {
    for (const [, rows] of entRowsByMember(r, ef)) for (const s of rows) tot += add(s);
    return tot;
  }
  for (const s of mSeries(r)) if (sel.has(s.m)) tot += add(s);
  return tot;
}
// highest value of a gauge in the time range in view: [value, member], or the unchanged value
function gaugePeak(name) {
  const r = MREC.get(name), sel = selSet();
  if (!r) {
    const st = D.static.find(x => x.n === name);
    if (!st) return null;
    const v = st.v.filter(x => sel.has(x[0])).sort((a, b) => b[1] - a[1])[0];
    return v ? [v[1], v[0]] : null;
  }
  let best = null;
  const ef = entFilter(r);
  const list = ef ? Array.from(entRowsByMember(r, ef)).map(([m, rows]) => Object.assign({}, combine(rows, r.a), { m })) : mSeries(r);
  for (const s of list) { if (!sel.has(s.m)) continue; const e = seriesExtent(s, state.x0, state.x1); if (e.hi > -Infinity && (!best || e.hi > best[0])) best = [e.hi, s.m]; }
  return best;
}
// per member: one counter's rate over another's, e.g. publish time per event published
function ratioSeries(numName, denName) {
  const num = MREC.get(numName), den = MREC.get(denName), sel = selSet(), out = [];
  if (!num || !den) return out;
  const perMember = r => { const ef = entFilter(r); return ef ? Array.from(entRowsByMember(r, ef)).map(([m, rows]) => Object.assign({}, combine(rows, r.a), { m })) : mSeries(r); };
  const dens = perMember(den);
  for (const a of perMember(num)) {
    if (!sel.has(a.m)) continue;
    const b = dens.find(x => x.m === a.m);
    if (!b) continue;
    const v = new Float64Array(a.v.length).fill(NaN);
    for (let i = 0; i < a.v.length; i++) {
      const j = a.o - b.o + i;
      if (j >= 0 && j < b.v.length && b.v[j] > 0 && a.v[i] === a.v[i]) v[i] = a.v[i] / b.v[j];
    }
    out.push({ key: "m:" + a.m, label: MEMBERS[a.m].label, color: memberColor(a.m), faint: a.m >= 8, t0: a.t0, step: a.step, v, o: a.o, f: 1, agg: "max" });
  }
  return out;
}
/* ---------- WAN: one publisher at a time (WAN metrics are tagged replication= and publisherId=) */
let WANPUBS = null;
function wanPubs() {          // [[key, replication, publisherId]]
  if (WANPUBS) return WANPUBS;
  const seen = new Map();
  for (const r of D.metrics) {
    if (r.c !== "wan" || !r.e) continue;
    const tags = r.tags || [], ip = tags.indexOf("publisherId"), ir = tags.indexOf("replication");
    if (ip < 0) continue;
    for (const e of r.e) {
      const parts = e[0].split(" · "), p = parts[ip], rep = ir >= 0 ? parts[ir] : "";
      if (p == null) continue;
      const k = rep + "\u0001" + p;
      if (!seen.has(k)) seen.set(k, [k, rep, p]);
    }
  }
  WANPUBS = Array.from(seen.values()).sort((a, b) => a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : a[1] < b[1] ? -1 : 1);
  return WANPUBS;
}
const wanPubName = k => { const x = k.split("\u0001"); return x[1] + (x[0] ? " (" + x[0] + ")" : ""); };
// the items of a metric to keep: those of the WAN publisher picked, for a WAN metric that has a publisherId;
// those of the CP group picked, for a CP metric tagged with the group (Raft group name, name@group id, group)
function entFilter(r) {
  if (state.cpGroup && r.c === "cp") return cpFilter(r, state.cpGroup);
  if (!state.wanPub || r.c !== "wan") return null;
  const tags = r.tags || [], ip = tags.indexOf("publisherId"), ir = tags.indexOf("replication");
  if (ip < 0) return null;
  const x = state.wanPub.split("\u0001"), rep = x[0], pub = x[1];
  return label => { const parts = label.split(" · "); return parts[ip] === pub && (ir < 0 || !rep || parts[ir] === rep); };
}
const cpGroupOf = id => id.slice(id.lastIndexOf("@") + 1);
function cpFilter(r, g) {
  const tags = r.tags || [];
  let i = tags.indexOf("groupId") >= 0 ? tags.indexOf("name") : -1;
  if (i >= 0) return label => label.split(" · ")[i] === g;
  i = tags.indexOf("id");
  if (i >= 0) return label => { const x = label.split(" · ")[i] || ""; return x.indexOf("@") >= 0 && cpGroupOf(x) === g; };
  i = tags.indexOf("group");
  if (i >= 0) return label => label.split(" · ")[i] === g;
  return null;
}
// the CP groups the metrics name: Raft groups, the group of each CP object (name@group) and of the sessions
let CPGROUPS = null;
function cpGroups() {
  if (CPGROUPS) return CPGROUPS;
  const set = new Set();
  for (const r of D.metrics) {
    if (r.c !== "cp" || !r.e) continue;
    const tags = r.tags || [], iname = tags.indexOf("groupId") >= 0 ? tags.indexOf("name") : -1, iid = tags.indexOf("id"), ig = tags.indexOf("group");
    for (const e of r.e) {
      const parts = e[0].split(" · ");
      if (iname >= 0 && parts[iname]) set.add(parts[iname]);
      else if (iid >= 0 && (parts[iid] || "").indexOf("@") >= 0) set.add(cpGroupOf(parts[iid]));
      else if (ig >= 0 && parts[ig]) set.add(parts[ig]);
    }
  }
  CPGROUPS = Array.from(set).sort((a, b) => a === "METADATA" ? -1 : b === "METADATA" ? 1 : a < b ? -1 : a > b ? 1 : 0);
  return CPGROUPS;
}
// per selected member, the series of the items a filter keeps: [[member, rows]] by member
function entRowsByMember(r, ef) {
  const sel = selSet(), by = new Map();
  for (const e of eSeries(r)) {
    if (!ef(e.label)) continue;
    for (const s of e.rows) if (sel.has(s.m)) { if (!by.has(s.m)) by.set(s.m, []); by.get(s.m).push(s); }
  }
  return Array.from(by.entries()).sort((a, b) => a[0] - b[0]);
}
function hasTab(tab, test) {
  return D.metrics.some(r => r.c === tab) || D.static.some(r => r.c === tab) || D.flat.some(r => r.c === tab) || PROWS.some(r => test(r.op));
}
// the WAN problems the member logs have (queue full and events dropped, failures, warnings and errors of the WAN classes)
const wanLogHas = () => HASLOGS && LOGS.evbins.some(b => LOGCATS[b[0]][0] === "wan");
function wanLogKpi(kp) {
  const ss = selSet(), rows = LOGS.events.filter(e => LOGCATS[e[2]][0] === "wan" && ss.has(e[1]) && inView(e[0]));
  let n = 0;
  for (const [ci, mi, , bins] of LOGS.evbins) if (LOGCATS[ci][0] === "wan" && ss.has(mi)) n += binsInView(bins);
  const last = rows[rows.length - 1];
  const k = kpi(kp, "WAN problems in member logs", fmtInt(n), last ? trimText(last[5] || "", 70) + " · " + mShort(last[1]) + " at " + shortTime(last[0]) : "none in this time range");
  k.classList.add("link"); k.tabIndex = 0; k.setAttribute("role", "link"); k.title = "Open the member logs on these lines";
  const go = () => { state.logCat = "wan"; setTab("logs"); };
  k.addEventListener("click", go);
  k.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
  if (n) { k.classList.add("alert"); k.querySelector(".kpi-value").classList.add("bad"); }
  return k;
}
function buildWan(sec) {
  if (!hasTab("wan", isWanOp)) {
    el("p", "lead", sec, "No WAN replication metrics or WAN operations in these files: WAN replication (a Hazelcast Enterprise feature) isn't configured on these members, or its metrics aren't in the diagnostics.");
    if (wanLogHas()) {
      el("p", "lead", sec, "The member logs have WAN replication problems (event queue full and events dropped, failures, warnings and errors of the WAN classes):");
      const kp0 = el("div", "kpis", sec);
      tabUpdaters.push(Object.assign(() => { clear(kp0); wanLogKpi(kp0); }, { tab: "wan" }));
    }
    return;
  }
  const wanMetrics = D.metrics.some(r => r.c === "wan") || D.static.some(r => r.c === "wan") || D.flat.some(r => r.c === "wan");
  el("p", "lead", sec, wanMetrics
    ? "WAN replication: the publishers' outbound queues, events sent, acknowledgements, target connections, sync and consistency checks, WAN bytes, and the latency of the WAN operations. Lines: By name splits the metrics per WAN publisher, map or target."
    : "No WAN metrics in these files, only the WAN operations the profilers saw.");
  const pubs = wanPubs();
  if (pubs.length) {
    const lab = el("label", "ctl", ctxRow("wan"));
    el("span", "ctl-label", lab, "WAN publisher");
    const sel = el("select", "opsel", lab);
    el("option", null, sel, "All " + pubs.length + " publisher" + (pubs.length === 1 ? "" : "s")).value = "";
    for (const p of pubs) { const o = el("option", null, sel, wanPubName(p[0])); o.value = p[0]; }
    sel.addEventListener("change", () => { state.wanPub = sel.value || null; update(); });
    syncers.push(() => { sel.value = state.wanPub || ""; });
    const bar = el("div", "selbar", sec); bar.hidden = true;
    tabUpdaters.push(Object.assign(() => {
      clear(bar);
      bar.hidden = !state.wanPub;
      if (!state.wanPub) return;
      bar.appendChild(document.createTextNode("Charts show WAN publisher "));
      el("b", null, bar, wanPubName(state.wanPub));
      bar.appendChild(document.createTextNode(" (its targets and maps too); the WAN bytes and the WAN operations are for all publishers."));
      const b = el("button", "btn btn-s", bar, "Show all"); b.type = "button";
      b.addEventListener("click", () => { state.wanPub = null; update(); });
    }, { tab: "wan" }));
  }
  const kp = el("div", "kpis", sec);
  const renderK = () => {
    clear(kp);
    const npub = state.wanPub ? 1 : itemCount(r => r.c === "wan", ["replication", "publisherId"]);
    if (npub) kpi(kp, state.wanPub ? "WAN publisher" : "WAN publishers", state.wanPub ? wanPubName(state.wanPub) : String(npub), state.wanPub ? "picked in the list" : "whose metrics changed");
    const sent = counterTotal("wan.totalPublishedEventCount");
    if (sent != null) kpi(kp, "Events published", fmtInt(sent), "in the time range in view");
    const q = gaugePeak("wan.outboundQueueSize") || gaugePeak("wan.queueSize");
    if (q) kpi(kp, "Largest outbound queue", fmtInt(q[0]), "events waiting · " + MEMBERS[q[1]].label);
    const dropped = counterTotal("wan.droppedCount");
    if (dropped != null) kpi(kp, "Events dropped", fmtInt(dropped), dropped ? "lost: queue full or publisher stopped" : "none");
    const failed = counterTotal("wan.failedTransmitCount");
    if (failed != null) kpi(kp, "Failed transmissions", fmtInt(failed), failed ? "batches that had to be resent" : "none");
    const bytes = counterTotal("tcp.bytesSend.WAN");
    if (bytes != null) kpi(kp, "WAN bytes sent", fmtBytes(bytes), (state.wanPub ? "all publishers, " : "") + "in the time range in view");
    if (wanLogHas()) wanLogKpi(kp);
  };
  tabUpdaters.push(Object.assign(renderK, { tab: "wan" }));
  const used = new Set();
  headline("wan", sec, [["wan.outboundQueueSize", "Outbound queue"], ["wan.queueFillPercent", "Outbound queue fill"],
    ["wan.queueSize", "WAN queue size"], ["wan.totalPublishedEventCount", "Events published"]], used);
  if (MREC.has("wan.totalPublishLatency") && MREC.has("wan.totalPublishedEventCount")) {
    used.add("wan.totalPublishLatency");
    const unit = MREC.get("wan.totalPublishLatency").u || "ms";
    addPanel("wan", sec, { title: "Average publish latency", code: "wan.totalPublishLatency / wan.totalPublishedEventCount", render: p =>
      drawTS(p, { series: ratioSeries("wan.totalPublishLatency", "wan.totalPublishedEventCount"), unit, note: "per event · highest per {step}" + (state.wanPub ? " · publisher " + wanPubName(state.wanPub) : "") }) });
  }
  headline("wan", sec, [["wan.ackDelayCurrentMillis", "Current acknowledgement delay"], ["wan.ackDelayTotalCount", "Acknowledgement delays"],
    ["wan.ackDelayTotalMillis", "Time spent waiting for acknowledgements"], ["wan.connectionHealth", "Connection health"],
    ["wan.failedTransmitCount", "Failed transmissions"], ["wan.droppedCount", "Events dropped"], ["wan.updateCount", "Updates sent"],
    ["wan.removeCount", "Removes sent"], ["wan.syncCount", "Sync events sent"], ["tcp.bytesSend.WAN", "WAN bytes sent"],
    ["tcp.bytesReceived.WAN", "WAN bytes received"], ["wan.sync.partitionsSynced", "Sync: partitions synced"],
    ["wan.sync.recordsSynced", "Sync: records synced"], ["wan.consistencyCheck.lastDiffPartitionCount", "Consistency check: partitions that differ"],
    ["wan.consistencyCheck.lastEntriesToSync", "Consistency check: entries to sync"]], used);
  if (PROWS.some(r => isWanOp(r.op))) {
    el("h2", null, sec, "WAN operations");
    el("p", "lead", sec, "Profiler data of the operations whose class is in a WAN package or named Wan… (they are on the Latency tab too).");
    latencyBlock("wan", sec, { pred: isWanOp, title: "WAN operations", what: "WAN operation" });
  }
  wanConnections(sec);
  moreSection("wan", sec, used, "All other WAN metrics");
}
// connections opened and closed on a WAN endpoint (advanced networking)
function wanConnections(sec) {
  const all = D.events.filter(e => (e[2] === "ConnectionAdded" || e[2] === "ConnectionRemoved") && /WAN/.test(e[3]));
  if (!all.length) return;
  const card = el("section", "card", sec), cap = el("div", "cap", card);
  el("h3", null, cap, "WAN connections opened and closed");
  const note = el("span", "note", cap);
  const wrap = el("div", "table-wrap", card);
  const render = () => {
    clear(wrap);
    const ss = selSet();
    const rows = all.filter(e => ss.has(e[1]) && e[0] >= state.x0 && e[0] <= state.x1);
    note.textContent = fmtInt(rows.length) + " in this range";
    if (!rows.length) { el("div", "empty-note", wrap, "None in this range."); return; }
    const t = el("table", null, wrap), hr = el("tr", null, el("thead", null, t));
    ["Time", "Seen by", "Event", "Details"].forEach(h => el("th", null, hr, h));
    const tb = el("tbody", null, t);
    for (const e of rows.slice(0, 500)) {
      const tr = el("tr", null, tb);
      el("td", "num", tr, shortTime(e[0])); el("td", null, tr, MEMBERS[e[1]].label); el("td", null, tr, e[2]); el("td", "wide", tr, e[3]);
    }
  };
  tabUpdaters.push(Object.assign(render, { tab: "wan" }));
}
function buildCp(sec) {
  if (!hasTab("cp", isCpOp)) {
    el("p", "lead", sec, "No CP Subsystem metrics or CP operations in these files: the CP Subsystem isn't enabled on these members (cp-member-count 0), or its metrics aren't in the diagnostics.");
    return;
  }
  const cpMetrics = D.metrics.some(r => r.c === "cp") || D.static.some(r => r.c === "cp") || D.flat.some(r => r.c === "cp");
  el("p", "lead", sec, cpMetrics
    ? "CP Subsystem: Raft groups (term, commits, log, snapshots, leader elections), CP members, sessions and CP data structures, and the latency of the CP operations. Every CP member of a group reports that group, so Lines: By name shows one line per Raft group or CP object with the highest value across members."
    : "No CP Subsystem metrics in these files, only the CP operations the profilers saw.");
  const groups = cpGroups();
  if (groups.length > 1) {
    const lab = el("label", "ctl", ctxRow("cp"));
    el("span", "ctl-label", lab, "CP group");
    const sel = el("select", null, lab);
    el("option", null, sel, "All " + groups.length + " CP groups").value = "";
    for (const g of groups) { const o = el("option", null, sel, g); o.value = g; }
    sel.addEventListener("change", () => { state.cpGroup = sel.value || null; update(); });
    syncers.push(() => { sel.value = state.cpGroup || ""; });
    const bar = el("div", "selbar", sec); bar.hidden = true;
    tabUpdaters.push(Object.assign(() => {
      clear(bar);
      bar.hidden = !state.cpGroup;
      if (!state.cpGroup) return;
      bar.appendChild(document.createTextNode("Charts and tables show CP group "));
      el("b", null, bar, state.cpGroup);
      bar.appendChild(document.createTextNode(" (its Raft group, CP objects and sessions); the CP member metrics and the CP operations are for all groups."));
      const b = el("button", "btn btn-s", bar, "Show all"); b.type = "button";
      b.addEventListener("click", () => { state.cpGroup = null; update(); });
    }, { tab: "cp" }));
  }
  const kp = el("div", "kpis", sec);
  const renderK = () => {
    clear(kp);
    const groups = state.cpGroup ? 1 : itemCount(r => r.n.indexOf("raft.group.") === 0, ["groupId"]);
    if (state.cpGroup) kpi(kp, "CP group", state.cpGroup, "picked in the list");
    else if (groups) kpi(kp, "Raft groups", String(groups), "whose metrics changed");
    const members = gaugePeak("raft.metadata.activeMembers");
    if (members) kpi(kp, "Active CP members", fmtInt(members[0]), "highest seen");
    const missing = gaugePeak("raft.missingMembers");
    if (missing) kpi(kp, "Missing CP members", fmtInt(missing[0]), missing[0] ? "highest, seen by " + MEMBERS[missing[1]].label : "none");
    const elections = counterTotal("raft.group.leadership.electedCount", true);
    if (elections != null) kpi(kp, "Leader elections", fmtInt(elections), "in the time range in view");
    const term = gaugePeak("raft.group.term");
    if (term) kpi(kp, "Highest Raft term", fmtInt(term[0]), "goes up at each election");
    const commits = counterTotal("raft.group.commitIndex", true);
    if (commits != null) kpi(kp, "Commits", fmtInt(commits), (state.cpGroup ? "group " + state.cpGroup : "all groups") + ", in the time range in view");
  };
  tabUpdaters.push(Object.assign(renderK, { tab: "cp" }));
  const used = new Set();
  headline("cp", sec, [["raft.group.term", "Raft term"], ["raft.group.leadership.electedCount", "Leader elections"],
    ["raft.group.commitIndex", "Commits"], ["raft.group.lastApplied", "Entries applied"], ["raft.group.lastLogIndex", "Log entries appended"],
    ["raft.group.availableLogCapacity", "Available log capacity"], ["raft.group.snapshotIndex", "Snapshot index"],
    ["raft.group.snapshotBuild.count", "Snapshots taken"], ["raft.group.snapshotBuild.durationMs", "Snapshot build time"],
    ["raft.group.snapshotTransfer.count", "Snapshots sent to followers"], ["raft.group.snapshotTransfer.durationMs", "Snapshot transfer time"],
    ["raft.group.leadership.averageTimeMs", "Average leadership time"], ["raft.metadata.activeMembers", "Active CP members"],
    ["raft.missingMembers", "Missing CP members"], ["raft.metadata.groups", "CP groups"], ["raft.nodes", "Raft nodes on the member"]], used);
  if (structKinds(CP_KINDS, "cp").length) {
    el("h2", null, sec, "Raft groups and CP objects");
    const v = structView("cp", sec, CP_KINDS, "cp", { kindLabel: "Raft groups or kind of CP object", kindText: "Show",
      nameOk: name => !state.cpGroup || (name.indexOf("@") >= 0 ? cpGroupOf(name) : name.split(" · ").pop()) === state.cpGroup,
      nameNote: () => state.cpGroup ? " in CP group " + state.cpGroup : "",
      leadMore: "Every CP member of a group reports the group and its objects: By name and the table take the highest value across members." });
    for (const k of v.K) for (const r of k.recs) used.add(r.n);
  }
  if (PROWS.some(r => isCpOp(r.op))) {
    el("h2", null, sec, "CP operations");
    el("p", "lead", sec, "Profiler data of the operations whose class is in a cp or raft package (they are on the Latency tab too).");
    latencyBlock("cp", sec, { pred: isCpOp, title: "CP operations", what: "CP operation" });
  }
  moreSection("cp", sec, used, "All other CP Subsystem metrics");
}

/* ---------- Threads: stuck IO and operation threads */
const TH = D.threads || { fam: [], list: [], eps: [], detail: {}, cut: 0 };
const THREADS = TH.list.map((x, i) => ({ i, m: x[0], fam: TH.fam[x[1]], kind: x[2], name: x[3], from: x[4], to: x[5] }));
const EPS = TH.eps.map(x => ({ th: THREADS[x[0]], a: x[1], f: x[2], l: x[3], n: x[4], idle: x[5], wait: x[6], then: x[7], tt: x[8] }));
const STUCK_TOTAL = new Set(EPS.map(e => e.th.i)).size;
const STALL_IO = "threads.io.idleWhileTasksWait.max", STALL_OP = "threads.op.noProgressWhileWaiting.max";
const stuckColor = () => getComputedStyle(root).getPropertyValue("--stuck").trim() || "#e34948";
function threadShort(name) {
  const m = /((?:IO\.thread-(?:in|out)-\d+)|(?:(?:priority-)?generic-operation\.thread-\d+)|(?:partition-operation\.thread-\d+))$/.exec(name);
  return m ? m[1] : name.replace(/^hz\.[^.]+\./, "");
}
function threadKind(th) {
  if (th.kind === "io") return th.fam === "tcp.inputThread" ? "IO input" : th.fam === "tcp.outputThread" ? "IO output" : "IO, " + th.fam;
  if (/priority-generic-operation/.test(th.name)) return "priority generic operation";
  if (/generic-operation/.test(th.name)) return "generic operation";
  if (/partition-operation/.test(th.name)) return "partition operation";
  return "operation";
}
const thLabel = th => threadShort(th.name) + " · " + MEMBERS[th.m].label;
const epDur = e => e.l - e.a;
const natCmp = (a, b) => a.localeCompare(b, "en", { numeric: true });
function thenText(e) {
  if (e.then === "ok") return "recovered by " + shortTime(e.tt);
  if (e.then === "restart") return "still stuck when the member restarted (" + shortTime(e.tt) + ")";
  if (e.then === "gone") return "not in the next dump (" + shortTime(e.tt) + ")";
  return "still stuck at the last dump";
}
function threadHit(th, q) {
  return !q || th.name.toLowerCase().indexOf(q) >= 0 || MEMBERS[th.m].label.toLowerCase().indexOf(q) >= 0 || threadKind(th).toLowerCase().indexOf(q) >= 0;
}
// episodes that overlap the time range in view, on the selected members (and matching Find)
function stuckInView(useFind) {
  const sel = selSet(), q = useFind ? findQ() : "";
  return EPS.filter(e => sel.has(e.th.m) && e.l >= state.x0 && e.a <= state.x1 && threadHit(e.th, q));
}
function threadsChecked() {
  const sel = selSet();
  return THREADS.filter(t => sel.has(t.m) && t.to >= state.x0 && t.from <= state.x1);
}
function stuckRows() {
  const per = new Map();
  for (const e of stuckInView(true)) {
    let r = per.get(e.th.i);
    if (!r) { r = { th: e.th, eps: [], tot: 0 }; per.set(e.th.i, r); }
    r.eps.push(e);
    r.tot += Math.max(0, Math.min(e.l, state.x1) - Math.max(e.a, state.x0));
  }
  return Array.from(per.values());
}
// the thread the charts follow: the one picked, else the one stuck the longest in view
function shownThread() {
  if (state.thSel != null) { const th = THREADS[state.thSel]; if (th && selSet().has(th.m)) return { th, picked: true }; }
  const rows = stuckRows();
  if (!rows.length) return null;
  return { th: rows.reduce((a, b) => b.tot > a.tot ? b : a).th, picked: false };
}
function pickThread(i) { state.thSel = state.thSel === i ? null : i; update(); }
function stuckTip(box, e) {
  el("div", "tip-t", box, thLabel(e.th));
  el("div", "tip-sub", box, threadKind(e.th) + " thread · " + span(e.a, e.l));
  tipRow(box, stuckColor(), "≈ " + dur(epDur(e) / 1000), "stuck · work waiting at " + e.n + " dumps in a row", false);
  if (e.th.kind === "io") tipRow(box, null, e.idle != null ? fmtMs(e.idle) : "never", "without I/O (idleTimeMillis, highest)", false);
  tipRow(box, null, fmtInt(e.wait), e.th.kind === "io" ? "tasks waiting (taskQueueSize, highest)" : "operations waiting (pending, highest)", false);
  el("div", "tip-foot", box, "Then: " + thenText(e));
}
function renderStuckLanes(p) {
  let rows = stuckRows();
  const all = rows.length, LIMIT = 40;
  if (rows.length > LIMIT) rows = rows.sort((a, b) => b.tot - a.tot).slice(0, LIMIT);
  rows.sort((a, b) => a.th.m - b.th.m || natCmp(threadShort(a.th.name), threadShort(b.th.name)));
  const W = p.width, M = { l: W < 640 ? 130 : Math.min(340, Math.max(250, Math.round(W * 0.27))), r: 16, t: 8, b: 36 }, rowH = 24;
  const H = M.t + M.b + Math.max(1, rows.length) * rowH + (rows.length ? 0 : 40);
  p.host.style.minHeight = H + "px";
  frame(p, H, M);
  drawMarkers(p, false);
  const shown = shownThread();
  rows.forEach((r, ri) => {
    const y0 = M.t + ri * rowH, cy = y0 + rowH / 2;
    if (shown && shown.th.i === r.th.i) svgEl("rect", { x: 0, y: y0, width: W, height: rowH, class: "row-hl" }, p.g.grid);
    const t = svgEl("text", { x: M.l - 10, y: cy + 4, class: "row-label", "text-anchor": "end" }, p.g.axes);
    t.textContent = trimText(threadShort(r.th.name) + (NM > 1 ? " · " + MEMBERS[r.th.m].label : ""), Math.max(6, Math.floor((M.l - 14) / 6.4)));
    svgEl("title", null, t).textContent = r.th.name + " · " + MEMBERS[r.th.m].label + " · " + threadKind(r.th) + " thread";
    const a = p.sx(Math.max(r.th.from, state.x0)), b = p.sx(Math.min(r.th.to, state.x1));
    if (b > a) svgEl("line", { x1: a, x2: b, y1: Math.round(cy) + 0.5, y2: Math.round(cy) + 0.5, class: "cover" }, p.g.events);
    for (const e of r.eps) {
      const A = p.sx(Math.max(e.a, state.x0)), w = Math.max(4, p.sx(Math.min(e.l, state.x1)) - A);
      e._x0 = A; e._x1 = A + w;
      svgEl("rect", { x: A.toFixed(1), y: (cy - 6).toFixed(1), width: w.toFixed(1), height: 12, rx: 2, class: "stuckbar" }, p.g.marks);
    }
  });
  if (!rows.length) emptyNote(p, EPS.length ? "No thread was stuck in this selection" : "No thread was stuck");
  p.note.textContent = all > LIMIT ? "the " + LIMIT + " stuck the longest, of " + fmtInt(all) : all ? fmtInt(all) + " thread" + (all === 1 ? "" : "s") + " · click one for its charts" : "";
  clear(p.legend);
  p.legend.hidden = !rows.length;
  const it = el("span", "lg lg-static", p.legend), s = el("span", "sw", it); s.style.background = stuckColor();
  el("span", null, it, "stuck: work waiting, no progress");
  el("span", "lg-note", p.legend, "grey line: the time the thread has data for");
  oomeLegend(p.legend);
  const rowAt = py => { const ri = Math.floor((py - M.t) / rowH); return ri >= 0 && ri < rows.length ? ri : -1; };
  p.tipAt = (t, py, px) => {
    const ri = rowAt(py);
    if (ri < 0) return null;
    let best = null, bd = 10;
    for (const e of rows[ri].eps) { const d = px < e._x0 ? e._x0 - px : px > e._x1 ? px - e._x1 : 0; if (d < bd) { bd = d; best = e; } }
    if (!best) return null;
    clear(p.hot);
    svgEl("rect", { x: best._x0 - 2, y: M.t + ri * rowH + rowH / 2 - 8, width: best._x1 - best._x0 + 4, height: 16, rx: 3, class: "bar-hot" }, p.hot);
    return box => stuckTip(box, best);
  };
  p.onClick = pt => { const ri = rowAt(pt[1]); if (ri >= 0) pickThread(rows[ri].th.i); };
  p.svg.classList.add("clickable");
  finish(p);
}
// one series of the shown thread, at each dump: w tasks or operations waiting, i idle ms, r completed per second
function threadPts(th, key) {
  const pts = [];
  for (const w of (TH.detail[th.i] || [])) { const arr = w[key]; if (!arr) continue; for (let j = 0; j < w.t.length; j++) if (arr[j] != null) pts.push([w.t[j], arr[j]]); }
  return pts;
}
function renderThreadChart(p, key) {
  const sh = shownThread();
  if (!sh) { drawTS(p, { series: [], unit: "count", empty: "No stuck thread to show" }); return; }
  const th = sh.th, io = th.kind === "io";
  const meta = {
    w: [io ? "Tasks waiting" : "Operations waiting", io ? th.fam + ".taskQueueSize" : "operation.thread.normalPendingCount + priorityPendingCount", "count", ""],
    i: ["Time since its last I/O", th.fam + ".idleTimeMillis", "ms", ""],
    r: [io ? "Tasks completed per second" : "Operations completed per second", io ? th.fam + ".completedTaskCount" : "operation.thread.completedTotalCount", "count", "/s"]
  }[key];
  p.h3.textContent = meta[0];
  if (p.code) p.code.textContent = meta[1];
  const pts = threadPts(th, key);
  const bands = EPS.filter(e => e.th === th).map(e => [e.a, e.l]);
  drawTS(p, { series: pts.length ? [{ key: "t", label: thLabel(th), color: memberColor(th.m), pts, join: 2.5 * G.step, dots: pts.length <= 240 }] : [],
    unit: meta[2], rate: meta[3], bands, legendAlways: true, noDirect: true,
    legendNote: "red band: stuck",
    note: "at each metrics dump" + (key === "r" ? ", since the one before" : ""),
    empty: TH.detail[th.i] ? "No data for this thread here" : "Not kept for this thread (too many stuck threads for the report size)" });
}
function buildThreads(sec) {
  if (!THREADS.length && !D.metrics.some(r => r.c === "threads")) {
    el("p", "lead", sec, "No IO thread or operation thread metrics in these files (tcp.inputThread.*, tcp.outputThread.*, operation.thread.*): they come from the MetricsPlugin.");
    unchangedSection("threads", sec);
    return;
  }
  el("p", "lead", sec, THREADS.length
    ? "Whether an IO or operation thread was stuck: it stays idle while its queue stays above 0, at two metrics dumps in a row at least. For an IO thread, tasks wait (taskQueueSize > 0) while idleTimeMillis keeps growing (no I/O since the dump before); for a partition or generic operation thread, operations wait and none completes. Checked at every metrics dump (" + stepName(G.step) + "), for every thread of every member."
    : "Per-thread metrics. The stuck check needs taskQueueSize and idleTimeMillis (IO threads) or pending counts and completedTotalCount (operation threads), which these files don't have.");
  if (THREADS.length) {
    const kp = el("div", "kpis", sec);
    const renderK = () => {
      clear(kp);
      const eps = stuckInView(false), checked = threadsChecked(), n = new Set(eps.map(e => e.th.i)).size;
      const nio = checked.filter(t => t.kind === "io").length;
      const k = kpi(kp, "Stuck threads", fmtInt(n), n ? "of " + fmtInt(checked.length) + " checked, in the time range in view" : "none of the " + fmtInt(checked.length) + " threads checked");
      if (n) k.querySelector(".kpi-value").classList.add("bad");
      if (eps.length) {
        const w = eps.reduce((a, b) => epDur(b) > epDur(a) ? b : a);
        kpi(kp, "Longest", "≈ " + dur(epDur(w) / 1000), thLabel(w.th));
        const still = new Set(eps.filter(e => e.then === "end" || e.then === "restart").map(e => e.th.i)).size;
        kpi(kp, "Still stuck at the last dump", fmtInt(still), still ? "before a restart or the end of the logs" : "all recovered");
        const mw = eps.reduce((a, b) => b.wait > a.wait ? b : a);
        kpi(kp, "Most work waiting", fmtInt(mw.wait), (mw.th.kind === "io" ? "tasks · " : "operations · ") + thLabel(mw.th));
      } else kpi(kp, "Threads checked", fmtInt(checked.length), nio + " IO, " + (checked.length - nio) + " operation");
    };
    tabUpdaters.push(Object.assign(renderK, { tab: "threads" }));
  }
  if (EPS.length) {
    const lanes = addPanel("threads", sec, { title: "When threads were stuck", height: 120, noTable: true, render: renderStuckLanes });
    lanes.fixed = true;
    // the episodes
    const card = el("section", "card", sec), cap = el("div", "cap", card);
    el("h3", null, cap, "Stuck episodes");
    const tnote = el("span", "note", cap);
    const wrap = el("div", "table-wrap", card);
    const COLS = [
      { key: "m", label: "Member", val: e => MEMBERS[e.th.m].label },
      { key: "th", label: "Thread", val: e => threadShort(e.th.name) },
      { key: "kind", label: "Kind", val: e => threadKind(e.th) },
      { key: "a", label: "Stuck from", num: true, val: e => e.a, fmt: e => shortTime(e.a) },
      { key: "l", label: "To", num: true, val: e => e.l, fmt: e => shortTime(e.l) },
      { key: "d", label: "Stuck for", num: true, val: e => epDur(e), fmt: e => "≈ " + dur(epDur(e) / 1000) },
      { key: "n", label: "Dumps", num: true, val: e => e.n, fmt: e => fmtInt(e.n) },
      { key: "idle", label: "No I/O for", num: true, val: e => e.idle == null ? -1 : e.idle, fmt: e => e.th.kind !== "io" ? "" : e.idle == null ? "never" : fmtMs(e.idle) },
      { key: "w", label: "Waiting", num: true, val: e => e.wait, fmt: e => fmtInt(e.wait) },
      { key: "then", label: "Then", val: e => thenText(e) }
    ];
    const render = () => {
      clear(wrap);
      const eps = stuckInView(true);
      const col = COLS.find(c => c.key === state.thSort.key) || COLS[3];
      eps.sort((a, b) => { const x = col.val(a), y = col.val(b); return (x < y ? -1 : x > y ? 1 : 0) * state.thSort.dir || a.a - b.a || a.th.i - b.th.i; });
      const sh = shownThread();
      tnote.textContent = eps.length ? fmtInt(eps.length) + " in the time range in view · times are as precise as the metrics dumps · click one for its charts" : "";
      if (!eps.length) { el("div", "empty-note", wrap, EPS.length ? "None in this selection." : "No thread was stuck: no thread had work waiting while making no progress."); return; }
      const t = el("table", null, wrap), hr = el("tr", null, el("thead", null, t));
      for (const c of COLS) {
        const th = el("th", c.num ? "num" : null, hr);
        const b = el("button", null, th, c.label + (state.thSort.key === c.key ? (state.thSort.dir < 0 ? " ↓" : " ↑") : ""));
        b.type = "button";
        if (state.thSort.key === c.key) th.setAttribute("aria-sort", state.thSort.dir < 0 ? "descending" : "ascending");
        b.addEventListener("click", () => { state.thSort = { key: c.key, dir: state.thSort.key === c.key ? -state.thSort.dir : (c.num && c.key !== "a" && c.key !== "l" ? -1 : 1) }; render(); });
      }
      const tb = el("tbody", null, t);
      for (const e of eps.slice(0, 500)) {
        const tr = el("tr", "click" + (sh && sh.picked && sh.th === e.th ? " sel" : ""), tb);
        tr.tabIndex = 0;
        for (const c of COLS) {
          const td = el("td", c.num ? "num" : c.key === "th" ? "op" : null, tr, c.fmt ? c.fmt(e) : c.val(e));
          if (c.key === "th") td.title = e.th.name;
        }
        const pick = () => pickThread(e.th.i);
        tr.addEventListener("click", pick);
        tr.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } });
      }
      if (eps.length > 500) el("div", "hint", wrap, "First 500 of " + fmtInt(eps.length) + "; zoom in or use Find.");
    };
    tabUpdaters.push(Object.assign(render, { tab: "threads" }));
    // the charts of one thread
    const head = el("h2", null, sec), lead = el("p", "lead", sec), selbar = el("div", "selbar", sec);
    const charts = ["w", "i", "r"].map(k => {
      const p = addPanel("threads", sec, { title: k, code: " ", height: 180, render: pp => renderThreadChart(pp, k) });
      p.code = p.card.querySelector("code");
      p.fixed = true;
      return p;
    });
    tabUpdaters.push(Object.assign(() => {
      const sh = shownThread();
      head.hidden = lead.hidden = !sh;
      clear(selbar); selbar.hidden = !sh;
      charts[0].card.hidden = !sh;
      charts[1].card.hidden = !sh || sh.th.kind !== "io";
      charts[2].card.hidden = !sh || !(TH.detail[sh.th.i] || []).some(w => w.r);
      if (!sh) return;
      head.textContent = threadShort(sh.th.name) + " on " + MEMBERS[sh.th.m].label;
      lead.textContent = sh.th.name + " · " + threadKind(sh.th) + " thread. Each point is one metrics dump; the red bands are its stuck episodes" +
        (sh.th.kind === "io" ? ", where the time since its last I/O climbs while tasks wait." : ", where operations wait and none complete.") +
        (sh.picked ? "" : " This is the thread stuck the longest in view; click another one above.");
      selbar.appendChild(document.createTextNode("Charts show "));
      el("b", null, selbar, thLabel(sh.th));
      const z = el("button", "btn btn-s", selbar, "Zoom to its stuck time"); z.type = "button";
      z.addEventListener("click", () => {
        const mine = EPS.filter(e => e.th === sh.th);
        if (!mine.length) return;
        const a = Math.min(...mine.map(e => e.a)), b = Math.max(...mine.map(e => e.l)), pad = Math.max(10 * G.step, (b - a) * 0.6);
        setView(a - pad, b + pad);
      });
      if (sh.picked) {
        const b = el("button", "btn btn-s", selbar, "Back to the longest"); b.type = "button";
        b.addEventListener("click", () => { state.thSel = null; update(); });
      }
    }, { tab: "threads" }));
  }
  // the rule over time, per member, then every per-thread metric, by member or by thread
  const used = new Set();
  const ioList = [["tcp.inputThread.idleTimeMillis"], ["tcp.outputThread.idleTimeMillis"], ["tcp.inputThread.taskQueueSize"],
    ["tcp.outputThread.taskQueueSize"], ["tcp.inputThread.completedTaskCount"], ["tcp.outputThread.completedTaskCount"],
    ["tcp.inputThread.bytesTransceived"], ["tcp.outputThread.bytesTransceived"], ["tcp.inputThread.eventCount"], ["tcp.outputThread.eventCount"]];
  const opList = [["operation.thread.normalPendingCount"], ["operation.thread.priorityPendingCount"], ["operation.thread.completedTotalCount"]];
  const thr = G.step;
  const stallNote = (name, chart, never) => {
    if (MREC.has(name)) return chart;
    const st = D.static.find(x => x.n === name);
    return st && st.v.every(x => x[1] === 0) ? never : "";
  };
  const stallPanel = (name, code, refLabel) => {
    used.add(name);
    const p = metricPanel("threads", sec, name, prettyName(name), { tweak: spec => {
      spec.note = "highest per {step} · highest thread of each member";
      if (NM <= 8) { spec.series.push({ key: "ref", label: refLabel, color: null, faint: true, runs: [[X_MIN, X_MAX, thr]] }); spec.faintLabel = refLabel; }
    } });
    if (p) p.card.querySelector("code").textContent = code;
  };
  for (const [title, list, intro, stall] of [
    ["IO threads", ioList, "Each member's IO threads read (input) and write (output) the connections. " +
      stallNote(STALL_IO, "The first chart is the stuck check over time: for each member, the time without I/O of its IO threads that have tasks waiting (0 when none has); above the dump period, tasks wait while a thread does nothing. ",
        "The stuck check never fired: no IO thread had tasks waiting while it did no I/O. ") +
      "By member: the highest time since the last I/O, the sum of the rest; Lines: By name shows each thread.",
      () => stallPanel(STALL_IO, "idleTimeMillis of the IO threads with taskQueueSize > 0", "one dump period (" + stepName(G.step) + ")")],
    ["Operation threads", opList, "Partition and generic operation threads. " +
      stallNote(STALL_OP, "The first chart is the stuck check over time: for each member, how long its operation threads with operations waiting have completed none (0 when all make progress). ",
        "The stuck check never fired: no operation thread had operations waiting while it completed none. ") +
      "Lines: By name shows each thread.",
      () => stallPanel(STALL_OP, "time with operations pending and completedTotalCount unchanged", "one dump period (" + stepName(G.step) + ")")]]) {
    if (!list.some(x => MREC.has(x[0]))) continue;
    el("h2", null, sec, title);
    el("p", "lead", sec, intro);
    stall();
    headline("threads", sec, list, used);
  }
  moreSection("threads", sec, used, "All other thread metrics");
}

/* ---------- Saturated executors (diagnostics): pool full and tasks queued at two dumps in a row */
const POOLS = D.pools || { list: [], eps: [] };
const SATS = POOLS.eps.map(x => ({ ex: POOLS.list[x[0]], xi: x[0], a: x[1], l: x[2], n: x[3], q: x[4], pool: x[5], max: x[6], rate: x[7], then: x[8], tt: x[9], full: x[10] }));
const SAT_TOTAL = new Set(SATS.map(e => e.xi)).size;
const satDur = e => e.l - e.a;
const satLabel = e => e.ex[2] + " · " + MEMBERS[e.ex[0]].label;
function satInView() { const ss = selSet(); return SATS.filter(e => ss.has(e.ex[0]) && e.l >= state.x0 && e.a <= state.x1); }
function satThen(e) {
  if (e.then === "ok") return "no longer saturated at the next dump (" + shortTime(e.tt) + ")";
  if (e.then === "restart") return "still saturated when the member restarted (" + shortTime(e.tt) + ")";
  return "still saturated at the last dump";
}
// sort orders of the tables that are rebuilt on every update
const SORTS = { ev: { key: "t0", dir: 1 }, oome: { key: "t0", dir: 1 }, msg: { key: "n", dir: -1 }, cp: { key: "t0", dir: 1 } };
function buildSaturation(sec) {
  if (!POOLS.list.length) return;
  if (!SATS.length) {
    el("p", "lead", sec, "No executor stayed saturated: none of the " + fmtInt(POOLS.list.length) + " executors with pool metrics had its pool full (poolSize = maximumPoolSize) with tasks queued (queueSize > 0) at two dumps in a row.");
    return;
  }
  const card = el("section", "card alert", sec), cap = el("div", "cap", card);
  el("h3", null, cap, "Saturated executors");
  const note = el("span", "note", cap);
  el("p", "lead", card, "An executor is saturated when its pool is full (poolSize = maximumPoolSize) and tasks wait in its queue (queueSize > 0) at two metrics dumps in a row at least: every thread of the pool is busy and work piles up. Raise its pool size, or find what makes its tasks slow.");
  const p = addPanel("operations", card, { title: "When", height: 100, noTable: true, render: pp => {
    const rows = [], by = new Map(), eps = satInView(), many = new Set(eps.map(e => e.ex[0])).size > 1;
    for (const e of eps) {
      let r = by.get(e.xi);
      if (!r) { r = { label: many ? MEMBERS[e.ex[0]].label + " · " + e.ex[2] : e.ex[2], title: e.ex[1] + " · " + satLabel(e), eps: [] }; by.set(e.xi, r); rows.push(r); }
      r.eps.push(e);
    }
    rows.sort((a, b) => a.eps[0].ex[0] - b.eps[0].ex[0] || a.label.localeCompare(b.label));
    renderBarLanes(pp, rows, { empty: "No executor was saturated in this selection", color: () => stuckColor(), tip: (box, e) => {
      el("div", "tip-t", box, satLabel(e));
      el("div", "tip-sub", box, span(e.a, e.l));
      tipRow(box, stuckColor(), fmtInt(e.n), "dumps in a row with the pool full and tasks queued", false);
      tipRow(box, null, fmtInt(e.q), "tasks queued, highest", false);
      tipRow(box, null, fmtInt(e.pool) + " / " + fmtInt(e.max), "threads in the pool / maximum", false);
      if (e.rate != null) tipRow(box, null, fmtNum(e.rate) + "/s", "tasks completed meanwhile", false);
      el("div", "tip-foot", box, "Then: " + satThen(e));
    } });
  } });
  p.fixed = true;
  p.card.classList.remove("card");
  p.card.querySelector(".cap").hidden = true;
  const wrap = el("div", "table-wrap", card);
  const COLS = [
    { key: "m", label: "Member", val: e => MEMBERS[e.ex[0]].label },
    { key: "x", label: "Executor", val: e => e.ex[2], cls: "op", title: e => e.ex[1] + " · " + e.ex[2] },
    { key: "a", label: "From", num: true, val: e => e.a, fmt: e => shortTime(e.a) },
    { key: "l", label: "To", num: true, val: e => e.l, fmt: e => shortTime(e.l) },
    { key: "d", label: "For", num: true, val: satDur, fmt: e => "≈ " + dur(satDur(e) / 1000) },
    { key: "n", label: "Dumps", num: true, val: e => e.n, fmt: e => fmtInt(e.n) },
    { key: "q", label: "Queued (max)", num: true, val: e => e.q, fmt: e => fmtInt(e.q) },
    { key: "p", label: "Pool / max", num: true, val: e => e.pool, fmt: e => fmtInt(e.pool) + " / " + fmtInt(e.max) },
    { key: "r", label: "Completed/s", num: true, val: e => e.rate == null ? -1 : e.rate, fmt: e => e.rate == null ? "" : fmtNum(e.rate) },
    { key: "t", label: "Then", val: e => satThen(e) + (e.full ? " · its queue was full (remainingQueueCapacity 0)" : ""), cls: "wrapcell" }
  ];
  const render = () => {
    clear(wrap);
    const q = findQ();
    const eps = satInView().filter(e => !q || e.ex[2].toLowerCase().indexOf(q) >= 0 || MEMBERS[e.ex[0]].label.toLowerCase().indexOf(q) >= 0);
    const n = new Set(eps.map(e => e.xi)).size;
    note.textContent = fmtInt(n) + " executor" + (n === 1 ? "" : "s") + " in the time range in view · times are as precise as the metrics dumps";
    sortableTable(wrap, COLS, eps, state.satSort, render, null, "None in this selection.");
  };
  tabUpdaters.push(Object.assign(render, { tab: "operations" }));
}
// a sortable table: cols {key, label, num, val, fmt, cls, title, lv}; sortState {key, dir} (kept between renders);
// onPick(row) makes the rows clickable, isSel(row) marks the picked one
function sortableTable(wrap, COLS, rows, sortState, rerender, onPick, empty, isSel, limit) {
  if (!rows.length) { el("div", "empty-note", wrap, empty || "Nothing in this selection."); return; }
  const col = COLS.find(c => c.key === sortState.key) || COLS[0];
  rows = rows.slice().sort((a, b) => { const x = col.val(a), y = col.val(b); return (x < y ? -1 : x > y ? 1 : 0) * sortState.dir; });
  const t = el("table", null, wrap), hr = el("tr", null, el("thead", null, t));
  for (const c of COLS) {
    const th = el("th", c.num ? "num" : null, hr);
    const b = el("button", null, th, c.label + (sortState.key === c.key ? (sortState.dir < 0 ? " ↓" : " ↑") : ""));
    b.type = "button";
    if (sortState.key === c.key) th.setAttribute("aria-sort", sortState.dir < 0 ? "descending" : "ascending");
    b.addEventListener("click", () => {
      sortState.dir = sortState.key === c.key ? -sortState.dir : (c.num && c.key !== "a" && c.key !== "t0" ? -1 : 1);
      sortState.key = c.key;
      rerender();
    });
  }
  const tb = el("tbody", null, t), max = limit || 500;
  for (const r of rows.slice(0, max)) {
    const tr = el("tr", (onPick ? "click" : "") + (isSel && isSel(r) ? " sel" : ""), tb);
    for (const c of COLS) {
      const td = el("td", c.cls || (c.num ? "num" : null), tr, c.fmt ? c.fmt(r) : c.val(r));
      if (c.title) td.title = c.title(r);
      if (c.lv) td.classList.add("lvl-" + c.lv(r));
      if (c.cellFn) c.cellFn(r)(td);
      if (c.data) td.setAttribute("data-v", String(Math.round(c.data(r))));
    }
    if (onPick) {
      tr.tabIndex = 0;
      tr.addEventListener("click", () => onPick(r));
      tr.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); onPick(r); } });
    }
  }
  if (rows.length > max) el("div", "hint", wrap, "First " + fmtInt(max) + " of " + fmtInt(rows.length) + "; zoom in or use Find.");
}
const laneMargins = W => ({ l: W < 640 ? 130 : Math.min(340, Math.max(250, Math.round(W * 0.27))), r: 16, t: 8, b: 36 });
// rows of bars on a time axis: rows [{label, title, eps: [{a, l, ...}]}]
function renderBarLanes(p, rows, o) {
  const W = p.width, M = laneMargins(W), rowH = 24;
  const H = M.t + M.b + Math.max(1, rows.length) * rowH + (rows.length ? 0 : 40);
  p.host.style.minHeight = H + "px";
  frame(p, H, M);
  drawMarkers(p, false);
  rows.forEach((r, ri) => {
    const cy = M.t + ri * rowH + rowH / 2;
    const t = svgEl("text", { x: M.l - 10, y: cy + 4, class: "row-label", "text-anchor": "end" }, p.g.axes);
    t.textContent = trimText(r.label, Math.max(6, Math.floor((M.l - 14) / 6.4)));
    svgEl("title", null, t).textContent = r.title || r.label;
    svgEl("line", { x1: M.l, x2: W - M.r, y1: Math.round(cy) + 0.5, y2: Math.round(cy) + 0.5, class: "cover" }, p.g.events);
    for (const e of r.eps) {
      const A = p.sx(Math.max(e.a, state.x0)), w = Math.max(4, p.sx(Math.min(e.l, state.x1)) - A);
      e._x0 = A; e._x1 = A + w;
      const rect = svgEl("rect", { x: A.toFixed(1), y: (cy - 6).toFixed(1), width: w.toFixed(1), height: 12, rx: 2 }, p.g.marks);
      rect.style.fill = o.color(e);
    }
  });
  if (!rows.length) emptyNote(p, o.empty);
  clear(p.legend);
  p.legend.hidden = !oomeLegend(p.legend);
  p.tipAt = (t, py, px) => {
    const ri = Math.floor((py - M.t) / rowH);
    if (ri < 0 || ri >= rows.length) return null;
    let best = null, bd = 10;
    for (const e of rows[ri].eps) { const d = px < e._x0 ? e._x0 - px : px > e._x1 ? px - e._x1 : 0; if (d < bd) { bd = d; best = e; } }
    if (!best) return null;
    clear(p.hot);
    svgEl("rect", { x: best._x0 - 2, y: M.t + ri * rowH + rowH / 2 - 8, width: best._x1 - best._x0 + 4, height: 16, rx: 3, class: "bar-hot" }, p.hot);
    return box => o.tip(box, best);
  };
  finish(p);
}
// a KPI tile that opens a tab
function linkKpi(k, tab, title) {
  k.classList.add("link"); k.tabIndex = 0; k.setAttribute("role", "link"); k.title = title;
  k.addEventListener("click", () => setTab(tab));
  k.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setTab(tab); } });
  return k;
}
// points [t, v] in time order as steps: each value holds until the next one, the last one until `end`
function stepRuns(pts, end) {
  const runs = [];
  for (const [t, v] of pts) {
    const q = runs[runs.length - 1];
    if (q && q[2] === v) { q[1] = t; continue; }
    if (q) q[1] = t;
    runs.push([t, t, v]);
  }
  if (runs.length && end != null) runs[runs.length - 1][1] = Math.max(runs[runs.length - 1][1], end);
  return runs;
}

/* ---------- Member logs: Overview, Health monitor, Exceptions, CP Subsystem */
const LOGS = D.logs;
const LOGCATS = HASLOGS ? LOGS.cats : [];
const LVL_NAME = { E: "ERROR", W: "WARN", I: "INFO", D: "DEBUG" };
const cssVar = (name, dflt) => getComputedStyle(root).getPropertyValue(name).trim() || dflt;
const warnColor = () => cssVar("--warn", "#c98500");
const sevColor = sev => sev >= 2 ? stuckColor() : sev === 1 ? warnColor() : grey();
const shortCls = c => c ? c.slice(c.lastIndexOf(".") + 1) : "";
const inView = t => t >= state.x0 && t <= state.x1;
function binsInView(bins) { let n = 0; for (const b of bins) if (b[0] >= state.x0 && b[0] <= state.x1) n += b[1]; return n; }
function logSpan() {
  const ss = selSet();
  let a = Infinity, b = -Infinity;
  for (const r of (HASLOGS ? LOGS.info : [])) if (ss.has(r[0]) && r[4] != null) { a = Math.min(a, r[4]); b = Math.max(b, r[5]); }
  return a <= b ? [a, b] : null;
}
function logEnd(mi) { const r = HASLOGS ? LOGS.info.find(x => x[0] === mi) : null; return r && r[5] != null ? r[5] : X_MAX; }
// kinds of lines with more than the report keeps: [member, kind ("cp:..." for the CP Subsystem), lines, kept]
const SAMPLED = HASLOGS ? (LOGS.sampled || []) : [];
const EV_CAPPED = SAMPLED.some(x => x[1].indexOf("cp:") !== 0), CP_CAPPED = SAMPLED.some(x => x[1].indexOf("cp:") === 0);
const CAPPED_NOTE = " · some kinds had more lines than the report keeps: 1,000 to 2,000 of each per member, spread over the whole log (counts and charts include them all)";
function logBanner(sec, what) {
  sec.classList.add("logsec");
  const b = el("div", "srcbanner", sec);
  el("b", null, b, "Member logs");
  b.appendChild(document.createTextNode(" · " + what + " · " + fmtInt(META.logfiles) + " log file" + (META.logfiles === 1 ? "" : "s") + " (" +
    fmtBytes(META.logbytes) + ") from " + LOGS.info.length + " member" + (LOGS.info.length === 1 ? "" : "s")));
}
// rows of dots on a time axis, the dot area by how many: rows [{key, label, title, color, bins: [[t, count]...]}]
function renderDotLanes(p, rows, o) {
  const W = p.width, M = laneMargins(W), rowH = 22;
  const H = M.t + M.b + Math.max(1, rows.length) * rowH + (rows.length ? 0 : 40);
  p.host.style.minHeight = H + "px";
  frame(p, H, M);
  drawMarkers(p, false);
  const plotW = W - M.l - M.r, nd = Math.max(10, Math.floor(plotW / 9)), w = (state.x1 - state.x0) / nd;
  let maxN = 1;
  const dots = rows.map(r => {
    const map = new Map();
    for (const b of r.bins) {
      if (b[0] < state.x0 || b[0] > state.x1) continue;
      const d = Math.max(0, Math.min(nd - 1, Math.floor((b[0] - state.x0) / w)));
      let g = map.get(d);
      if (!g) { g = { n: 0, wt: 0, t0: b[0], t1: b[0] }; map.set(d, g); }
      g.n += b[1]; g.wt += b[0] * b[1]; g.t0 = Math.min(g.t0, b[0]); g.t1 = Math.max(g.t1, b[0]);
    }
    const arr = Array.from(map.values());
    for (const g of arr) if (g.n > maxN) maxN = g.n;
    return arr;
  });
  const sp = logSpan();
  rows.forEach((r, ri) => {
    const cy = M.t + ri * rowH + rowH / 2;
    if (o.selected && o.selected(r)) svgEl("rect", { x: 0, y: M.t + ri * rowH, width: W, height: rowH, class: "row-hl" }, p.g.grid);
    const t = svgEl("text", { x: M.l - 10, y: cy + 4, class: "row-label", "text-anchor": "end" }, p.g.axes);
    t.textContent = trimText(r.label, Math.max(6, Math.floor((M.l - 14) / 6.4)));
    svgEl("title", null, t).textContent = r.title || r.label;
    if (sp && sp[1] >= state.x0 && sp[0] <= state.x1)
      svgEl("line", { x1: p.sx(Math.max(sp[0], state.x0)), x2: p.sx(Math.min(sp[1], state.x1)), y1: Math.round(cy) + 0.5, y2: Math.round(cy) + 0.5, class: "cover" }, p.g.events);
    for (const g of dots[ri]) {
      g.cx = p.sx(g.wt / g.n); g.cy = cy; g.r = 3.5 + 5 * Math.sqrt(g.n / maxN);
      const c = svgEl("circle", { cx: g.cx.toFixed(1), cy: cy.toFixed(1), r: g.r.toFixed(1), class: "dot" }, p.g.marks);
      c.style.fill = r.color;
    }
  });
  if (!rows.length) emptyNote(p, o.empty);
  clear(p.legend);
  if (o.legend && rows.length) o.legend(p.legend);
  const oo = oomeLegend(p.legend);
  p.legend.hidden = !(o.legend && rows.length) && !oo;
  const rowAt = py => { const ri = Math.floor((py - M.t) / rowH); return ri >= 0 && ri < rows.length ? ri : -1; };
  p.tipAt = (t, py, px) => {
    const ri = rowAt(py);
    if (ri < 0) return null;
    let best = null, bd = 14;
    for (const g of dots[ri]) { const d = Math.abs(g.cx - px); if (d < bd) { bd = d; best = g; } }
    if (!best) return null;
    clear(p.hot);
    svgEl("circle", { cx: best.cx, cy: best.cy, r: best.r + 2.5, class: "dot-hot" }, p.hot);
    const r = rows[ri];
    return box => {
      el("div", "tip-t", box, r.title || r.label);
      el("div", "tip-sub", box, best.t0 === best.t1 ? logTime(best.t0) : span(best.t0, best.t1));
      tipRow(box, r.color, fmtInt(best.n), o.what || "lines", false);
      if (o.onPick) el("div", "tip-foot", box, o.pickHint || "Click to list them");
    };
  };
  if (o.onPick) { p.onClick = pt => { const ri = rowAt(pt[1]); if (ri >= 0) o.onPick(rows[ri]); }; p.svg.classList.add("clickable"); }
  finish(p);
}
function sevLegend(host) {
  for (const [sev, label] of [[2, "critical"], [1, "warning"], [0, "information"]]) {
    const it = el("span", "lg lg-static", host), s = el("span", "sw", it);
    s.style.background = sevColor(sev); s.style.borderRadius = "50%";
    el("span", null, it, label);
  }
  el("span", "lg-note", host, "dot size: how many lines");
}
// warning and error lines are counted per second (per minute for a member with too many): the finest bin
function levelStep() { const ss = selSet(); let st = 1000; for (const r of LOGS.levels) if (ss.has(r[0]) && r[2] > st) st = r[2]; return st; }
const levelIn = (t, st) => t + st > state.x0 && t <= state.x1;
// warnings and errors in bins that fit the time range in view, summed over the selected members
function logLevelSeries(p) {
  const ss = selSet(), fine = levelStep();
  const want = (state.x1 - state.x0) / Math.max(20, plotWidth(p) / 6);
  const step = Math.max(fine, TSTEPS.find(x => x >= want) || TSTEPS[TSTEPS.length - 1]);
  const t0 = Math.floor(state.x0 / step) * step, n = Math.max(1, Math.ceil((state.x1 - t0) / step));
  const vw = new Float64Array(n), ve = new Float64Array(n);
  for (const [mi, rows] of LOGS.levels) {
    if (!ss.has(mi)) continue;
    for (const r of rows) {
      if (r[0] < t0) continue;
      const i = Math.floor((r[0] - t0) / step);
      if (i >= n) break;
      vw[i] += r[1]; ve[i] += r[2];
    }
  }
  const base = { t0, step, o: Math.round(t0 / step), f: 1, agg: "sum", noAgg: true };
  const amount = col => (a, b) => { let n = 0; for (const [mi, rows, st] of LOGS.levels) if (ss.has(mi)) n += rowsSum(rows, st, a, b, col); return n; };
  return [Object.assign({ key: "E", label: "errors (ERROR, SEVERE)", color: stuckColor(), v: ve, amount: amount(2) }, base),
    Object.assign({ key: "W", label: "warnings (WARN, WARNING)", color: warnColor(), v: vw, amount: amount(1) }, base)];
}
// the notable kinds of lines, with their times on the selected members
function catRows() {
  const ss = selSet(), by = new Map();
  for (const [ci, mi, , bins] of LOGS.evbins) {
    if (!ss.has(mi)) continue;
    let r = by.get(ci);
    if (!r) { const c = LOGCATS[ci]; r = { key: c[0], ci, label: c[1], sev: c[2], color: sevColor(c[2]), bins: [], per: new Map() }; by.set(ci, r); }
    for (const b of bins) r.bins.push(b);
    r.per.set(mi, (r.per.get(mi) || 0) + binsInView(bins));
  }
  for (const r of by.values()) { r.n = 0; for (const v of r.per.values()) r.n += v; }
  return Array.from(by.values()).filter(r => r.n > 0).sort((a, b) => a.ci - b.ci);
}
const perText = r => Array.from(r.per.entries()).filter(x => x[1]).map(x => MEMBERS[x[0]].label + (r.per.size > 1 ? " " + fmtInt(x[1]) : "")).join(", ");
function buildLogsOverview(sec) {
  logBanner(sec, "what the members logged");
  const kp = el("div", "kpis", sec);
  const renderK = () => {
    clear(kp);
    const ss = selSet();
    const oo = LOG_OOME.filter(o => ss.has(o[1]) && inView(o[0]));
    const last = oo[oo.length - 1];
    const k1 = linkKpi(kpi(kp, "Out of memory errors", fmtInt(oo.length), last ? (last[2] || "OutOfMemoryError") + " · " + MEMBERS[last[1]].label + " at " + shortTime(last[0]) : "no OutOfMemoryError"), "logs-exceptions", "Open the Exceptions tab");
    if (oo.length) { k1.classList.add("alert"); k1.querySelector(".kpi-value").classList.add("bad"); }
    let nx = 0, kinds = 0;
    for (const g of LOGX) { const n = excInView(g); nx += n; if (n) kinds++; }
    linkKpi(kpi(kp, "Exceptions", fmtInt(nx), fmtInt(kinds) + " kind" + (kinds === 1 ? "" : "s") + " · see Exceptions"), "logs-exceptions", "Open the Exceptions tab");
    let we = 0, ee = 0;
    for (const [mi, rows, st] of LOGS.levels) if (ss.has(mi)) for (const r of rows) if (levelIn(r[0], st)) { we += r[1]; ee += r[2]; }
    const k3 = kpi(kp, "Errors", fmtInt(ee), "ERROR and SEVERE lines");
    if (ee) k3.querySelector(".kpi-value").classList.add("bad");
    kpi(kp, "Warnings", fmtInt(we), "WARN and WARNING lines");
    for (const r of catRows()) {
      if (r.sev < 1 || r.key === "oome") continue;
      const k = kpi(kp, r.label, fmtInt(r.n), perText(r));
      k.classList.add("link"); k.tabIndex = 0; k.setAttribute("role", "button"); k.title = "List these lines below";
      const pick = () => { state.logCat = r.key; update(); const t = $("log-lines"); if (t) t.scrollIntoView({ block: "start" }); };
      k.addEventListener("click", pick);
      k.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
      if (r.sev >= 2) { k.classList.add("alert"); k.querySelector(".kpi-value").classList.add("bad"); }
    }
  };
  tabUpdaters.push(Object.assign(renderK, { tab: "logs" }));
  const lv = addPanel("logs", sec, { title: "Warnings and errors", height: 190, render: p => {
    drawTS(p, { kind: "stack", series: logLevelSeries(p), unit: "count", baseStep: levelStep(), note: "lines per {step}, all selected members", legendAlways: true, fmt: fmtCount, shade: false });
  } });
  lv.fixed = true;
  const tl = addPanel("logs", sec, { title: "When notable lines were logged", height: 120, noTable: true, render: p => {
    const rows = catRows();
    renderDotLanes(p, rows, { empty: "Nothing notable in this selection", what: "lines", selected: r => state.logCat === r.key, legend: sevLegend,
      pickHint: "Click to list them below",
      onPick: r => { state.logCat = state.logCat === r.key ? "all" : r.key; update(); } });
    p.note.textContent = rows.length ? "click a row to list its lines below" : "";
  } });
  tl.fixed = true;
  if (LOGS.membership.length) {
    const ms = addPanel("logs", sec, { title: "Cluster size, as each member logged it", height: 170, render: p => {
      const ss = selSet(), series = [];
      for (const [mi, rows] of LOGS.membership) {
        if (!ss.has(mi)) continue;
        series.push({ key: "m:" + mi, label: MEMBERS[mi].label, color: memberColor(mi), faint: mi >= 8, runs: stepRuns(rows.map(r => [r[0], r[1]]), logEnd(mi)) });
      }
      drawTS(p, { series, unit: "count", note: "from the 'Members {size:…}' lines", fmt: v => fmtInt(v) + " member" + (v === 1 ? "" : "s"), shade: false });
    } });
    ms.fixed = true;
  }
  // the lines themselves
  const card = el("section", "card", sec), cap = el("div", "cap", card);
  card.id = "log-lines";
  el("h3", null, cap, "Notable log lines");
  const note = el("span", "note", cap);
  const fl = el("div", "tabctl", card), chips = el("div", "seg", fl);
  chips.setAttribute("role", "group"); chips.setAttribute("aria-label", "Kind of line");
  const wrap = el("div", "table-wrap", card);
  const render = () => {
    clear(chips); clear(wrap);
    const ss = selSet(), q = findQ();
    const counts = new Map(catRows().map(r => [r.key, r.n]));
    if (state.logCat !== "all" && !counts.get(state.logCat)) state.logCat = "all";
    for (const [k, label] of [["all", "All"]].concat(LOGCATS.filter(c => counts.get(c[0])).map(c => [c[0], c[1]]))) {
      const b = el("button", null, chips, label + (k !== "all" ? " " + fmtInt(counts.get(k)) : ""));
      b.type = "button"; b.setAttribute("aria-pressed", String(state.logCat === k));
      b.addEventListener("click", () => { state.logCat = k; update(); });
    }
    const rows = LOGS.events.filter(e => ss.has(e[1]) && inView(e[0]) && (state.logCat === "all" || LOGCATS[e[2]][0] === state.logCat) &&
      (!q || e[5].toLowerCase().indexOf(q) >= 0 || e[4].toLowerCase().indexOf(q) >= 0 || MEMBERS[e[1]].label.toLowerCase().indexOf(q) >= 0 || LOGCATS[e[2]][1].toLowerCase().indexOf(q) >= 0));
    note.textContent = fmtInt(rows.length) + " line" + (rows.length === 1 ? "" : "s") + " in this range" + (EV_CAPPED ? CAPPED_NOTE : "");
    sortableTable(wrap, [
      { key: "t0", label: "Time", num: true, val: e => e[0], fmt: e => shortTime(e[0]) },
      { key: "m", label: "Member", val: e => MEMBERS[e[1]].label },
      { key: "c", label: "What", val: e => LOGCATS[e[2]][1] },
      { key: "lv", label: "Level", val: e => LVL_NAME[e[3]] || e[3], lv: e => e[3] },
      { key: "lg", label: "Logger", val: e => e[4] },
      { key: "x", label: "Message", val: e => e[5], cls: "msg" }
    ], rows, SORTS.ev, render, null, "No notable line in this selection.");
  };
  tabUpdaters.push(Object.assign(render, { tab: "logs" }));
  // files
  const det = el("details", "card sect", sec);
  el("summary", null, det, "Log files: " + fmtInt(LOGS.files.length));
  const ul = el("ul", "notes", det);
  for (const r of LOGS.info.slice().sort((a, b) => a[0] - b[0])) {
    const li = el("li", null, ul);
    el("b", null, li, MEMBERS[r[0]].label);
    li.appendChild(document.createTextNode(" · " + (r[1] ? "Hazelcast " + r[1] : "version not logged") + (r[2] ? ", cluster " + r[2] : "") + " · " + fmtInt(r[3]) + " records" + (r[4] != null ? " · " + span(r[4], r[5]) : "") + " · " +
      LOGS.files.filter(f => f[0] === r[0]).map(f => f[1] + " (" + fmtBytes(f[2]) + ")").join(", ")));
  }
}
// Health monitor
const HKEYS = HASLOGS ? LOGS.health.keys : [];
const HK = new Map(HKEYS.map((k, i) => [k[0], i]));
const HJOIN = new Map();
function hJoin(mi, times) {
  if (!HJOIN.has(mi)) { const d = []; for (let i = 1; i < times.length; i++) d.push(times[i] - times[i - 1]); d.sort((a, b) => a - b); HJOIN.set(mi, Math.max(60000, 3 * (d[d.length >> 1] || 30000))); }
  return HJOIN.get(mi);
}
// one line per member: the sum of these HealthMonitor values at each of its lines
function healthSeries(keys, scale) {
  const ss = selSet(), out = [];
  for (const [mi, times, cols] of LOGS.health.rows) {
    if (!ss.has(mi)) continue;
    const arrs = keys.filter(k => HK.has(k) && cols[HK.get(k)]).map(k => cols[HK.get(k)]);
    if (!arrs.length) continue;
    const pts = [];
    for (let i = 0; i < times.length; i++) {
      let v = 0, any = false;
      for (const a of arrs) { const x = a[i]; if (x != null) { v += x; any = true; } }
      if (any) pts.push([times[i], v * (scale || 1)]);
    }
    if (pts.length) out.push({ key: "m:" + mi, label: MEMBERS[mi].label, color: memberColor(mi), faint: mi >= 8, pts, join: hJoin(mi, times), dots: pts.length < 60 });
  }
  return out;
}
const HEALTH_CHARTS = [
  ["Heap used, % of max heap", ["heap.memory.used/max"], "percent"], ["Heap used", ["heap.memory.used"], "bytes"],
  ["Process CPU", ["load.process"], "percent"], ["System CPU", ["load.system"], "percent"], ["System load average", ["load.systemAverage"], "count"],
  ["Time in GC, % of wall time", ["minor.gc.time", "major.gc.time", "unknown.gc.time"], "percent", 0.1],
  ["Major GC time, % of wall time", ["major.gc.time"], "percent", 0.1],
  ["Garbage collections per minute", ["minor.gc.count", "major.gc.count", "unknown.gc.count"], "count", 60],
  ["Operations completed per second", ["operations.completed.count"], "count"], ["Running operations", ["operations.running.count"], "count"],
  ["Pending invocations", ["operations.pending.invocations.count"], "count"], ["Invocation slots in use", ["operations.pending.invocations.percentage"], "percent"],
  ["Operation queue", ["executor.q.operations.size"], "count"], ["Priority operation queue", ["executor.q.priorityOperation.size"], "count"],
  ["Response queue", ["executor.q.response.size"], "count"], ["Event queue", ["event.q.size"], "count"], ["Threads", ["thread.count"], "count"],
  ["Active connections", ["connection.active.count"], "count"], ["Client connections", ["client.connection.count"], "count"],
  ["Client endpoints", ["clientEndpoint.count"], "count"], ["Cluster time difference", ["cluster.timeDiff"], "ms"],
  ["Free physical memory", ["physical.memory.free"], "bytes"], ["Free swap", ["swap.space.free"], "bytes"],
  ["Native memory used", ["native.memory.used"], "bytes"], ["Native memory used, % of max", ["native.memory.used/max"], "percent"]
];
function healthChanges(key) {
  if (!HK.has(key)) return false;
  let first;
  for (const row of LOGS.health.rows) for (const x of (row[2][HK.get(key)] || [])) { if (x == null) continue; if (first === undefined) first = x; else if (x !== first) return true; }
  return false;
}
function buildLogsHealth(sec) {
  logBanner(sec, "HealthMonitor");
  if (!LOGS.health.rows.length) {
    el("p", "lead", sec, "No HealthMonitor line in these logs: hazelcast.health.monitoring.level is OFF, or SILENT and memory and CPU stayed under its thresholds.");
    return;
  }
  const n = LOGS.health.rows.reduce((a, r) => a + r[1].length, 0);
  el("p", "lead", sec, fmtInt(n) + " HealthMonitor lines (com.hazelcast.internal.diagnostics.HealthMonitor), one point each. Counts that only grow (GC, operations completed) are shown as a rate between two lines of the same member; a restart is skipped. In SILENT mode the monitor only logs while memory or CPU is above its threshold, so gaps are normal there.");
  const used = new Set();
  const add = (title, keys, unit, scale) => {
    keys = keys.filter(k => HK.has(k));
    if (!keys.length || !keys.some(healthChanges)) return;
    keys.forEach(k => used.add(k));
    const rate = keys.some(k => HKEYS[HK.get(k)][2] === "c");
    addPanel("logs-health", sec, { title, code: keys.join(" + "), height: 180, render: pp => {
      drawTS(pp, { series: healthSeries(keys, scale), unit, rate: unit === "count" && rate && !scale ? "/s" : "", shade: false,
        note: rate ? "between two HealthMonitor lines" : "at each HealthMonitor line", fmt: scale === 60 ? (v => sig3(v) + " /min") : null });
    } });
  };
  for (const c of HEALTH_CHARTS) add(c[0], c[1], c[2], c[3]);
  const rest = HKEYS.map(k => k[0]).filter(k => !used.has(k));
  const moving = rest.filter(healthChanges), flat = rest.filter(k => !healthChanges(k));
  if (moving.length) {
    el("h2", null, sec, "Other HealthMonitor values");
    for (const k of moving) { const u = HKEYS[HK.get(k)][1]; add(k, [k], u === "percent" || u === "bytes" || u === "ms" ? u : "count"); }
  }
  if (flat.length) {
    const det = el("details", "card sect", sec);
    el("summary", null, det, "Unchanged: " + flat.length + " HealthMonitor value" + (flat.length === 1 ? "" : "s") + " that never changed");
    el("p", "list-inline", det).textContent = flat.map(k => {
      for (const row of LOGS.health.rows) { const a = row[2][HK.get(k)]; const x = a && a.find(v => v != null); if (x != null) return k + " = " + fmtVal(x, HKEYS[HK.get(k)][1], ""); }
      return k;
    }).join(" · ");
  }
}
// Exceptions: one row per exception class and root cause
function excInView(g) { const ss = selSet(); let n = 0; for (const [mi, bins] of g.ms) if (ss.has(mi)) n += binsInView(bins); return n; }
const excLabel = g => shortCls(g.c) + (g.r ? " ← " + shortCls(g.r) : "");
const excTitle = g => g.c + (g.r ? ", caused by " + g.r : "");
const excColor = g => g.oome || g.lv === "E" ? stuckColor() : g.lv === "W" ? warnColor() : grey();
function excLegend(host) {
  for (const [c, label] of [[stuckColor(), "logged as ERROR, or OutOfMemoryError"], [warnColor(), "WARN"], [grey(), "INFO or lower"]]) {
    const it = el("span", "lg lg-static", host), s = el("span", "sw", it);
    s.style.background = c; s.style.borderRadius = "50%";
    el("span", null, it, label);
  }
  el("span", "lg-note", host, "dot size: how many");
}
function buildLogsExceptions(sec) {
  logBanner(sec, "exceptions");
  if (!LOGX.length) { el("p", "lead", sec, "No exception in these logs."); return; }
  el("p", "lead", sec, "Every line that names an exception, in its message or its stack trace, grouped by exception class and root cause (the last 'Caused by'): one row each, however many times it was logged. OutOfMemoryError is listed on its own first.");
  const kp = el("div", "kpis", sec);
  const oomeCard = el("section", "card alert", sec);
  const renderK = () => {
    clear(kp); clear(oomeCard);
    const ss = selSet();
    let n = 0, kinds = 0;
    const mem = new Set();
    for (const g of LOGX) { let k = 0; for (const [mi, bins] of g.ms) if (ss.has(mi)) { const x = binsInView(bins); k += x; if (x) mem.add(mi); } n += k; if (k) kinds++; }
    kpi(kp, "Exception kinds", fmtInt(kinds), "class and root cause, in view");
    kpi(kp, "Occurrences", fmtInt(n), "lines that name an exception");
    kpi(kp, "Members", fmtInt(mem.size), "with an exception in view");
    const oo = LOG_OOME.filter(o => ss.has(o[1]) && inView(o[0]));
    const k = kpi(kp, "Out of memory errors", fmtInt(oo.length), oo.length ? "listed below" : "no OutOfMemoryError");
    if (oo.length) { k.classList.add("alert"); k.querySelector(".kpi-value").classList.add("bad"); }
    oomeCard.hidden = !oo.length;
    if (!oo.length) return;
    const cap = el("div", "cap", oomeCard);
    el("h3", null, cap, "OutOfMemoryError");
    el("span", "note", cap, fmtInt(oo.length) + " in view · a member that ran out of memory should be restarted: what it was doing when the error hit may be half done");
    const w = el("div", "table-wrap", oomeCard);
    sortableTable(w, [
      { key: "t0", label: "Time", num: true, val: o => o[0], fmt: o => shortTime(o[0]) },
      { key: "m", label: "Member", val: o => MEMBERS[o[1]].label },
      { key: "r", label: "Reason", val: o => o[2] || "(none given)", cls: "lvl-E" },
      { key: "lg", label: "Logger", val: o => shortCls(o[3]), title: o => o[3] },
      { key: "x", label: "Line", val: o => o[4], cls: "msg" }
    ], oo, SORTS.oome, renderK, null, "");
  };
  tabUpdaters.push(Object.assign(renderK, { tab: "logs-exceptions" }));
  const tl = addPanel("logs-exceptions", sec, { title: "When exceptions were logged", height: 120, noTable: true, render: p => {
    const ss = selSet(), LIMIT = 30;
    const groups = LOGX.map((g, i) => ({ g, i, n: excInView(g) })).filter(x => x.n > 0).sort((a, b) => (b.g.oome - a.g.oome) || b.n - a.n);
    const rows = groups.slice(0, LIMIT).map(x => ({ key: x.i, label: excLabel(x.g), title: excTitle(x.g), color: excColor(x.g),
      bins: [].concat(...x.g.ms.filter(m => ss.has(m[0])).map(m => m[1])) }));
    renderDotLanes(p, rows, { empty: "No exception in this selection", what: "occurrences", legend: excLegend, selected: r => state.excSel === r.key,
      pickHint: "Click for its messages and a stack trace",
      onPick: r => { state.excSel = state.excSel === r.key ? null : r.key; update(); } });
    p.note.textContent = groups.length > LIMIT ? "the " + LIMIT + " most frequent of " + groups.length + " kinds" : groups.length ? "click a row for its messages and a stack trace" : "";
  } });
  tl.fixed = true;
  const card = el("section", "card", sec), cap = el("div", "cap", card);
  el("h3", null, cap, "Exceptions");
  const note = el("span", "note", cap);
  const wrap = el("div", "table-wrap", card);
  const detail = el("div", null, sec);
  const render = () => {
    clear(wrap); clear(detail);
    const q = findQ(), ss = selSet();
    const rows = LOGX.map((g, i) => ({ g, i, n: excInView(g) })).filter(x => x.n > 0 &&
      (!q || x.g.c.toLowerCase().indexOf(q) >= 0 || (x.g.r || "").toLowerCase().indexOf(q) >= 0 || x.g.lg.join(" ").toLowerCase().indexOf(q) >= 0 || x.g.msg.some(m => m[0].toLowerCase().indexOf(q) >= 0)));
    note.textContent = fmtInt(rows.length) + " kind" + (rows.length === 1 ? "" : "s") + " in this range · click one for its messages and a stack trace";
    sortableTable(wrap, [
      { key: "c", label: "Exception", val: x => excLabel(x.g), title: x => excTitle(x.g), cls: "op" },
      { key: "lv", label: "Level", val: x => LVL_NAME[x.g.lv] || x.g.lv, lv: x => x.g.lv },
      { key: "n", label: "Count", num: true, val: x => x.n, fmt: x => fmtInt(x.n) },
      { key: "m", label: "Members", val: x => x.g.ms.filter(m => ss.has(m[0]) && binsInView(m[1])).map(m => MEMBERS[m[0]].label).join(", "), cls: "wrapcell" },
      { key: "f", label: "First", num: true, val: x => x.g.f, fmt: x => shortTime(x.g.f) },
      { key: "l", label: "Last", num: true, val: x => x.g.l, fmt: x => shortTime(x.g.l) },
      { key: "lg", label: "Logged by", val: x => x.g.lg.join(", "), cls: "wrapcell" },
      { key: "x", label: "Message", val: x => (x.g.msg[0] || [""])[0], cls: "msg" }
    ], rows, state.excSort, render, x => { state.excSel = state.excSel === x.i ? null : x.i; update(); }, "No exception in this selection.", x => x.i === state.excSel);
    const sel = state.excSel != null ? LOGX[state.excSel] : null;
    if (!sel) return;
    el("h2", null, detail, excLabel(sel));
    el("p", "lead", detail, excTitle(sel) + " · " + fmtInt(sel.n) + " in all, " + span(sel.f, sel.l) + " · " +
      sel.ms.map(m => MEMBERS[m[0]].label + " " + fmtInt(m[1].reduce((a, b) => a + b[1], 0))).join(", "));
    const c2 = el("section", "card", detail);
    const cap2 = el("div", "cap", c2);
    el("h3", null, cap2, "Messages");
    el("span", "note", cap2, "numbers in the messages are ignored to group them; the most frequent " + sel.msg.length);
    const w2 = el("div", "table-wrap", c2);
    sortableTable(w2, [{ key: "x", label: "Message (as first seen)", val: m => m[0] || "(no message)", cls: "msg" }, { key: "n", label: "Count", num: true, val: m => m[1], fmt: m => fmtInt(m[1]) }],
      sel.msg, SORTS.msg, render, null, "No message.");
    if (sel.s) {
      const c3 = el("section", "card", detail);
      const cap3 = el("div", "cap", c3);
      el("h3", null, cap3, "The first one");
      el("span", "note", cap3, logTime(sel.s[0]) + " · " + MEMBERS[sel.s[1]].label + " · " + sel.s[2]);
      el("pre", "sample", c3, sel.s[3]);
    }
  };
  tabUpdaters.push(Object.assign(render, { tab: "logs-exceptions" }));
}
// CP Subsystem
const CP_KIND_NAME = { leader: "became leader", demote: "stepped down", election: "election started", prevote: "pre-vote", noleader: "no leader", setleader: "leader known",
  newterm: "new term", vote: "vote", status: "status", group: "group", session: "session created", sessionend: "session ended", sessionother: "session",
  restore: "persistence", members: "CP members",
  rebalance: "leadership rebalance", snapshot: "group members", problem: "problem", other: "other" };
const CP_CHIPS = [["all", "All"], ["elect", "Elections"], ["lead", "Leaders"], ["group", "Groups"], ["session", "Sessions"],
  ["restore", "Persistence"], ["members", "CP members"], ["problem", "Warnings and errors"], ["other", "Other"]];
const cpChipOf = e => e[5] === "W" || e[5] === "E" || e[3] === "problem" ? "problem" :
  ({ prevote: "elect", election: "elect", vote: "elect", newterm: "elect", noleader: "elect", leader: "lead", setleader: "lead", snapshot: "lead", demote: "lead",
    group: "group", status: "group", session: "session", sessionend: "session", sessionother: "session", restore: "restore", members: "members",
    rebalance: "members" })[e[3]] || "other";
// how many CP lines of a kind in view on the selected members (all of them, even when the table keeps a sample)
function cpCount(kind) { const ss = selSet(); let n = 0; for (const [k, mi, , bins] of (LOGS.cpbins || [])) if (k === kind && ss.has(mi)) n += binsInView(bins); return n; }
const ROLE_COLOR = { LEADER: () => cssVar("--accent", "#2a78d6"), CANDIDATE: () => warnColor(), FOLLOWER: () => grey() };
const roleColor = role => (ROLE_COLOR[role] || grey)();
function buildLogsCp(sec) {
  logBanner(sec, "CP Subsystem");
  if (!LOGCP.length) { el("p", "lead", sec, "No CP Subsystem line in these logs: the CP Subsystem is not enabled (cp-member-count 0), or its loggers are filtered out."); return; }
  el("p", "lead", sec, "What the CP Subsystem logged: its Raft groups and each member's role in them (from the 'CP Group Members' lines and the election lines), terms, elections, sessions and persistence. Each member logs its own view of each group.");
  const kp = el("div", "kpis", sec);
  const cpView = () => { const ss = selSet(); return LOGCP.filter(e => ss.has(e[1]) && inView(e[0])); };
  const renderK = () => {
    clear(kp);
    const rows = cpView(), ss = selSet();
    const groups = new Set(rows.filter(e => e[2]).map(e => e[2]));
    kpi(kp, "CP groups", fmtInt(groups.size), Array.from(groups).sort().join(", ") || "none in view");
    const won = rows.filter(e => e[3] === "leader");
    kpi(kp, "Leader elections won", fmtInt(cpCount("leader")), won.length ? "last: " + won[won.length - 1][2] + " by " + MEMBERS[won[won.length - 1][1]].label + " at " + shortTime(won[won.length - 1][0]) : "no 'We are the LEADER!' line");
    const leaders = new Map();
    for (const [mi, g, rr] of LOGS.roles) if (ss.has(mi)) for (const r of rr) if (r[0] <= state.x1 && r[3]) { const q = leaders.get(g); if (!q || r[0] >= q[0]) leaders.set(g, [r[0], r[3], r[2]]); }
    kpi(kp, "Leaders, last seen", fmtInt(leaders.size) + " group" + (leaders.size === 1 ? "" : "s"), Array.from(leaders.entries()).sort().map(x => x[0] + ": " + x[1][1] + (x[1][2] != null ? " (term " + x[1][2] + ")" : "")).join(" · ") || "no leader logged");
    const ended = cpCount("sessionend");
    kpi(kp, "Sessions created", fmtInt(cpCount("session")), "CP sessions, in view" + (ended ? " · " + fmtInt(ended) + " closed or expired" : ""));
    const bad = rows.filter(e => e[5] === "W" || e[5] === "E");
    const k = kpi(kp, "CP warnings and errors", fmtInt(bad.length), bad.length ? "last at " + shortTime(bad[bad.length - 1][0]) + ": " + trimText(bad[bad.length - 1][6], 70) : "none");
    if (bad.some(e => e[5] === "E")) k.querySelector(".kpi-value").classList.add("bad");
    const took = rows.filter(e => e[3] === "restore" && /Took\(ms\)/.test(e[6])).map(e => +(/Took\(ms\): (\d+)/.exec(e[6]) || [0, 0])[1]);
    if (took.length) kpi(kp, "Persistence restores", fmtInt(took.length), "longest took " + fmtMs(Math.max(...took)));
  };
  tabUpdaters.push(Object.assign(renderK, { tab: "logs-cp" }));
  if (LOGS.roles.length) {
    const lanes = addPanel("logs-cp", sec, { title: "Raft roles, as each member logged them", height: 140, noTable: true, render: renderCpRoles });
    lanes.fixed = true;
  }
  const terms = !LOGCP.some(e => e[4] != null && e[2]) ? null : addPanel("logs-cp", sec, { title: "Raft term", height: 170, render: p => {
    const ss = selSet(), by = new Map();
    for (const e of LOGCP) { if (!ss.has(e[1]) || e[4] == null || !e[2]) continue; if (!by.has(e[2])) by.set(e[2], []); by.get(e[2]).push([e[0], e[4]]); }
    const sp = logSpan();
    const series = Array.from(by.keys()).sort().map((g, i) => {
      let hi = -1;
      const pts = by.get(g).sort((a, b) => a[0] - b[0]).filter(x => { if (x[1] < hi) return false; hi = x[1]; return true; });
      return { key: "g:" + g, label: g, color: catColor(i), faint: i >= 8, runs: stepRuns(pts, sp ? sp[1] : null) };
    });
    drawTS(p, { series, unit: "count", note: "the highest term the selected members logged, per group", fmt: v => "term " + fmtInt(v), shade: false, legendAlways: true });
  } });
  if (terms) terms.fixed = true;
  const card = el("section", "card", sec), cap = el("div", "cap", card);
  el("h3", null, cap, "CP Subsystem lines");
  const note = el("span", "note", cap);
  const fl = el("div", "tabctl", card), chips = el("div", "seg", fl);
  chips.setAttribute("role", "group"); chips.setAttribute("aria-label", "Kind of line");
  const wrap = el("div", "table-wrap", card);
  const render = () => {
    clear(chips); clear(wrap);
    const q = findQ(), base = cpView();
    const counts = new Map();
    for (const e of base) counts.set(cpChipOf(e), (counts.get(cpChipOf(e)) || 0) + 1);
    if (state.cpKind !== "all" && !counts.get(state.cpKind)) state.cpKind = "all";
    for (const [k, label] of CP_CHIPS) {
      if (k !== "all" && !counts.get(k)) continue;
      const b = el("button", null, chips, label + (k !== "all" ? " " + fmtInt(counts.get(k)) : ""));
      b.type = "button"; b.setAttribute("aria-pressed", String(state.cpKind === k));
      b.addEventListener("click", () => { state.cpKind = k; render(); });
    }
    const rows = base.filter(e => (state.cpKind === "all" || cpChipOf(e) === state.cpKind) &&
      (!q || e[6].toLowerCase().indexOf(q) >= 0 || (e[2] || "").toLowerCase().indexOf(q) >= 0 || MEMBERS[e[1]].label.toLowerCase().indexOf(q) >= 0));
    note.textContent = fmtInt(rows.length) + " line" + (rows.length === 1 ? "" : "s") + " in this range" + (CP_CAPPED ? CAPPED_NOTE : "");
    sortableTable(wrap, [
      { key: "t0", label: "Time", num: true, val: e => e[0], fmt: e => shortTime(e[0]) },
      { key: "m", label: "Member", val: e => MEMBERS[e[1]].label },
      { key: "g", label: "Group", val: e => e[2] || "–" },
      { key: "k", label: "What", val: e => CP_KIND_NAME[e[3]] || e[3], lv: e => e[5] },
      { key: "tm", label: "Term", num: true, val: e => e[4] == null ? -1 : e[4], fmt: e => e[4] == null ? "" : fmtInt(e[4]) },
      { key: "x", label: "Line", val: e => e[6], cls: "msg" }
    ], rows, SORTS.cp, render, null, "No CP line in this selection.");
  };
  tabUpdaters.push(Object.assign(render, { tab: "logs-cp" }));
}
// one row per group and member: leader, follower or candidate, from that member's own log
function renderCpRoles(p) {
  const ss = selSet(), groups = new Map();
  for (const [mi, g, rows] of LOGS.roles) { if (!ss.has(mi) || !g) continue; if (!groups.has(g)) groups.set(g, []); groups.get(g).push([mi, rows]); }
  const lines = [];
  for (const g of Array.from(groups.keys()).sort()) {
    lines.push({ head: g });
    for (const [mi, rows] of groups.get(g).sort((a, b) => a[0] - b[0])) lines.push({ g, mi, rows });
  }
  const W = p.width, M = { l: W < 640 ? 130 : Math.min(300, Math.max(220, Math.round(W * 0.22))), r: 16, t: 8, b: 36 }, rowH = 22;
  const H = M.t + M.b + Math.max(1, lines.length) * rowH + (lines.length ? 0 : 40);
  p.host.style.minHeight = H + "px";
  frame(p, H, M);
  drawMarkers(p, false);
  const segs = [];
  lines.forEach((ln, ri) => {
    const cy = M.t + ri * rowH + rowH / 2;
    if (ln.head) { svgEl("text", { x: 8, y: cy + 4, class: "row-head" }, p.g.axes).textContent = trimText(ln.head, Math.floor((M.l - 14) / 7)); return; }
    svgEl("text", { x: M.l - 10, y: cy + 4, class: "row-label", "text-anchor": "end" }, p.g.axes).textContent = trimText(MEMBERS[ln.mi].label, Math.floor((M.l - 14) / 6.4));
    const end = logEnd(ln.mi), rows = ln.rows;
    let labelled = null;
    for (let i = 0; i < rows.length;) {
      let j = i;     // the same role, term and leader logged again: one bar
      while (j + 1 < rows.length && rows[j + 1][1] === rows[i][1] && rows[j + 1][2] === rows[i][2] && rows[j + 1][3] === rows[i][3]) j++;
      const r = rows[i], a = r[0], b = Math.max(a, j + 1 < rows.length ? rows[j + 1][0] : end);
      i = j + 1;
      if (b < state.x0 || a > state.x1) continue;
      const A = p.sx(Math.max(a, state.x0)), w = Math.max(3, p.sx(Math.min(b, state.x1)) - A);
      const rect = svgEl("rect", { x: A.toFixed(1), y: (cy - 6).toFixed(1), width: w.toFixed(1), height: 12, rx: 2 }, p.g.marks);
      rect.style.fill = roleColor(r[1]);
      if (r[1] === "FOLLOWER") rect.style.opacity = "0.6";
      if (r[2] != null && r[2] !== labelled && w > 24) { svgEl("text", { x: A + 4, y: cy + 3.5, class: "bar-label" }, p.g.marks).textContent = "t" + r[2]; labelled = r[2]; }
      segs.push({ ri, A, B: A + w, r, a, b, g: ln.g, mi: ln.mi });
    }
  });
  if (!lines.length) emptyNote(p, "No Raft group in this selection");
  clear(p.legend);
  p.legend.hidden = !lines.length;
  for (const role of ["LEADER", "FOLLOWER", "CANDIDATE"]) {
    const it = el("span", "lg lg-static", p.legend), s = el("span", "sw", it);
    s.style.background = roleColor(role);
    if (role === "FOLLOWER") s.style.opacity = "0.6";
    el("span", null, it, role.toLowerCase());
  }
  el("span", "lg-note", p.legend, "t4: Raft term 4 · a role holds until the member logs another one");
  oomeLegend(p.legend);
  p.tipAt = (t, py, px) => {
    const ri = Math.floor((py - M.t) / rowH);
    const s = segs.find(x => x.ri === ri && px >= x.A - 3 && px <= x.B + 3);
    if (!s) return null;
    clear(p.hot);
    svgEl("rect", { x: s.A - 2, y: M.t + ri * rowH + rowH / 2 - 8, width: s.B - s.A + 4, height: 16, rx: 3, class: "bar-hot" }, p.hot);
    return box => {
      el("div", "tip-t", box, s.g + " · " + MEMBERS[s.mi].label);
      el("div", "tip-sub", box, span(s.a, s.b));
      tipRow(box, roleColor(s.r[1]), s.r[1].toLowerCase(), s.r[2] != null ? "term " + s.r[2] : "", false);
      if (s.r[3]) el("div", "tip-foot", box, "Leader: " + s.r[3]);
    };
  };
  finish(p);
}

/* ---------- Transfer: bytes each member received and sent, per peer (member, client, WAN target, other cluster) */
const TR = D.transfer || null;
const TR_PEERS = TR ? TR.peers : [];
const TR_LINKS = TR ? TR.links.map((x, i) => ({ i, m: x[0], p: x[1], peer: TR_PEERS[x[1]], step: x[2], nconn: x[3], rows: x[4] })) : [];
const TR_TOT = new Map(TR ? TR.totals.map(x => [x[0], { step: x[1], rows: x[2] }]) : []);
const TR_KINDS = [["member", "Members", 0], ["client", "Clients", 2], ["wan", "WAN, other clusters", 6], ["unknown", "Unknown", 3],
  ["unseen", "Not seen as a connection", -1]];
const TR_KNAME = Object.fromEntries(TR_KINDS.map(k => [k[0], k[1]]));
const trKindColor = k => { const e = TR_KINDS.find(x => x[0] === k); return e && e[2] >= 0 ? catColor(e[2]) : grey(); };
const trPeerName = peer => (peer[0] === "client" ? "client " : peer[0] === "unknown" ? "" : "") + peer[1];
const trLinkName = L => MEMBERS[L.m].label + " ↔ " + trPeerName(L.peer);
function rowsFrom(rows, t) { let lo = 0, hi = rows.length; while (lo < hi) { const m = (lo + hi) >> 1; if (rows[m][0] < t) lo = m + 1; else hi = m; } return lo; }
// bytes in [a, b] of sparse bins [[t, in, out]...] of `step` ms (a bin that sticks out counts for the part inside)
function rowsSum(rows, step, a, b, col) {
  let s = 0;
  for (let k = rowsFrom(rows, a - step); k < rows.length; k++) {
    const t = rows[k][0];
    if (t >= b) break;
    const ov = Math.min(b, t + step) - Math.max(a, t);
    if (ov > 0) s += rows[k][col] * Math.min(1, ov / step);
  }
  return s;
}
const linkAmt = (L, a, b) => [rowsSum(L.rows, L.step, a, b, 1), rowsSum(L.rows, L.step, a, b, 2)];
function totAmt(mi, a, b) {
  const T = TR_TOT.get(mi);
  if (T) return [rowsSum(T.rows, T.step, a, b, 1), rowsSum(T.rows, T.step, a, b, 2)];
  let i = 0, o = 0;
  for (const L of TR_LINKS) if (L.m === mi) { const x = linkAmt(L, a, b); i += x[0]; o += x[1]; }
  return [i, o];
}
const TR_RANK = new Map(TR_LINKS.map(L => [L.i, linkAmt(L, X_MIN - 1, X_MAX + 1)]).sort((x, y) => (y[1][0] + y[1][1]) - (x[1][0] + x[1][1])).map((x, r) => [x[0], r]));
const trSpan = () => state.msel || [state.x0, state.x1];
// what a member's IO threads moved that none of its connection counters saw (connections opened and closed between two
// dumps): the bytes of the IO threads minus those of the connections, added up over time, as a running maximum, so that a
// counter read a dump later than the other one doesn't count
const TR_UNSEEN = new Map();
function trUnseenCurve(mi) {
  if (TR_UNSEEN.has(mi)) return TR_UNSEEN.get(mi);
  const T = TR_TOT.get(mi);
  let res = null;
  if (T) {
    const ev = new Map();
    const put = (t, i, o) => { const e = ev.get(t); if (e) { e[0] += i; e[1] += o; } else ev.set(t, [i, o]); };
    for (const r of T.rows) put(r[0] + T.step, r[1], r[2]);
    for (const L of TR_LINKS) if (L.m === mi) for (const r of L.rows) put(r[0] + L.step, -r[1], -r[2]);
    const ts = Array.from(ev.keys()).sort((a, b) => a - b), mi_ = new Float64Array(ts.length), mo = new Float64Array(ts.length);
    let di = 0, dO = 0, xi = 0, xo = 0;
    ts.forEach((t, k) => { const e = ev.get(t); di += e[0]; dO += e[1]; if (di > xi) xi = di; if (dO > xo) xo = dO; mi_[k] = xi; mo[k] = xo; });
    res = { ts, v: [mi_, mo] };
  }
  TR_UNSEEN.set(mi, res);
  return res;
}
function trUnseenAt(c, t, col) {
  let lo = 0, hi = c.ts.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (c.ts[m] <= t) lo = m + 1; else hi = m; }
  return lo ? c.v[col][lo - 1] : 0;
}
const trUnseen = (mi, a, b, col) => { const c = trUnseenCurve(mi); return c ? Math.max(0, trUnseenAt(c, b, col) - trUnseenAt(c, a, col)) : 0; };
function trFine(srcs) { let st = G.step || 60000; for (const S of srcs) if (S.step > st) st = S.step; return st; }
function trStep(p, fine) {
  const want = (state.x1 - state.x0) / Math.max(20, plotWidth(p) / 5);
  return Math.max(fine, TSTEPS.find(x => x >= want) || TSTEPS[TSTEPS.length - 1]);
}
// bins of `step` across the view, filled from sparse bins (a coarser one is spread over the bins it covers)
function trGrid(srcs, step) {
  const t0 = Math.floor(state.x0 / step) * step, n = Math.max(1, Math.ceil((state.x1 - t0) / step)), end = t0 + n * step;
  const vin = new Float64Array(n), vout = new Float64Array(n);
  for (const S of srcs) {
    for (let k = rowsFrom(S.rows, t0 - S.step); k < S.rows.length; k++) {
      const r = S.rows[k], t = r[0];
      if (t >= end) break;
      if (S.step <= step) {
        const i = Math.floor((t - t0) / step);
        if (i >= 0 && i < n) { vin[i] += r[1]; vout[i] += r[2]; }
        continue;
      }
      for (let u = Math.max(t, t0); u < Math.min(t + S.step, end);) {
        const i = Math.floor((u - t0) / step), e = Math.min(t + S.step, t0 + (i + 1) * step), f = (e - u) / S.step;
        vin[i] += r[1] * f; vout[i] += r[2] * f; u = e;
      }
    }
  }
  return { t0, n, vin, vout };
}
// how much of each bin a member's counters cover: from the first metrics dump of each lifetime (the baseline of the
// counters) to its last one. A rate is the bytes over that time, so a bin a member is up for part of isn't too low.
const TR_LIVES = new Map(TR && TR.lives ? TR.lives.map(x => [x[0], x[1]]) : []);
function trCover(mi, t0, step, n) {
  const cov = new Float64Array(n);
  for (const c of (TR_LIVES.get(mi) || MEMBERS[mi].cover)) {
    const i0 = Math.max(0, Math.floor((c[0] - t0) / step)), i1 = Math.min(n - 1, Math.floor((c[1] - t0) / step));
    for (let i = i0; i <= i1; i++) cov[i] += Math.max(0, Math.min(c[1], t0 + (i + 1) * step) - Math.max(c[0], t0 + i * step));
  }
  return cov;
}
function trSeries(key, label, color, g, col, step, cov, amount, extra) {
  const src = col === 0 ? g.vin : g.vout, v = new Float64Array(g.n), per = step / 1000;
  for (let i = 0; i < g.n; i++) v[i] = !cov ? src[i] / per : cov[i] > 0.02 * step ? src[i] / (cov[i] / 1000) : NaN;
  return Object.assign({ key, label, color, t0: g.t0, step, v, o: Math.round(g.t0 / step), f: 1, noAgg: true, amount }, extra || {});
}
const trFmt = step => v => fmtBytes(v) + "/s · " + fmtBytes(v * step / 1000) + " in " + stepName(step);
// the transfer of each selected member and peer in [a, b]: links, plus what the IO threads saw that no connection did
function trRows(a, b) {
  const ss = selSet(), rows = [], per = new Map();
  for (const L of TR_LINKS) {
    if (!ss.has(L.m)) continue;
    const [i, o] = linkAmt(L, a, b);
    if (!(i + o)) continue;
    rows.push({ L, m: L.m, kind: L.peer[0], peer: trPeerName(L.peer), note: L.peer[2] || "", in: i, out: o, nconn: L.nconn });
    const q = per.get(L.m) || [0, 0]; q[0] += i; q[1] += o; per.set(L.m, q);
  }
  for (const mi of ss) {
    if (!TR_TOT.has(mi) || !TR_LINKS.some(L => L.m === mi)) continue;     // no connection counters: nothing to compare
    const [ti, to] = totAmt(mi, a, b), ui = trUnseen(mi, a, b, 0), uo = trUnseen(mi, a, b, 1);
    if ((ui + uo) > 65536 && (ui + uo) > 0.005 * (ti + to))
      rows.push({ L: null, m: mi, kind: "unseen", peer: "not seen as a connection", note: "opened and closed between two dumps", in: ui, out: uo, nconn: 0 });
  }
  return rows;
}
function trKindSeries(dirCol, step, g, mis) {
  // per kind of peer: one grid for all selected members' links of that kind, plus the IO-thread rest
  const ss = new Set(mis), out = [];
  const totG = TR_TOT.size ? trGrid(mis.filter(mi => TR_TOT.has(mi)).map(mi => TR_TOT.get(mi)), step) : null;
  for (const [k, label] of TR_KINDS) {
    if (k === "unseen") {
      if (!totG) continue;
      const v = { t0: totG.t0, n: totG.n, vin: new Float64Array(totG.n), vout: new Float64Array(totG.n) };
      let any = 0, all = 0;
      for (let i = 0; i < totG.n; i++) {
        const a = totG.t0 + i * step, b = a + step;
        for (const mi of mis) if (ss.has(mi) && TR_LINKS.some(L => L.m === mi)) { v.vin[i] += trUnseen(mi, a, b, 0); v.vout[i] += trUnseen(mi, a, b, 1); }
        any += dirCol ? v.vout[i] : v.vin[i]; all += dirCol ? totG.vout[i] : totG.vin[i];
      }
      if (any > 65536 && any > 0.002 * all) out.push(trSeries("k:unseen", label, trKindColor(k), v, dirCol, step, null,
        (a, b) => mis.reduce((n, mi) => n + trUnseen(mi, a, b, dirCol), 0)));
      continue;
    }
    const ls = TR_LINKS.filter(L => ss.has(L.m) && L.peer[0] === k);
    if (!ls.length) continue;
    out.push(trSeries("k:" + k, label, trKindColor(k), trGrid(ls, step), dirCol, step, null,
      (a, b) => ls.reduce((s, L) => s + linkAmt(L, a, b)[dirCol], 0)));
  }
  return out;
}
function buildTransfer(sec) {
  if (!TR) {
    el("p", "lead", sec, "No network byte counters in these files: the per-connection counters (tcp.connection.in.bytesRead, tcp.connection.out.bytesWritten) and the IO thread counters (tcp.inputThread / outputThread.bytesTransceived) come from the MetricsPlugin.");
    return;
  }
  const perPeer = TR_LINKS.length > 0;
  if (!perPeer) el("p", "lead", sec, "How many bytes each member received and sent, from its IO threads (tcp.inputThread / outputThread.bytesTransceived). " +
    "These files have no per-connection counters (tcp.connection.in.bytesRead and out.bytesWritten), so who the bytes went to and came from can't be told.");
  else el("p", "lead", sec, "How many bytes each member received and sent, and with whom: the other members, each client host, WAN targets and other clusters. " +
    "Every connection has its own counters in the metrics (tcp.connection.in.bytesRead and out.bytesWritten, named by local and remote address); " +
    "a connection is matched to a member when the other member has the same connection going out, " + (TR.types ? "to a client by its type (tcp.connection.connectionType), " : "") +
    "and a member's connections to addresses that are not in the cluster go to another cluster (WAN). The member totals come from the IO threads, which also count the connections that opened and closed between two dumps.");
  const how = el("p", "lead", sec);
  const kp = el("div", "kpis", sec);
  const renderK = () => {
    clear(kp); clear(how);
    const [a, b] = trSpan();
    how.appendChild(document.createTextNode(state.msel ? "Amounts for the measured span: " : "Amounts for the time range in view: "));
    el("b", null, how, span(a, b) + " (" + dur((b - a) / 1000) + ")");
    how.appendChild(document.createTextNode(state.msel ? ". " : ". To get them for another span, pick Drag to: Measure in the toolbar (or hold Shift) and drag across a chart, or type the times. "));
    if (state.msel) { const c = el("button", "btn btn-s", how, "Clear the span"); c.type = "button"; c.addEventListener("click", clearMeasure); }
    const ss = selSet();
    let ti = 0, to = 0;
    for (const mi of ss) { const x = totAmt(mi, a, b); ti += x[0]; to += x[1]; }
    const rows = trRows(a, b);
    const k = kind => rows.filter(r => r.kind === kind);
    kpi(kp, "Received", fmtBytes(ti), ss.size > 1 ? "by the " + ss.size + " members" : MEMBERS[Array.from(ss)[0]].label);
    kpi(kp, "Sent", fmtBytes(to), ss.size > 1 ? "by the " + ss.size + " members" : MEMBERS[Array.from(ss)[0]].label);
    if (!perPeer) return;
    const mm = k("member").reduce((s, r) => s + r.in, 0);
    kpi(kp, "Between members", fmtBytes(mm), "received by members from other members");
    const cl = k("client"), hosts = new Set(cl.map(r => r.peer));
    kpi(kp, "Clients", fmtBytes(cl.reduce((s, r) => s + r.in + r.out, 0)), fmtInt(hosts.size) + " client host" + (hosts.size === 1 ? "" : "s") + ", in and out");
    const wn = k("wan");
    if (wn.length) kpi(kp, "WAN, other clusters", fmtBytes(wn.reduce((s, r) => s + r.in + r.out, 0)), fmtInt(new Set(wn.map(r => r.peer)).size) + " address" + (new Set(wn.map(r => r.peer)).size === 1 ? "" : "es") + ", in and out");
    const top = rows.filter(r => r.L).sort((x, y) => (y.in + y.out) - (x.in + x.out))[0];
    if (top) kpi(kp, "Busiest", fmtBytes(top.in + top.out), MEMBERS[top.m].label + " ↔ " + top.peer);
  };
  tabUpdaters.push(Object.assign(renderK, { tab: "transfer" }));
  // members x kind of peer
  const mcard = el("section", "card", sec), mcap = el("div", "cap", mcard);
  mcard.hidden = !perPeer;
  el("h3", null, mcap, "Each member, by kind of peer");
  el("span", "note", mcap, "received / sent");
  const mwrap = el("div", "table-wrap", mcard);
  const renderM = () => {
    clear(mwrap);
    const [a, b] = trSpan(), rows = trRows(a, b), ss = selSet();
    const kinds = TR_KINDS.filter(k => rows.some(r => r.kind === k[0]));
    if (!rows.length) { el("div", "empty-note", mwrap, "No transfer in this span."); return; }
    const t = el("table", "trm", mwrap), hr = el("tr", null, el("thead", null, t));
    el("th", null, hr, "Member");
    for (const k of kinds) { const th = el("th", "num", hr); const s = el("span", "sw", th); s.style.background = trKindColor(k[0]); th.appendChild(document.createTextNode(" " + k[1])); }
    el("th", "num", hr, "All");
    const tb = el("tbody", null, t);
    for (const mi of Array.from(ss).sort((x, y) => x - y)) {
      const mine = rows.filter(r => r.m === mi);
      if (!mine.length && !TR_TOT.has(mi)) continue;
      const tr = el("tr", null, tb);
      el("td", null, tr, MEMBERS[mi].label);
      for (const k of kinds) {
        const rs = mine.filter(r => r.kind === k[0]);
        el("td", "num", tr, rs.length ? fmtBytes(rs.reduce((s, r) => s + r.in, 0)) + " / " + fmtBytes(rs.reduce((s, r) => s + r.out, 0)) : "");
      }
      const tt = totAmt(mi, a, b);
      el("td", "num", tr, fmtBytes(tt[0]) + " / " + fmtBytes(tt[1]));
    }
  };
  tabUpdaters.push(Object.assign(renderM, { tab: "transfer" }));
  // charts
  const perMember = dirCol => p => {
    const mis = selMembers().filter(mi => TR_TOT.has(mi) || TR_LINKS.some(L => L.m === mi));
    const srcs = mis.map(mi => TR_TOT.get(mi) || { step: 0, rows: [] });
    const step = trStep(p, trFine(srcs.concat(TR_LINKS.filter(L => mis.indexOf(L.m) >= 0))));
    const series = mis.map(mi => {
      const g = TR_TOT.has(mi) ? trGrid([TR_TOT.get(mi)], step) : trGrid(TR_LINKS.filter(L => L.m === mi), step);
      return trSeries("m:" + mi, MEMBERS[mi].label, memberColor(mi), g, dirCol, step, trCover(mi, g.t0, step, g.n), (a, b) => totAmt(mi, a, b)[dirCol], { faint: mi >= 8 });
    });
    drawTS(p, { series, unit: "bytes", rate: "/s", shade: false, measure: "rate", fmt: trFmt(step), note: "per second, average per " + stepName(step) });
  };
  addPanel("transfer", sec, { title: "Received, per member", code: "tcp.inputThread.bytesTransceived", height: 190, render: perMember(0) }).fixed = true;
  addPanel("transfer", sec, { title: "Sent, per member", code: "tcp.outputThread.bytesTransceived", height: 190, render: perMember(1) }).fixed = true;
  const byKind = dirCol => p => {
    const mis = selMembers();
    const step = trStep(p, trFine(TR_LINKS.filter(L => mis.indexOf(L.m) >= 0).concat(mis.filter(mi => TR_TOT.has(mi)).map(mi => TR_TOT.get(mi)))));
    const series = trKindSeries(dirCol, step, null, mis);
    drawTS(p, { kind: "stack", series, unit: "bytes", rate: "/s", shade: false, measure: "rate", legendAlways: true, fmt: trFmt(step),
      note: "per second, average per " + stepName(step) + ", all selected members" });
  };
  if (TR_LINKS.length) {
    addPanel("transfer", sec, { title: "Received, by kind of peer", code: "tcp.connection.in.bytesRead", height: 190, render: byKind(0) }).fixed = true;
    addPanel("transfer", sec, { title: "Sent, by kind of peer", code: "tcp.connection.out.bytesWritten", height: 190, render: byKind(1) }).fixed = true;
    const busiest = dirCol => p => {
      const ss = selSet(), [a, b] = [state.x0, state.x1];
      const cand = TR_LINKS.filter(L => ss.has(L.m)).map(L => [L, linkAmt(L, a, b)[dirCol]]).filter(x => x[1] > 0).sort((x, y) => y[1] - x[1]);
      const pick = cand.slice(0, 8).map(x => x[0]);
      const sel = state.trSel != null ? TR_LINKS[state.trSel] : null;
      if (sel && ss.has(sel.m) && pick.indexOf(sel) < 0) pick.push(sel);
      const step = trStep(p, trFine(pick));
      const series = pick.map(L => {
        const g = trGrid([L], step), r = TR_RANK.get(L.i);
        return trSeries("l:" + L.i, trLinkName(L), catColor(r < 8 ? r : 99), g, dirCol, step, trCover(L.m, g.t0, step, g.n), (x, y) => linkAmt(L, x, y)[dirCol],
          { sel: sel === L, faint: r >= 8 && sel !== L });
      });
      drawTS(p, { series, unit: "bytes", rate: "/s", shade: false, measure: "rate", fmt: trFmt(step), noDirect: true, legendAlways: true,
        note: (cand.length > 8 ? "the 8 busiest of " + cand.length : cand.length + " member and peer pair" + (cand.length === 1 ? "" : "s")) + " in view · per second, average per " + stepName(step),
        empty: "No transfer in this selection" });
    };
    addPanel("transfer", sec, { title: "Busiest pairs, received by the member", height: 200, render: busiest(0) }).fixed = true;
    addPanel("transfer", sec, { title: "Busiest pairs, sent by the member", height: 200, render: busiest(1) }).fixed = true;
  }
  if (!perPeer) return;
  // every member and peer
  const card = el("section", "card", sec), cap = el("div", "cap", card);
  el("h3", null, cap, "Transfer per member and peer");
  const note = el("span", "note", cap);
  const fl = el("div", "tabctl", card), chips = el("div", "seg", fl);
  chips.setAttribute("role", "group"); chips.setAttribute("aria-label", "Kind of peer");
  const wrap = el("div", "table-wrap", card);
  const detail = el("div", null, sec);
  const render = () => {
    clear(chips); clear(wrap); clear(detail);
    const [a, b] = trSpan(), q = findQ();
    let rows = trRows(a, b);
    const counts = new Map();
    for (const r of rows) counts.set(r.kind, (counts.get(r.kind) || 0) + 1);
    if (state.trKind !== "all" && !counts.get(state.trKind)) state.trKind = "all";
    for (const [k, label] of [["all", "All"]].concat(TR_KINDS.filter(x => counts.get(x[0])).map(x => [x[0], x[1]]))) {
      const bt = el("button", null, chips, label + (k !== "all" ? " " + fmtInt(counts.get(k)) : ""));
      bt.type = "button"; bt.setAttribute("aria-pressed", String(state.trKind === k));
      bt.addEventListener("click", () => { state.trKind = k; render(); });
    }
    rows = rows.filter(r => (state.trKind === "all" || r.kind === state.trKind) &&
      (!q || MEMBERS[r.m].label.toLowerCase().indexOf(q) >= 0 || r.peer.toLowerCase().indexOf(q) >= 0 || r.note.toLowerCase().indexOf(q) >= 0 || (TR_KNAME[r.kind] || "").toLowerCase().indexOf(q) >= 0));
    const top = Math.max(1, ...rows.map(r => Math.max(r.in, r.out)));
    note.textContent = fmtInt(rows.length) + " pair" + (rows.length === 1 ? "" : "s") + " · " + (state.msel ? "measured span" : "time range in view") + " · click one for its connections";
    const bar = (v, cls) => cell => { const w = el("div", "tbar " + cls, cell); w.style.width = Math.max(v > 0 ? 1 : 0, Math.round(100 * v / top)) + "%"; };
    sortableTable(wrap, [
      { key: "m", label: "Member", val: r => MEMBERS[r.m].label },
      { key: "peer", label: "Peer", val: r => r.peer, cls: "op", title: r => r.note },
      { key: "kind", label: "Kind", val: r => TR_KNAME[r.kind] || r.kind },
      { key: "in", label: "Received", num: true, val: r => r.in, fmt: r => fmtBytes(r.in), cellFn: r => bar(r.in, "in"), data: r => r.in },
      { key: "out", label: "Sent", num: true, val: r => r.out, fmt: r => fmtBytes(r.out), cellFn: r => bar(r.out, "out"), data: r => r.out },
      { key: "tot", label: "Total", num: true, val: r => r.in + r.out, fmt: r => fmtBytes(r.in + r.out) },
      { key: "rate", label: "Average", num: true, val: r => (r.in + r.out) / Math.max(1, (b - a) / 1000), fmt: r => fmtBytes((r.in + r.out) / Math.max(1, (b - a) / 1000)) + "/s" },
      { key: "n", label: "Connections", num: true, val: r => r.nconn, fmt: r => r.L ? fmtInt(r.nconn) : "" },
      { key: "note", label: "Note", val: r => r.note, cls: "wrapcell" }
    ], rows, state.trSort, render, r => { if (!r.L) return; state.trSel = state.trSel === r.L.i ? null : r.L.i; update(); },
    "No transfer in this selection.", r => r.L && r.L.i === state.trSel);
    // the connections of the picked pair
    const L = state.trSel != null ? TR_LINKS[state.trSel] : null;
    if (!L) return;
    el("h2", null, detail, trLinkName(L));
    const cs = (TR.conns || []).filter(c => c[0] === L.m && c[1] === L.p);
    el("p", "lead", detail, fmtInt(L.nconn) + " connection" + (L.nconn === 1 ? "" : "s") + " · " + (TR_KNAME[L.peer[0]] || L.peer[0]) + (L.peer[2] ? " · " + L.peer[2] : "") +
      ". Each connection is named local address -> remote address, as the member saw it; the bytes are over its whole life in these files." +
      (cs.length < L.nconn ? " The report keeps the " + fmtInt((TR.conns || []).length) + " busiest connections of all." : ""));
    const c2 = el("section", "card", detail), w2 = el("div", "table-wrap", c2);
    sortableTable(w2, [
      { key: "pid", label: "Connection", val: c => c[2], cls: "op" },
      { key: "a", label: "First seen", num: true, val: c => c[3], fmt: c => shortTime(c[3]) },
      { key: "l", label: "Last seen", num: true, val: c => c[4], fmt: c => shortTime(c[4]) },
      { key: "in", label: "Received", num: true, val: c => c[5], fmt: c => fmtBytes(c[5]) },
      { key: "out", label: "Sent", num: true, val: c => c[6], fmt: c => fmtBytes(c[6]) },
      { key: "ty", label: "Type", val: c => c[7] || "" }
    ], cs, TR_CSORT, render, null, "None kept.");
    const z = el("button", "btn btn-s", detail, "Clear the selection"); z.type = "button";
    z.addEventListener("click", () => { state.trSel = null; update(); });
  };
  tabUpdaters.push(Object.assign(render, { tab: "transfer" }));
}
const TR_CSORT = { key: "in", dir: -1 };

/* ---------- the other tabs */
function buildGeneric(tab, sec, list, intro) {
  if (intro) el("p", "lead", sec, intro);
  const used = new Set();
  headline(tab, sec, list, used);
  if (!D.metrics.some(r => r.c === tab) && !D.static.some(r => r.c === tab)) el("p", "lead", sec, "No metrics of this kind in these files.");
  moreSection(tab, sec, used);
}
function buildSystem(sec) {
  const used = new Set(["memory.usedHeap", "gc.minorTime", "gc.majorTime", "gc.unknownTime", "gc.minorCount", "gc.majorCount", "gc.unknownCount"]);
  if (MREC.has("memory.usedHeap")) {
    addPanel("system", sec, { title: "Heap", code: "memory.usedHeap, memory.maxHeap", render: p => {
      const spec = metricSpec(MREC.get("memory.usedHeap"));
      const ss = selSet();
      const maxRec = MREC.get("memory.maxHeap");
      for (const m of selMembers()) {
        const mx = maxRec ? null : staticValue("memory.maxHeap", m);
        if (mx > 0 && ss.has(m)) spec.series.push({ key: "x:" + m, label: MEMBERS[m].label + " max", color: null, faint: true, runs: [[X_MIN, X_MAX, mx]] });
      }
      if (maxRec) for (const s of mSeries(maxRec)) if (ss.has(s.m)) spec.series.push(Object.assign({ key: "x:" + s.m, label: MEMBERS[s.m].label + " max", color: null, faint: true }, s));
      spec.faintLabel = "max heap";
      drawTS(p, spec);
    } });
    addPanel("system", sec, { title: "Heap used, % of max heap", render: p => drawTS(p, { series: heapPctSeries(), unit: "pct", note: "highest per {step}" }) });
  }
  if (MREC.has("gc.minorTime") || MREC.has("gc.majorTime")) {
    addPanel("system", sec, { title: "Time in GC, % of wall time", code: "gc.minorTime + gc.majorTime + gc.unknownTime", render: p => drawTS(p, { series: sumSeries(["gc.minorTime", "gc.majorTime", "gc.unknownTime"], 0.1), unit: "pct", note: "average per {step}" }) });
    addPanel("system", sec, { title: "Major GC time, % of wall time", code: "gc.majorTime", render: p => drawTS(p, { series: sumSeries(["gc.majorTime"], 0.1), unit: "pct", note: "average per {step}" }) });
  }
  if (MREC.has("gc.minorCount") || MREC.has("gc.majorCount")) {
    addPanel("system", sec, { title: "Garbage collections per minute", code: "gc.minorCount + gc.majorCount + gc.unknownCount", render: p => drawTS(p, { series: sumSeries(["gc.minorCount", "gc.majorCount", "gc.unknownCount"], 60), unit: "count", note: "average per {step}", fmt: v => sig3(v) + " /min" }) });
  }
  if (!HASGC) el("p", "lead", sec, "No GC logs were found with these files: put the members' GC logs (-Xlog:gc*:file=gc.log:time,uptime) in the input folder, next to their diagnostics, for a GC tab with every pause and the humongous allocations.");
  headline("system", sec, [["os.processCpuLoad", "Process CPU"], ["os.systemCpuLoad", "System CPU"], ["os.systemLoadAverage", "System load average"],
    ["thread.threadCount", "Threads"], ["os.openFileDescriptorCount", "Open file descriptors"], ["os.freePhysicalMemorySize", "Free physical memory"],
    ["memory.usedNative", "Used native memory"], ["os.freeSwapSpaceSize", "Free swap"]], used);
  moreSection("system", sec, used);
}

/* ---------- GC: the pauses in the members' GC logs (found next to their diagnostics and member logs) */
// pause categories, in a fixed order; their colours (blue, green, orange) were checked as all pairs on both
// surfaces. Full GCs and evacuation failures are a state, not a series: the critical red, as a cross with a line
const GC_CATS = [{ label: "Young, mixed", ci: 0 }, { label: "Remark, cleanup, other", ci: 2 }, { label: "Humongous allocation", ci: 1 },
  { label: "Full GC, evacuation failure", status: true }];
const GC_TOSPACE = 1, GC_UNDO = 2, GC_MARK = 4;
const gcColor = c => GC_CATS[c].status ? stuckColor() : catColor(GC_CATS[c].ci);
const GC_KCAT = GCD.kinds.map(k => /^Pause Full|Degenerated/.test(k) ? 3 : /^Pause Young/.test(k) ? 0 : 1);
const GC_HUM = GCD.causes.map(c => /humongous/i.test(c));
const GCEV = [];
for (const col of GCD.mem) {
  const fl = new Map(col.f.map(x => [x[0], x]));
  let t = 0;
  for (let j = 0; j < col.t.length; j++) {
    t += col.t[j];
    const f = fl.get(j), flags = f ? f[1] : 0, k = col.k[j], c = col.c[j];
    let cat = GC_KCAT[k];
    if (cat !== 3 && (flags & GC_TOSPACE)) cat = 3;
    else if (cat !== 3 && GC_HUM[c]) cat = 2;
    GCEV.push({ m: col.m, t, i: col.i ? col.i[j] : -1, k, c, b: col.b[j], a: col.a[j], h: col.h[j], p: col.p[j] / 1000, fl: flags,
      hb: f ? f[2] : null, ha: f ? f[3] : null, cat, hum: GC_HUM[c] });
  }
}
GCEV.sort((x, y) => x.t - y.t || x.m - y.m);
const GCT = Float64Array.from(GCEV.map(e => e.t));
// where each member has a GC log: first to last pause of each file, merged across rotations
const GC_COVER = (() => {
  const by = {};
  for (const f of GCD.files) if (f[3] != null) (by[f[0]] = by[f[0]] || []).push([f[3], f[4]]);
  for (const k in by) {
    const sp = by[k].sort((a, b) => a[0] - b[0]), out = [];
    for (const x of sp) { const q = out[out.length - 1]; if (q && x[0] <= q[1] + 1800000) q[1] = Math.max(q[1], x[1]); else out.push(x.slice()); }
    by[k] = out;
  }
  return by;
})();
const gcCover = () => unionCover(selMembers(), i => GC_COVER[i] || []);
const gcFiles = m => GCD.files.filter(f => f[0] === m);
// G1 region size: from the log, else G1's default for the largest heap seen (heap / 2048, a power of 2, 1 to 32 MB)
function gcRegion(ms) {
  for (const m of ms) for (const f of gcFiles(m)) if (f[7]) return { mb: f[7], known: true };
  let h = 0;
  for (const e of GCEV) if (ms.indexOf(e.m) >= 0 && e.h > h) h = e.h;
  if (!h) return null;
  return { mb: Math.min(32, Math.max(1, Math.pow(2, Math.floor(Math.log2(h / 2048))))), known: false, heap: h };
}
const gcState = { sel: "", sort: { key: "p", dir: -1 }, hsort: { key: "t", dir: 1 }, ksort: { key: "tot", dir: -1 }, fsort: { key: "t", dir: 1 } };
function gcMatch(e) {
  const s = gcState.sel;
  if (!s) return true;
  const x = s.split(":");
  return x[0] === "cat" ? e.cat === +x[1] : x[0] === "cause" ? e.c === +x[1] : x[0] === "kind" ? e.k === +x[1] : e.k === +x[1] && e.c === +x[2];
}
const gcSelName = s => { const x = s.split(":"); return x[0] === "cat" ? GC_CATS[+x[1]].label : x[0] === "cause" ? (GCD.causes[+x[1]] || "no cause") : x[0] === "kind" ? GCD.kinds[+x[1]] : GCD.kinds[+x[1]] + " · " + (GCD.causes[+x[2]] || "no cause"); };
// the pauses in view, on the selected members, matching Find and (unless all) the pauses picked
function gcRows(all) {
  const ss = selSet(), q = findQ(), out = [];
  for (let i = lowerBound(GCT, state.x0); i < GCEV.length && GCEV[i].t <= state.x1; i++) {
    const e = GCEV[i];
    if (!ss.has(e.m) || (!all && !gcMatch(e))) continue;
    if (q && GCD.kinds[e.k].toLowerCase().indexOf(q) < 0 && GCD.causes[e.c].toLowerCase().indexOf(q) < 0 && MEMBERS[e.m].label.toLowerCase().indexOf(q) < 0) continue;
    out.push(e);
  }
  return out;
}
// a table's columns without the member one when every row is from the same member
const gcCols = (cols, rows) => new Set(rows.map(e => e.m)).size <= 1 ? cols.filter(c => c.key !== "m") : cols;
const gcBaseCat = (k, c) => GC_KCAT[k] === 3 ? 3 : GC_HUM[c] ? 2 : GC_KCAT[k];
// the pauses by member: [[member, pauses]] in member order
function gcByMember(rows) {
  const by = new Map();
  for (const e of rows) { let a = by.get(e.m); if (!a) by.set(e.m, a = []); a.push(e); }
  return Array.from(by.entries()).sort((a, b) => a[0] - b[0]);
}
const gcMb = v => v == null ? "" : fmtBytes(v * MB);
const gcHeap = e => e.b == null ? "" : gcMb(e.b) + " → " + gcMb(e.a);
const gcId = e => e.i >= 0 ? "GC(" + e.i + ")" : "";
const gcThen = e => e.fl & GC_UNDO ? "cycle undone: objects already reclaimed" : e.fl & GC_MARK ? "concurrent mark cycle" : "";
const gcWhat = e => (e.fl & GC_TOSPACE ? "evacuation failure (to-space exhausted)" : "") + (GC_KCAT[e.k] === 3 ? "full GC" : "");
// the time-bin size for a chart: about px pixels per bin
function gcStep(p, px) {
  const want = (state.x1 - state.x0) / Math.max(10, plotWidth(p) / px);
  return TSTEPS.find(s => s >= want) || TSTEPS[TSTEPS.length - 1];
}
// per member: an array over the bins in view, NaN where the member has no GC log
function gcBins(step, m, fill) {
  const n0 = Math.floor(state.x0 / step), n = Math.floor(state.x1 / step) - n0 + 1, v = new Float64Array(n).fill(NaN);
  for (const c of GC_COVER[m] || []) {
    if (c[1] < state.x0 || c[0] > state.x1) continue;
    const a = Math.max(0, Math.floor(c[0] / step) - n0), b = Math.min(n - 1, Math.floor(c[1] / step) - n0);
    for (let i = a; i <= b; i++) v[i] = fill;
  }
  return { n0, n, v, t0: n0 * step, step };
}
function gcTip(box, e) {
  el("div", "tip-t", box, (gcId(e) ? gcId(e) + " " : "") + GCD.kinds[e.k]);
  el("div", "tip-sub", box, logTime(e.t) + " · " + MEMBERS[e.m].label);
  tipRow(box, gcColor(e.cat), fmtMs(e.p), "pause" + (GCD.causes[e.c] ? " · " + GCD.causes[e.c] : ""), false);
  if (e.b != null) el("div", "tip-foot", box, "Heap " + gcHeap(e) + (e.h != null ? " of " + gcMb(e.h) : ""));
  if (e.hb != null) el("div", "tip-foot", box, "Humongous regions " + e.hb + " → " + e.ha);
  const w = gcWhat(e), th = gcThen(e);
  if (w) el("div", "tip-foot bad", box, w.charAt(0).toUpperCase() + w.slice(1));
  if (th) el("div", "tip-foot", box, "Then: " + th);
}
// every pause as a dot: time, pause length; dots that would land on the same 2 px are drawn once (the longest)
function renderGcPauses(p) {
  const rows = gcRows();
  const W = p.width, H = p.o.height || 250, M = tsMargins(W, false);
  frame(p, H, M);
  p.spec = null;
  shadeGaps(p, gcCover(), "no GC log");
  const log = state.log;
  let lo = Infinity, hi = 0;
  for (const e of rows) { if (e.p > hi) hi = e.p; if (e.p > 0 && e.p < lo) lo = e.p; }
  const has = rows.length > 0;
  let sy, ticks;
  if (log) {
    const lmin = has && lo < Infinity ? Math.floor(Math.log10(lo)) : -1, lmax = has && hi > 0 ? Math.max(lmin + 1, Math.ceil(Math.log10(hi))) : lmin + 1;
    const k = (p.bottom - p.top - 6) / (lmax - lmin);
    sy = v => p.bottom - 3 - (Math.log10(Math.max(v, Math.pow(10, lmin))) - lmin) * k;
    ticks = [];
    for (let e = lmin; e <= lmax; e += lmax - lmin > 6 ? 2 : 1) ticks.push(Math.pow(10, e));
  } else {
    const T = linTicks(0, has ? hi * 1.04 : 1, Math.max(2, Math.round((p.bottom - p.top) / 46)), "ms", false);
    const k = (p.bottom - p.top) / ((T.max - T.min) || 1);
    sy = v => p.bottom - (v - T.min) * k;
    ticks = T.ticks;
  }
  for (const v of ticks) {
    const Y = Math.round(sy(v)) + 0.5;
    if (Y < p.top - 1 || Y > p.bottom + 1) continue;
    if (Math.abs(Y - p.bottom - 0.5) > 1) svgEl("line", { x1: M.l, x2: W - M.r, y1: Y, y2: Y, class: "gridline" }, p.g.grid);
    svgEl("text", { x: M.l - 8, y: Y + 4, class: "tick", "text-anchor": "end" }, p.g.grid).textContent = v === 0 ? "0" : fmtMs(v);
  }
  drawMarkers(p, true);
  const slots = new Map(), bad = [];
  for (const e of rows) {
    if (e.cat === 3) { bad.push(e); continue; }
    const X = p.sx(e.t), Y = sy(e.p), key = e.cat * 1e9 + Math.round(X / 2) * 1e4 + Math.round(Y / 2);
    const d = slots.get(key);
    if (!d) slots.set(key, { x: X, y: Y, e, n: 1 });
    else { d.n++; if (e.p > d.e.p) { d.e = e; d.y = Y; d.x = X; } }
  }
  const dots = Array.from(slots.values()).sort((a, b) => a.e.cat - b.e.cat);
  const seen = new Set();
  for (const d of dots) {
    seen.add(d.e.cat);
    svgEl("circle", { cx: d.x.toFixed(1), cy: d.y.toFixed(1), r: d.e.cat === 2 ? 3.5 : 2.5, fill: gcColor(d.e.cat), class: "gcdot" }, p.g.marks);
  }
  // full GCs and evacuation failures: a line across the chart, a cross at the pause, a label when there is room
  let lastX = -1e9;
  for (const e of bad) {
    const X = p.sx(e.t), Y = sy(e.p), Xr = Math.round(X) + 0.5;
    svgEl("line", { x1: Xr, x2: Xr, y1: p.top, y2: p.bottom, class: "gcfail" }, p.g.events);
    svgEl("path", { d: "M" + (X - 4.5).toFixed(1) + " " + (Y - 4.5).toFixed(1) + "l9 9m0 -9l-9 9", class: "gcx" }, p.g.marks);
    if (X - lastX > 90 && X + 84 <= W - M.r) {
      svgEl("text", { x: X + 4, y: p.top + 10, class: "oome-label" }, p.g.events).textContent = GC_KCAT[e.k] === 3 ? "full GC" : "evacuation failure";
      lastX = X;
    }
    dots.push({ x: X, y: Y, e, n: 1 });
  }
  if (!has) emptyNote(p, GCEV.length ? "No GC pause in this selection" : "No pause in the GC logs");
  clear(p.legend);
  p.legend.hidden = !has;
  for (let c = 0; c < 3; c++) {
    const it = el("span", "lg lg-static" + (seen.has(c) ? "" : " off"), p.legend), sw = el("span", "sw sw-dot", it);
    sw.style.background = gcColor(c);
    el("span", null, it, GC_CATS[c].label);
  }
  if (bad.length) { const it = el("span", "lg lg-static", p.legend); el("span", "gcx-key", it, "✕"); el("span", null, it, GC_CATS[3].label); }
  el("span", "lg-note", p.legend, "one dot per pause" + (log ? " · log scale" : ""));
  oomeLegend(p.legend);
  p.note.textContent = has ? fmtInt(rows.length) + " pause" + (rows.length === 1 ? "" : "s") + (gcState.sel ? " · " + gcSelName(gcState.sel) : "") : "";
  p.tipAt = (t, py, px) => {
    let best = null, bd = 100;
    for (const d of dots) { const dx = d.x - px, dy = d.y - py, q = dx * dx + dy * dy; if (q < bd) { bd = q; best = d; } }
    if (!best) return null;
    clear(p.hot);
    svgEl("circle", { cx: best.x, cy: best.y, r: 6, class: "dot-hot" }, p.hot);
    return box => { gcTip(box, best.e); if (best.n > 1) el("div", "tip-sub", box, "the longest of " + best.n + " pauses at this spot"); };
  };
  finish(p);
}
// GC logs whose lines have the JVM uptime only: placed in time with the JVM start of their member, else not
const GC_UNPLACED = GCD.files.reduce((a, f) => a + (f[12] || 0), 0);
const GC_UPFILES = GCD.files.filter(f => /JVM start/.test(f[11] || "")).length;
const GC_UPTIME_HOW = "A GC log with the JVM uptime only on its lines (the default of -Xlog) is placed in time with its member's JVM start: the dump time " +
  "minus runtime.uptime in the diagnostics, checked against the GC counts (gc.minorCount).";
function buildGc(sec) {
  if (!GCEV.length) {
    el("p", "lead", sec, "GC log files were found (listed below), but no GC pause in them" + (META.window ? " between --from and --to." : GC_UNPLACED ?
      " could be placed in time: their lines have the JVM uptime only, and " + (NDIAG ? "they match none of their member's lifetimes in the diagnostics. " :
        "there are no diagnostics to place them with. ") + GC_UPTIME_HOW + " With -Xlog:gc*:file=gc.log:time,uptime each line has the date and time." :
      ": they need a date-time on each line (JDK 9+: -Xlog:gc*:file=gc.log:time,uptime; JDK 8: -XX:+PrintGCDetails -XX:+PrintGCDateStamps)."));
    gcFilesCard(sec);
    return;
  }
  const coll = Array.from(new Set(GCD.files.map(f => f[6]).filter(x => x)));
  if (UPTIME) el("p", "lead", sec, "From the GC logs (" + (coll.length ? coll.join(", ") : "collector not in the log") + "): every stop-the-world pause, " +
    "its kind and cause, the heap before and after it. Their lines have the JVM uptime only and no diagnostics came with them, so the time axis is " +
    "the JVM uptime (0 is the JVM start, 3d 06:00 is 3 days and 6 hours later) and each JVM has a line of its own: a log's rotated files go " +
    "together when each starts where the one before ended (GC ids go on), and a file that starts from 0 again is another JVM.");
  else el("p", "lead", sec, "From the GC logs found with the diagnostics and member logs (" + (coll.length ? coll.join(", ") : "collector not in the log") +
    "): every stop-the-world pause, its kind and cause, the heap before and after it. A GC log goes with the member whose files are in the same " +
    "folder, else whose host name is in its file or folder names, else whose files are in the nearest folder (then the JVM start time); the GC log files table says which." +
    (GC_UPFILES ? " " + GC_UPTIME_HOW : "") + (GC_UNPLACED ? " " + fmtInt(GC_UNPLACED) + " pause" + (GC_UNPLACED === 1 ? "" : "s") +
      " with the JVM uptime only could not be placed in time: see the GC log files table." : ""));
  // the pauses to show: a category, a cause or a kind
  const lab = el("label", "ctl", ctxRow("gc"));
  el("span", "ctl-label", lab, "Pauses");
  const sel = el("select", "opsel", lab);
  sel.addEventListener("change", () => { gcState.sel = sel.value; update(); });
  let selKey = null;
  syncers.push(() => {
    if (state.tab !== "gc") return;
    const rows = gcRows(true), nc = [0, 0, 0, 0], ncause = new Map(), nkind = new Map();
    for (const e of rows) { nc[e.cat]++; ncause.set(e.c, (ncause.get(e.c) || 0) + 1); nkind.set(e.k, (nkind.get(e.k) || 0) + 1); }
    const key = nc.join(",") + "|" + Array.from(ncause).join(",") + "|" + Array.from(nkind).join(",") + "|" + gcState.sel;
    if (key !== selKey) {
      selKey = key;
      clear(sel);
      el("option", null, sel, "All " + fmtInt(rows.length) + " pauses").value = "";
      const g1 = el("optgroup", null, sel); g1.label = "Kind of pause";
      GC_CATS.forEach((c, i) => { if (nc[i] || gcState.sel === "cat:" + i) { const o = el("option", null, g1, c.label + " (" + fmtInt(nc[i]) + ")"); o.value = "cat:" + i; } });
      const g2 = el("optgroup", null, sel); g2.label = "Cause";
      Array.from(ncause).sort((a, b) => b[1] - a[1]).forEach(([c, n]) => { const o = el("option", null, g2, (GCD.causes[c] || "no cause given") + " (" + fmtInt(n) + ")"); o.value = "cause:" + c; });
      const g3 = el("optgroup", null, sel); g3.label = "Pause";
      Array.from(nkind).sort((a, b) => b[1] - a[1]).forEach(([k, n]) => { const o = el("option", null, g3, GCD.kinds[k] + " (" + fmtInt(n) + ")"); o.value = "kind:" + k; });
      if (gcState.sel && !sel.querySelector("option[value=\"" + gcState.sel + "\"]")) { const o = el("option", null, sel, gcSelName(gcState.sel)); o.value = gcState.sel; }
    }
    sel.value = gcState.sel;
  });
  const bar = el("div", "selbar", sec); bar.hidden = true;
  tabUpdaters.push(Object.assign(() => {
    clear(bar);
    bar.hidden = !gcState.sel;
    if (!gcState.sel) return;
    bar.appendChild(document.createTextNode("Pauses, numbers and tables show "));
    el("b", null, bar, gcSelName(gcState.sel));
    bar.appendChild(document.createTextNode(" (the heap and humongous charts: every pause)"));
    const b = el("button", "btn btn-s", bar, "Show all"); b.type = "button";
    b.addEventListener("click", () => { gcState.sel = ""; update(); });
  }, { tab: "gc" }));
  const kp = el("div", "kpis", sec);
  tabUpdaters.push(Object.assign(() => {
    clear(kp);
    const rows = gcRows(), ms = new Set(rows.map(e => e.m));
    let cov = 0;
    for (const m of selMembers()) for (const c of GC_COVER[m] || []) cov += Math.max(0, Math.min(c[1], state.x1) - Math.max(c[0], state.x0));
    const tot = rows.reduce((a, e) => a + e.p, 0), mins = (state.x1 - state.x0) / 60000;
    const who = Array.from(ms).every(m => MEMBERS[m].gcOnly) ? (UPTIME ? "JVM" : "GC log") : "member";
    kpi(kp, gcState.sel ? "Pauses picked" : "GC pauses", fmtInt(rows.length), (mins > 0 ? sig3(rows.length / mins / Math.max(1, ms.size)) + " per minute per " + who + " · " : "") +
      fmtInt(ms.size) + " " + who + (ms.size === 1 ? "" : "s"));
    kpi(kp, "Time in these pauses", fmtMs(tot), cov > 0 ? fmtPct(100 * tot / cov) + " of the time, stop-the-world" : "");
    if (rows.length) {
      const w = rows.reduce((a, e) => e.p > a.p ? e : a);
      kpi(kp, "Longest pause", fmtMs(w.p), GCD.kinds[w.k] + " · " + mShort(w.m) + " · " + shortTime(w.t));
    }
    const hum = rows.filter(e => e.hum), und = hum.filter(e => e.fl & GC_UNDO).length;
    const hk = kpi(kp, "Humongous allocation GCs", fmtInt(hum.length), hum.length ? fmtPct(100 * hum.length / rows.length) + " of these pauses" + (und ? " · " + fmtInt(und) + " found the objects already gone" : "") : "no GC caused by one");
    if (hum.length) { hk.classList.add("hl"); hk.style.setProperty("--hl", gcColor(2)); }
    const bad = rows.filter(e => e.cat === 3), full = bad.filter(e => GC_KCAT[e.k] === 3).length, ev = bad.length - full;
    const bk = kpi(kp, "Full GCs, evacuation failures", fmtInt(bad.length), bad.length ? fmtInt(full) + " full GC" + (full === 1 ? "" : "s") + ", " + fmtInt(ev) + " evacuation failure" + (ev === 1 ? "" : "s") + " (to-space exhausted)" : "none");
    if (bad.length) { bk.classList.add("alert"); bk.querySelector(".kpi-value").classList.add("bad"); }
    let hp = null;
    for (const e of gcRows(true)) if (e.a != null && e.h > 0 && (!hp || e.a / e.h > hp.a / hp.h)) hp = e;
    if (hp) kpi(kp, "Highest heap after a GC", fmtPct(100 * hp.a / hp.h), gcMb(hp.a) + " of " + gcMb(hp.h) + " · " + mShort(hp.m) + " · " + shortTime(hp.t));
  }, { tab: "gc" }));
  const pp = addPanel("gc", sec, { title: "GC pauses", height: 250, noTable: true, render: renderGcPauses });
  pp.fixed = true;
  // humongous allocations
  const anyHum = GCEV.some(e => e.hum) || GCEV.some(e => e.hb != null);
  el("h2", null, sec, "Humongous allocations");
  const hlead = el("p", "lead", sec);
  tabUpdaters.push(Object.assign(() => {
    const ms = selMembers().filter(m => GC_COVER[m]), R = gcRegion(ms);
    hlead.textContent = "With G1, an object of half a region or more is humongous: it gets whole regions of its own, outside the young generation. " +
      (R ? "Region size " + (R.known ? "here: " + fmtBytes(R.mb * MB) : "probably " + fmtBytes(R.mb * MB) + " (not in the log: G1's default for a " + gcMb(R.heap) + " heap; -Xlog:gc+init shows it)") +
        ", so objects of " + fmtBytes(R.mb * MB / 2) + " or more. " : "") +
      "Allocating one can start a GC (cause “G1 Humongous Allocation”) and many fill the old generation fast. In a member they usually are large values " +
      "(big entries, query or aggregation results, putAll / getAll batches, large client messages) or big internal arrays (a large map partition growing, " +
      "a big partition being migrated). When the GC finds them already dead, G1 frees them at once and, from JDK 16, undoes the concurrent cycle it was " +
      "starting (Concurrent Undo Cycle): large short-lived objects." + (anyHum ? "" : " None of the pauses in these GC logs was caused by a humongous allocation.");
  }, { tab: "gc" }));
  if (anyHum) {
    const hp = addPanel("gc", sec, { title: "GCs caused by humongous allocations", height: 170, render: p => {
      const step = gcStep(p, 6), series = [];
      for (const [m, evs] of gcByMember(gcRows(true).filter(e => e.hum))) {
        const B = gcBins(step, m, 0);
        for (const e of evs) { const i = Math.floor(e.t / step) - B.n0; if (i >= 0 && i < B.n) B.v[i] = (B.v[i] || 0) + 1; }
        series.push({ key: "m:" + m, m, label: MEMBERS[m].label, color: memberColor(m), t0: B.t0, step, v: B.v, o: 0, noAgg: true, agg: "sum" });
      }
      if (series.length === 1) series[0].color = gcColor(2);      // one member: the orange of their dots
      drawTS(p, { kind: "stack", series, unit: "count", note: "how many per " + stepName(step) + " · their pauses are the orange dots above", fmt: v => fmtInt(v), measure: "sum",
        cover: gcCover, coverLabel: "no GC log", empty: "No GC caused by a humongous allocation in this selection" });
    } });
    hp.fixed = true;
    if (GCEV.some(e => e.hb != null)) {
      const rp = addPanel("gc", sec, { title: "Humongous regions before each GC", height: 190, render: p => {
        const step = gcStep(p, 2), series = [];
        for (const [m, evs] of gcByMember(gcRows(true).filter(e => e.hb != null))) {
          const pts = [], R = gcRegion([m]);
          let last = -1;
          for (const e of evs) {
            const b = Math.floor(e.t / step), v = R ? e.hb * R.mb * MB : e.hb;
            if (b === last && v <= pts[pts.length - 1][1]) continue;
            if (b === last) pts[pts.length - 1] = [e.t, v]; else pts.push([e.t, v]);
            last = b;
          }
          if (pts.length) series.push({ key: "m:" + m, label: MEMBERS[m].label, color: memberColor(m), faint: m >= 8, pts, join: 1800000 });
        }
        const R = gcRegion(selMembers());
        drawTS(p, { series, unit: R ? "bytes" : "count", note: "highest per " + stepName(step) + (R ? " · regions × region size" : " · regions"), cover: gcCover, coverLabel: "no GC log" });
      } });
      rp.fixed = true;
    }
    const card = el("section", "card", sec), cap = el("div", "cap", card);
    el("h3", null, cap, "GCs caused by humongous allocations");
    const note = el("span", "note", cap), wrap = el("div", "table-wrap tall", card);
    tabUpdaters.push(Object.assign(() => {
      clear(wrap);
      const rows = gcRows(true).filter(e => e.hum), hr = rows.some(e => e.hb != null);
      note.textContent = fmtInt(rows.length) + " in the time range in view";
      const cols = [
        { key: "t", label: "Time", num: true, val: e => e.t, fmt: e => shortTime(e.t) },
        { key: "m", label: "Member", val: e => MEMBERS[e.m].label, fmt: e => mShort(e.m), title: e => MEMBERS[e.m].label },
        { key: "i", label: "GC", num: true, val: e => e.i, fmt: gcId },
        { key: "k", label: "Pause", val: e => GCD.kinds[e.k] },
        { key: "b", label: "Heap before → after", num: true, val: e => e.b || 0, fmt: gcHeap },
        { key: "p", label: "Pause time", num: true, val: e => e.p, fmt: e => fmtMs(e.p) },
        { key: "th", label: "Then", val: gcThen }];
      if (hr) cols.splice(5, 0, { key: "hb", label: "Humongous regions", num: true, val: e => e.hb == null ? -1 : e.hb, fmt: e => e.hb == null ? "" : e.hb + " → " + e.ha });
      sortableTable(wrap, gcCols(cols, rows), rows, gcState.hsort, () => update(), null, "No GC caused by a humongous allocation in this selection.", null, 1000);
    }, { tab: "gc" }));
  }
  // heap and time in pauses
  el("h2", null, sec, "Heap and time in pauses");
  const heap = addPanel("gc", sec, { title: "Heap after GC", height: 200, render: p => {
    const step = gcStep(p, 2), series = [];
    for (const [m, evs] of gcByMember(gcRows(true).filter(e => e.a != null))) {
      const lo = [], hs = [];
      let last = -1;
      for (const e of evs) {
        const b = Math.floor(e.t / step);
        if (b !== last) { lo.push([e.t, e.a * MB]); hs.push([e.t, (e.h || 0) * MB]); last = b; continue; }
        if (e.a * MB < lo[lo.length - 1][1]) lo[lo.length - 1] = [e.t, e.a * MB];
        if ((e.h || 0) * MB > hs[hs.length - 1][1]) hs[hs.length - 1][1] = (e.h || 0) * MB;
      }
      if (!lo.length) continue;
      series.push({ key: "m:" + m, label: MEMBERS[m].label, color: memberColor(m), faint: m >= 8, pts: lo, join: 1800000 });
      series.push({ key: "h:" + m, label: MEMBERS[m].label + " heap size", faint: true, pts: hs, join: 1800000 });
    }
    drawTS(p, { series, unit: "bytes", note: "lowest per " + stepName(step) + " (the live data, roughly) · grey: heap size", faintLabel: "heap size", cover: gcCover, coverLabel: "no GC log" });
  } });
  heap.fixed = true;
  const tp = addPanel("gc", sec, { title: "Time in GC pauses, % of wall time", height: 180, render: p => {
    const step = gcStep(p, 3), series = [], by = new Map(gcByMember(gcRows()));
    for (const m of selMembers()) {
      if (!GC_COVER[m]) continue;
      const B = gcBins(step, m, 0);
      for (const e of by.get(m) || []) { const i = Math.floor(e.t / step) - B.n0; if (i >= 0 && i < B.n) B.v[i] = (B.v[i] || 0) + 100 * e.p / step; }
      series.push({ key: "m:" + m, m, label: MEMBERS[m].label, color: memberColor(m), faint: m >= 8, t0: B.t0, step, v: B.v, o: 0, noAgg: true, agg: "mean" });
    }
    drawTS(p, { series, unit: "pct", note: "per " + stepName(step) + (gcState.sel ? " · " + gcSelName(gcState.sel) : ""), cover: gcCover, coverLabel: "no GC log", measure: "gauge" });
  } });
  tp.fixed = true;
  // full GCs and evacuation failures
  const bcard = el("section", "card alert", sec), bcap = el("div", "cap", bcard);
  el("h3", null, bcap, "Full GCs and evacuation failures");
  const bnote = el("span", "note", bcap);
  el("p", "lead", bcard, "A full GC stops the member to collect the whole heap; an evacuation failure (to-space exhausted) is a pause that found no free region to copy " +
    "the live objects to: both mean the heap was too full, often right after a burst of humongous allocations.");
  const bwrap = el("div", "table-wrap tall", bcard);
  tabUpdaters.push(Object.assign(() => {
    clear(bwrap);
    const rows = gcRows(true).filter(e => e.cat === 3);
    bcard.hidden = !rows.length;
    bnote.textContent = fmtInt(rows.length) + " in the time range in view";
    sortableTable(bwrap, gcCols([
      { key: "t", label: "Time", num: true, val: e => e.t, fmt: e => shortTime(e.t) },
      { key: "m", label: "Member", val: e => MEMBERS[e.m].label, fmt: e => mShort(e.m), title: e => MEMBERS[e.m].label },
      { key: "i", label: "GC", num: true, val: e => e.i, fmt: gcId },
      { key: "w", label: "What", val: gcWhat, cls: "wrapcell" },
      { key: "k", label: "Pause", val: e => GCD.kinds[e.k] },
      { key: "c", label: "Cause", val: e => GCD.causes[e.c] },
      { key: "b", label: "Heap before → after", num: true, val: e => e.b || 0, fmt: e => gcHeap(e) + (e.h != null ? " of " + gcMb(e.h) : "") },
      { key: "p", label: "Pause time", num: true, val: e => e.p, fmt: e => fmtMs(e.p) }
    ], rows), rows, gcState.fsort, () => update(), null, "None in this selection.", null, 500);
  }, { tab: "gc" }));
  // kinds and causes
  const kcard = el("section", "card", sec), kcap = el("div", "cap", kcard);
  el("h3", null, kcap, "Pauses by kind and cause");
  const knote = el("span", "note", kcap), kwrap = el("div", "table-wrap tall", kcard);
  tabUpdaters.push(Object.assign(() => {
    clear(kwrap);
    const by = new Map(), all = gcRows(true);
    let total = 0, nev = 0;
    for (const e of all) {
      const key = e.k + ":" + e.c;
      let g = by.get(key);
      if (!g) { g = { key: "kc:" + key, k: e.k, c: e.c, cat: gcBaseCat(e.k, e.c), n: 0, tot: 0, mx: 0, ev: 0, ms: new Set() }; by.set(key, g); }
      g.n++; g.tot += e.p; g.mx = Math.max(g.mx, e.p); g.ms.add(e.m); total += e.p;
      if (e.fl & GC_TOSPACE) { g.ev++; nev++; }
    }
    const rows = Array.from(by.values());
    knote.textContent = fmtInt(rows.length) + " kind" + (rows.length === 1 ? "" : "s") + " of pause · click one to show only it";
    const kcols = [
      { key: "cat", label: "Kind", val: g => g.cat, fmt: g => GC_CATS[g.cat].label, cellFn: g => td => { const sw = el("span", "sw", null); sw.style.background = gcColor(g.cat); sw.style.marginRight = "6px"; td.insertBefore(sw, td.firstChild); } },
      { key: "k", label: "Pause", val: g => GCD.kinds[g.k] },
      { key: "c", label: "Cause", val: g => GCD.causes[g.c] },
      { key: "n", label: "Pauses", num: true, val: g => g.n, fmt: g => fmtInt(g.n) },
      { key: "tot", label: "Total time", num: true, val: g => g.tot, fmt: g => fmtMs(g.tot) },
      { key: "share", label: "Share of the time", num: true, val: g => g.tot, fmt: g => total > 0 ? fmtPct(100 * g.tot / total) : "" },
      { key: "avg", label: "Average", num: true, val: g => g.tot / g.n, fmt: g => fmtMs(g.tot / g.n) },
      { key: "mx", label: "Longest", num: true, val: g => g.mx, fmt: g => fmtMs(g.mx) },
      { key: "ms", label: "Members", num: true, val: g => g.ms.size }];
    if (nev) kcols.splice(4, 0, { key: "ev", label: "Evacuation failures", num: true, val: g => g.ev, fmt: g => g.ev ? fmtInt(g.ev) : "" });
    if (NM === 1) kcols.pop();
    sortableTable(kwrap, kcols, rows, gcState.ksort, () => update(), g => { gcState.sel = gcState.sel === g.key ? "" : g.key; update(); }, "No GC pause in this selection.", g => gcState.sel === g.key);
  }, { tab: "gc" }));
  // the longest pauses
  const lcard = el("section", "card", sec), lcap = el("div", "cap", lcard);
  el("h3", null, lcap, "Longest pauses");
  const lnote = el("span", "note", lcap), lwrap = el("div", "table-wrap tall", lcard);
  tabUpdaters.push(Object.assign(() => {
    clear(lwrap);
    const rows = gcRows().sort((a, b) => b.p - a.p).slice(0, 200);
    lnote.textContent = "the " + fmtInt(rows.length) + " longest in the time range in view" + (gcState.sel ? " · " + gcSelName(gcState.sel) : "");
    sortableTable(lwrap, gcCols([
      { key: "t", label: "Time", num: true, val: e => e.t, fmt: e => shortTime(e.t) },
      { key: "m", label: "Member", val: e => MEMBERS[e.m].label, fmt: e => mShort(e.m), title: e => MEMBERS[e.m].label },
      { key: "i", label: "GC", num: true, val: e => e.i, fmt: gcId },
      { key: "k", label: "Pause", val: e => GCD.kinds[e.k], cellFn: e => td => { const sw = el("span", "sw", null); sw.style.background = gcColor(e.cat); sw.style.marginRight = "6px"; td.insertBefore(sw, td.firstChild); } },
      { key: "c", label: "Cause", val: e => GCD.causes[e.c] },
      { key: "b", label: "Heap before → after", num: true, val: e => e.b || 0, fmt: gcHeap },
      { key: "p", label: "Pause time", num: true, val: e => e.p, fmt: e => fmtMs(e.p) },
      { key: "x", label: "Notes", val: e => gcWhat(e) || gcThen(e), cls: "wrapcell" }
    ], rows), rows, gcState.sort, () => update(), null, "No GC pause in this selection.", null, 200);
  }, { tab: "gc" }));
  gcFilesCard(sec);
}
function gcFilesCard(sec) {
  const card = el("section", "card", sec), cap = el("div", "cap", card);
  el("h3", null, cap, "GC log files");
  el("span", "note", cap, fmtInt(GCD.files.length) + " file" + (GCD.files.length === 1 ? "" : "s") + " · a member's rotated files and copies are read once");
  const wrap = el("div", "table-wrap", card), st = { key: "m", dir: 1 };
  const render = () => {
    clear(wrap);
    sortableTable(wrap, [
      { key: "m", label: "Member", val: f => f[0] < 0 ? "" : MEMBERS[f[0]].label, fmt: f => f[0] < 0 ? "–" : mShort(f[0]),
        title: f => f[0] < 0 ? "" : MEMBERS[f[0]].label, cls: "wrapcell" },
      { key: "f", label: "File", val: f => f[1], cls: "wrapcell" },
      MEMBERS.some(m => !m.gcOnly) ? { key: "how", label: "Matched by", val: f => f[2] } : null,
      { key: "src", label: "Times from", val: f => f[11] || "", cls: "wrapcell" },
      { key: "col", label: "Collector", val: f => f[6] || "" },
      { key: "reg", label: "Region size", num: true, val: f => f[7] || 0, fmt: f => f[7] ? fmtBytes(f[7] * MB) : "" },
      { key: "a", label: "First pause", num: true, val: f => f[3] || 0, fmt: f => f[3] ? shortTime(f[3]) : "" },
      { key: "b", label: "Last pause", num: true, val: f => f[4] || 0, fmt: f => f[4] ? shortTime(f[4]) : "" },
      { key: "n", label: "Pauses", num: true, val: f => f[5], fmt: f => fmtInt(f[5]) },
      { key: "j", label: "JVM runs", num: true, val: f => f[9] || 0 },
      { key: "s", label: "Size", num: true, val: f => f[10] || 0, fmt: f => f[10] ? fmtBytes(f[10]) : "" }
    ].filter(Boolean), GCD.files, st, render, null, "No GC log file.");
  };
  render();
}

/* ---------- header */
function buildHeader() {
  // GC logs linked to no member have a lane of their own, but they are not members
  const real = MEMBERS.filter(m => !m.gcOnly), ngc = NM - real.length;
  $("title").textContent = real.length > 1 ? "Hazelcast cluster: " + real.length + " members" : real.length ? "Hazelcast member " + real[0].label
    : ngc > 1 ? "Hazelcast GC logs: " + ngc : "Hazelcast " + MEMBERS[0].label;
  if (ngc && real.length) {
    const t = el("span", "title-note", $("title"), " + " + ngc + " GC log" + (ngc === 1 ? "" : "s") + " not linked to a member");
    t.title = "GC logs whose folder, name and JVM start time match none of the members: each has a line of its own on the GC tab (Members and files lists them)";
  }
  const sub = $("subtitle"), add = t => el("span", null, sub, t);
  add(span(X_MIN, X_MAX) + " (" + dur((X_MAX - X_MIN) / 1000) + ")");
  add(META.timeNote);
  if (META.window) add("--from/--to applied");
  if (META.files || (!META.logfiles && !META.gcfiles)) add(fmtInt(META.files) + " diagnostics file" + (META.files === 1 ? "" : "s") + ", " + fmtBytes(META.bytes));
  if (META.logfiles) add(fmtInt(META.logfiles) + " member log file" + (META.logfiles === 1 ? "" : "s") + ", " + fmtBytes(META.logbytes));
  if (META.gcfiles) add(fmtInt(META.gcfiles) + " GC log file" + (META.gcfiles === 1 ? "" : "s") + ", " + fmtBytes(META.gcbytes));
  const box = $("files-box");
  box.hidden = false;
  $("members-sum").textContent = "Members and files · " + META.input;
  const ul = $("members-list");
  MEMBERS.forEach((m, mi) => {
    const li = el("li", null, ul);
    el("b", null, li, m.label);
    const lf = HASLOGS ? D.logs.files.filter(f => f[0] === mi) : [];
    const logs = lf.length ? lf.length + " member log file" + (lf.length === 1 ? "" : "s") + " (" + fmtBytes(lf.reduce((a, f) => a + f[2], 0)) + ")" : "";
    const gf = GCD.files.filter(f => f[0] === mi);
    const gcl = gf.length ? (gf.length === 1 ? "GC log " + gf[0][1] + " (" + gf[0][2] + ")" : gf.length + " GC log files (" + Array.from(new Set(gf.map(f => f[2]))).join(", ") + ")") : "";
    if (m.gcOnly) { li.appendChild(document.createTextNode(" · GC log only, no member's files near it: " + gf.map(f => f[1]).join(", "))); return; }
    if (m.logsOnly) { li.appendChild(document.createTextNode(" · member logs only: " + logs + (m.dirs.length ? " · " + m.dirs.join(", ") : "") + (gcl ? " · " + gcl : ""))); return; }
    li.appendChild(document.createTextNode(" · " + m.files + " file" + (m.files === 1 ? "" : "s") + ", " + (m.bytes / 1048576).toFixed(1) + " MB, " +
      m.lifetimes.length + " lifetime" + (m.lifetimes.length === 1 ? "" : "s") + (m.dirs.length ? " · " + m.dirs.join(", ") : "") +
      (m.cover.length ? " · " + span(m.cover[0][0], m.cover[m.cover.length - 1][1]) : "") + (logs ? " · " + logs : "") + (gcl ? " · " + gcl : "")));
  });
  const lost = GCD.files.filter(f => f[0] < 0);
  if (lost.length) el("li", null, ul, "GC log" + (lost.length === 1 ? "" : "s") + " not linked to a member, with no pause to chart: " + lost.map(f => f[1]).join(", ") + " (GC tab)");
  if (META.skipped && META.skipped.length) el("li", null, ul, "Neither diagnostics, member logs nor GC logs, skipped: " + META.skipped.join(", "));
  $("tz-note").textContent = "are " + META.timeNote;
  const lim = [];
  const res = (D.g ? stepName(G.step) + " for metrics and per " : "") + stepName(PSTEP) + " for the profilers";
  if (!HASDIAG) lim.push((HASLOGS ? "Only member logs" + (HASGC ? " and GC logs" : "") : "Only GC logs") + " here: the Diagnostics tabs need the diagnostics files (hazelcast.diagnostics.enabled=true).");
  else if (META.points === "max")
    lim.push("Resolution: every dump is in this report, one point per " + res + ". When a chart has more points than pixels, " +
      "neighbouring points are merged for drawing (the highest for sizes, the average for rates, the sum for profiler counts); zoom in to see every dump.");
  else
    lim.push("Resolution (--points " + META.points + "): one point per " + res + "; --points max (the default) keeps every dump.");
  if (EF > 1) lim.push("Per-item lines use " + stepName(G.step * EF) + " points to keep the file small (--budget; 0 for no limit).");
  if (HASDIAG) lim.push("Per-item lines are kept for the " + META.top + " most active items of each metric (--top).");
  const c = META.counters || {};
  if (c.truncated) lim.push(fmtInt(c.truncated) + " section(s) cut short (end of a file, a crash) were skipped.");
  if (c.out_of_order) lim.push(fmtInt(c.out_of_order) + " line(s) already read from another copy of the same file were skipped.");
  if (c.non_numeric) lim.push(fmtInt(c.non_numeric) + " metric value(s) were not numbers (NA or an error text) and were skipped.");
  if (META.timestamps && META.timestamps.length) lim.push("Timestamp metrics, not charted: " + META.timestamps.join(", ") + ".");
  $("limits-note").textContent = lim.join(" ");
  $("gen-note").textContent = "Generated " + META.generated + " by hz_diag_parser.py " + META.version + ": " + META.cmd;
}

/* ---------- build everything */
buildHeader();
buildTabs();
buildToolbar();
buildOverview(sections.overview);
buildLatency(sections.latency);
buildSlowOps(sections.slowops);
if (HASINV) buildInvocations(sections.invocations);
el("p", "lead", sections.operations, "Operation and response queues, invocations and executors (the partition and generic operation threads are on the Threads tab). Per-executor metrics show the sum over all of them by member; Lines: By name splits them.");
buildSaturation(sections.operations);
buildGeneric("operations", sections.operations, [["operation.queueSize"], ["operation.priorityQueueSize"], ["operation.genericQueueSize"],
  ["operation.responseQueueSize"], ["operation.invocations.pending"], ["operation.invocations.usedPercentage"], ["operation.completedCount"],
  ["executor.internal.queueSize"], ["executor.internal.poolSize"], ["event.eventQueueSize"]], null);
buildThreads(sections.threads);
buildGeneric("network", sections.network, [["tcp.bytesReceived"], ["tcp.bytesSend"],
  ["tcp.connection.activeCount"], ["tcp.connection.clientCount"], ["tcp.connection.count"],
  ["tcp.connection.in.bytesRead"], ["tcp.connection.out.bytesWritten"], ["tcp.connection.out.writeQueueSize"]],
  "Bytes in and out (rates), connections and connection pipelines (the IO threads are on the Threads tab, WAN traffic on the WAN tab, how much each member exchanged with each peer on the Transfer tab). Lines: By name splits the per-pipeline metrics.");
buildTransfer(sections.transfer);
buildWan(sections.wan);
buildMaps(sections.maps);
if (HASSTORE) buildMapStore(sections.mapstore);
buildSystem(sections.system);
if (HASGC) buildGc(sections.gc);
buildCluster(sections.cluster);
buildCp(sections.cp);
if (HASPROPS) buildConfig(sections.config);
buildGeneric("other", sections.other, [], "Every metric that fits none of the other tabs.");
if (HASLOGS) {
  buildLogsOverview(sections.logs);
  buildLogsHealth(sections["logs-health"]);
  buildLogsExceptions(sections["logs-exceptions"]);
  buildLogsMigrations(sections["logs-migrations"]);
  buildLogsCp(sections["logs-cp"]);
}
syncers.push(syncCtx);           // after every tab's pickers: show the row of the tab in view, if it has any
$("top").hidden = false;
$("notes").hidden = false;
const h = (location.hash || "").replace("#", "");
setTab(ALLTABS.some(t => t[0] === h) ? h : DEFTAB);
let rz = null, lastW = window.innerWidth;
window.addEventListener("resize", () => { if (window.innerWidth === lastW) return; lastW = window.innerWidth; clearTimeout(rz); rz = setTimeout(invalidate, 150); });
if (mq && mq.addEventListener) mq.addEventListener("change", () => { if (!root.getAttribute("data-theme")) update(); });
window.addEventListener("scroll", () => { if (tip.style.display === "block") clearHover(); }, { passive: true });
window.addEventListener("beforeprint", () => { for (const p of panels) if (p.tab === state.tab && !p.card.hidden) renderPanel(p); });
window.addEventListener("hashchange", () => { const k = (location.hash || "").replace("#", ""); if (k !== state.tab && sections[k]) setTab(k); });
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    sys.exit(main())
