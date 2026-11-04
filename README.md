# Hazelcast Diagnostics Analyzer

`hz_diag_parser.py` reads a folder of Hazelcast diagnostics files, from one member or a whole cluster, and builds
**one interactive HTML report**. If the members' logs and GC logs are in the folder too, it reads those as well.

The report is a single self-contained file. The data is embedded and compressed, so the report works offline in
any current browser and can be attached to a ticket or sent by mail.

- Python 3.6 or later, nothing else to install.
- Diagnostics files, member logs and GC logs, plain or `.gz`, from any number of members and over several days.
- Charts you can zoom, filter by member and search. Measure mode gives the amount that went through any time span.
- Light and dark themes. Works on a phone.

See [`examples/sample-report.html`](examples/sample-report.html) for a report on a synthetic three-member cluster.

## Quick start

```bash
python3 hz_diag_parser.py /path/to/diagnostics out/
open out/report.html            # or xdg-open on Linux, or double-click the file
```

The input folder is searched recursively. Files that are neither diagnostics, member logs nor GC logs are skipped
and listed. The console prints a summary with the members, the time range, stuck threads, saturated executors,
slow operations, OutOfMemoryErrors, GC pauses and more.

```bash
# full report
python3 hz_diag_parser.py in/ out/

# only two members, and one hour
python3 hz_diag_parser.py diag/ out/ --member 10.0.0.1:5701 --member 10.0.0.2 \
    --from "27-10-2026 15:00" --to "27-10-2026 16:00"

# a smaller report for a long capture (about 2,000 points across the time range)
python3 hz_diag_parser.py diag/ out/ --points 2000
```

## What to collect

Put everything for a cluster under one folder. A subfolder per member works best, because member logs and GC logs
are matched to a member by the folder they are in.

```
case-1234/
  member1/   diagnostics-10.0.0.1_5701-1793075400000-000.log ...   hazelcast.log   gc.log  gc.log.0
  member2/   diagnostics-10.0.0.2_5701-1793075420000-000.log ...   hazelcast.log   gc.log
  member3/   ...
```

### Diagnostics files

Diagnostics are written by every member when they are turned on, for example:

```
-Dhazelcast.diagnostics.enabled=true
-Dhazelcast.diagnostics.directory=/var/log/hazelcast
-Dhazelcast.diagnostics.operation-profiler.period.seconds=5
-Dhazelcast.diagnostics.invocation-profiler.period.seconds=5
```

The metrics and the slow operation detector are on by default. They are written every minute
(`hazelcast.diagnostics.metrics.period.seconds`, `hazelcast.diagnostics.slowoperations.period.seconds`). The
operation and invocation profilers are off until their period is set, and the Latency tab needs them.

A member is identified by its address, taken from the file name or from `thisAddress` in the file. The start time
in the file name separates its lifetimes: when a member restarts, its counters start again from zero and the report
doesn't mix them. The same file found twice is read once.

### Member logs (optional)

Add the members' own logs: `hazelcast.log`, console output, rotated or `.gz`. The usual layouts are recognised:
java.util.logging, log4j, log4j2, logback and ISO-8601 timestamps. A log is matched to a member by the address in
its lines. A folder with member logs only, and no diagnostics, works too.

### GC logs (optional)

Add the members' GC logs, with a date and time on every line:

```
# JDK 9 and later
-Xlog:gc*:file=/var/log/hazelcast/gc.log:time,uptime:filecount=10,filesize=50m

# JDK 8
-XX:+PrintGCDetails -XX:+PrintGCDateStamps -Xloggc:/var/log/hazelcast/gc.log
```

`-Xlog:gc` works, but `-Xlog:gc*` also logs the G1 region size and the humongous regions at each GC, and the
report charts those too. A log with the uptime only, and no `time` decoration, can't be placed in time.

A GC log goes with the member whose diagnostics or member logs are in the same folder or the nearest one. When
several members are as near, the tool picks the member whose lifetime started with that JVM, comparing the GC log's
wall time minus its uptime. If that doesn't settle it, a host or port in the file name decides. A GC log that
matches no member gets a lane of its own, and a folder of GC logs alone makes a report too. Rotated files and
copies of the same log are read once. The GC tab lists every file and how it was matched.

## Options

| Option | |
|---|---|
| `input_dir` | Folder with the diagnostics files, member logs and/or GC logs (searched recursively, `.gz` too) |
| `output_dir` | Where `report.html` goes (default `./hz-diag-report`) |
| `-m`, `--member HOST[:PORT]` | Only these members (repeatable). A folder name works for files without an address |
| `--from TIME`, `--to TIME` | Only data in this window, in the time shown in the logs: `27-10-2026 15:00`, `2026-10-27 15:00:00`, `15:00` (the first day), or a date |
| `--points max\|N` | `max` (default) keeps every metric and profiler dump. `N` merges them into about N points across the time range, for a smaller file |
| `--top N` | Per metric, chart data for the N most active maps, threads, pipelines, AtomicLongs... (default 40). The others still count in the totals |
| `--budget N` | At most about N values for the per-item lines. Past that they get coarser and the rest keeps its resolution (default 8,000,000; 0 means no limit) |
| `--open` | Open the report in a browser when it's done |
| `-q`, `--quiet` | Don't print progress |

## The report

Everything follows the toolbar: the **Member** list, **Lines** (one line per member, or per map, thread, operation
and so on), **Find**, the time range and the theme.

- **Zoom:** drag across any chart; double-click or *Zoom out* to go back.
- **Measure:** switch *Drag to* to *Measure*, or hold Shift and drag. Every chart then shows what went through that
  span: bytes for byte counters, counts for other counters, average and max for sizes. You can also type the start
  and end times.
- **Pickers:** what a tab shows is picked in a row of the bar that stays at the top of the page, for example an
  operation, a WAN publisher, a CP group, a kind of data structure, a metric or a kind of GC pause.
- **Tables:** every chart has a *Table* button that lists its values.

### Diagnostics tabs

| Tab | What it shows |
|---|---|
| **Overview** | Members and restarts, membership changes, slow executions, slow operations, stuck threads, saturated executors, OutOfMemoryErrors, longest GC pause, heap use, late heartbeats, and what happened when |
| **Latency** | From the operation and invocation profilers: calls per latency group, average and max latency, when each operation was slow, and a table per operation and member. You set the latency groups and the slow threshold, from 1 ms to over 8 s |
| **Slow operations** | What the slow operation detector reported: each slow invocation once, as a bar from its start to its end, with its stack trace and details. Invocations still running are marked |
| **Operations** | Operation, response and executor queues, pending invocations, and executors whose pool was full while tasks waited (saturated) |
| **Threads** | Stuck IO and operation threads: idle while work waited in their queue, at two dumps in a row or more. Per-thread metrics too |
| **Network** | Bytes in and out, connections and connection pipelines |
| **Transfer** | How much each member received from and sent to each peer (other members, client hosts, WAN targets), from the per-connection counters |
| **WAN** | WAN replication per publisher: outbound queues, events published, acknowledgements, failures, sync and consistency checks, WAN bytes and WAN operations |
| **Data structures** | Maps, multimaps, replicated maps, caches, queues, topics, ringbuffers, executors, PN counters... one kind and one metric at a time, with a table of every structure and every metric of the one you click |
| **System** | Heap, GC time from the metrics, CPU, load, threads, file descriptors, memory |
| **GC** | From the GC logs: every pause as a dot (young and mixed; remark, cleanup and others; caused by a humongous allocation), full GCs and evacuation failures, GCs caused by humongous allocations over time, humongous regions, heap after GC, time in pauses, the longest pauses and the GC log files |
| **Cluster** | Cluster size as each member saw it, member list changes and every event |
| **CP Subsystem** | Raft groups (term, commits, log, snapshots, leader elections), CP members, CP objects (AtomicLong, AtomicReference, FencedLock, semaphore, CountDownLatch, CPMap, sessions) and CP operations. One CP group can be picked |
| **Other** | Every metric that fits none of the other tabs |

### Member logs tabs

When member logs are found, a *Source* switch adds these tabs:

| Tab | What it shows |
|---|---|
| **Overview** | Warnings and errors over time, and the lines that matter: OutOfMemoryError, thread starvation and clock leaps, clock jumps, suspected members, split-brain protection failures, slow and delayed operations, closed connections, lifecycle and membership |
| **Health monitor** | The HealthMonitor lines, charted |
| **Exceptions** | Every exception, grouped by class and root cause, over time, with the stack trace of the first one |
| **CP Subsystem** | CP groups, Raft roles and terms, elections, sessions and persistence, from the log lines |

## How the numbers are read

- **Counters** that only grow, such as `map.getCount` or `tcp.bytesReceived`, are charted as a rate per second
  between two dumps of the same lifetime. A drop, from a restart, is skipped rather than drawn as a negative rate.
- **Sizes**, such as used heap or queue sizes, are charted as the highest value in each time bin.
- **Unchanged metrics:** a metric that never changed is listed in the *Unchanged* table of its tab instead of drawn
  as a flat line. A data structure or thread whose value never changed is left out of that metric.
- **Profilers:** the operation and invocation profilers count calls in buckets that double in size (powers of 2 µs).
  The report keeps every bucket from 1 ms up for every interval, so the latency groups can be changed in the report.
- **Times** are the members' server time when every member logs in the same time zone, and UTC when they don't.
  With member logs or GC logs alone, times are shown as printed.
- **Resolution:** with `--points max`, every dump is in the report. When there are more points than pixels,
  neighbouring points are merged for drawing, and zooming in shows them all.

The *How this works* section at the bottom of every report has the details.

## Tips

- **Large captures:** use `--points` and `--budget` for a smaller report.
- **More items:** raise `--top` to keep chart lines for more maps, threads or AtomicLongs.
- **Per-connection details:** some need the debug metric level (`-Dhazelcast.diagnostics.metric.level=debug`), for
  example the type of each connection on the Transfer tab.
- **Long captures:** keep the members' rolled files for the whole period you need. Older files are deleted when
  the maximum number of rolled files is reached (`hazelcast.diagnostics.max.rolled.file.count`).
