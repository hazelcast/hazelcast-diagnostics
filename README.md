# Hazelcast Diagnostics Analyzer

`hz_diag_parser.py` reads a folder of Hazelcast diagnostics files, from one member or a whole cluster, and builds
**one interactive HTML report**. It also reads what else is in the folder: the members' logs and GC logs, the
clients' diagnostics and logs, thread dumps and heap dumps.

The report is a single self-contained file. The data is embedded and compressed, so the report works offline in
any current browser and can be attached to a ticket or sent by mail.

- Python 3.6 or later, nothing else to install.
- Diagnostics files, member logs and GC logs, plain or `.gz`, from any number of members and over several days.
- Clients' diagnostics and logs, kept apart from the members. Thread dumps and heap dumps, on the same timeline.
- Charts you can zoom, filter by member and search. Measure mode gives the amount that went through any time span.
- Light and dark themes. Works on a phone.

See [`examples/sample-report.html`](examples/sample-report.html) for a report on a synthetic three-member cluster.

A second script, `hz_diag_agent.py`, does the same reading and writes it for an AI agent instead: a Markdown digest
with the findings ranked, and JSON files with the details. See [For an AI agent](#for-an-ai-agent-hz_diag_agentpy).

## Quick start

```bash
python3 hz_diag_parser.py /path/to/diagnostics out/
open out/report.html            # or xdg-open on Linux, or double-click the file
```

The input folder is searched recursively. Files that are neither diagnostics, member or client logs, GC logs, thread
dumps nor heap dumps are skipped and listed. The console prints a summary with the members, the time range, stuck
threads, saturated executors, slow operations, OutOfMemoryErrors, GC pauses, the dumps and more.

```bash
# only two members, and one hour
python3 hz_diag_parser.py diag/ out/ --member 10.0.0.1:5701 --member 10.0.0.2 \
    --from "27-10-2026 15:00" --to "27-10-2026 16:00"

# a smaller report for a long capture (about 2,000 points across the time range)
python3 hz_diag_parser.py diag/ out/ --points 2000
```

## What to collect

Put everything for a cluster under one folder. A subfolder per member works best, because member logs, GC logs and
dumps can be matched to a member by the folder they are in.

```
case-1234/
  member1/   diagnostics-10.0.0.1_5701-1793075400000-000.log ...   hazelcast.log   gc.log  gc.log.0   threads-20261027-153300.txt
  member2/   diagnostics-10.0.0.2_5701-1793075420000-000.log ...   hazelcast.log   gc.log
  member3/   ...
  client1/   diagnostics-client-0-1793075500000-000.log ...   client.log   java_pid4242.hprof
```

### Diagnostics files

Diagnostics are written by every member when they are turned on, for example:

```
-Dhazelcast.diagnostics.enabled=true
-Dhazelcast.diagnostics.directory=/var/log/hazelcast
-Dhazelcast.diagnostics.operation-profiler.period.seconds=5
-Dhazelcast.diagnostics.invocation-profiler.period.seconds=5
-Dhazelcast.diagnostics.storeLatency.period.seconds=60
-Dhazelcast.diagnostics.invocation.sample.period.seconds=60
```

The metrics and the slow operation detector are on by default. They are written every minute
(`hazelcast.diagnostics.metrics.period.seconds`, `hazelcast.diagnostics.slowoperations.period.seconds`). The
operation and invocation profilers are off until their period is set, and the Latency tab needs them. The store
latency plugin is off too: `hazelcast.diagnostics.storeLatency.period.seconds` turns it on and gives the MapStore
tab, which shows how long each map's MapStore and MapLoader calls took. So is the invocation sampler:
`hazelcast.diagnostics.invocation.sample.period.seconds` turns it on and gives the Invocations tab, with the
invocations each member was waiting on at each sample (`hazelcast.diagnostics.invocation.slow.threshold.seconds`,
5 s by default, sets which ones it lists as slow).

A member is identified by its address, taken from the file name or from `thisAddress` in the file. The start time
in the file name separates its lifetimes: when a member restarts, its counters start again from zero and the report
doesn't mix them. The same file found twice is read once.

### Member logs (optional)

Add the members' own logs: `hazelcast.log`, your application's log when Hazelcast logs into it, console output,
rotated or `.gz`. These layouts are recognised:

- java.util.logging, log4j, log4j2 and logback, with an ISO-8601 or day-first date and time at the start of the line
- a date and time after a level or a prefix (`INFO  [2026-11-02 01:30:06,766] ...`), or with the month in letters
- JSON lines (logstash encoder, ECS, log4j2 JSON layout)
- the time of day alone, when the date is in the file name (`20261102-app.log`)
- console output with terminal colours

A file is read as a member log when it has a Hazelcast line (`[host]:port [cluster] [version]`) somewhere in its
first 512 MB, so a partial log, without the member's start or shutdown, works too. A log is matched to a member by
the address in its lines. A folder with member logs only, and no diagnostics, works too. The files that are not read
are listed with the reason, in the console and in the report's *Members and files*.

In an application's log, only Hazelcast's own lines (with its `[host]:port [cluster] [version]` prefix, or from a
Hazelcast logger) make the notable lines and the migrations: an application line about a SQL `merge into` or a
database connection is not a split brain or a closed member connection. OutOfMemoryError, thread starvation and clock
jumps count from any line, as they are about the whole JVM.

### Clients (optional)

A client writes diagnostics too when they are turned on in its JVM (the same `hazelcast.diagnostics.*` properties),
in files named `diagnostics-client-<id>-<start>-NNN.log`. Its log lines start with the client's instance name instead
of an address: `hz.client_1 [dev] [5.5.0] ...`.

Both are read as a client, not a member. A client's diagnostics files (by their name, or by the client-only metrics
such as `invocations.pendingCalls`) and its logs go together by its instance name, on a line of their own: the
*Clients* group of the *Member* list, "client hz.client_1". The title, the *Members* count and the cluster size count
the members only ("Hazelcast cluster: 2 members + 1 client"), and the Transfer tab, which is between members, leaves
the client out. Its metrics (heap, CPU, GC...) and its log lines are charted with the members', in a colour of its own.

### GC logs (optional)

Add the members' GC logs. A date and time on every line is best:

```
# JDK 9 and later
-Xlog:gc*:file=/var/log/hazelcast/gc.log:time,uptime:filecount=10,filesize=50m

# JDK 8
-XX:+PrintGCDetails -XX:+PrintGCDateStamps -Xloggc:/var/log/hazelcast/gc.log
```

`-Xlog:gc` works, but `-Xlog:gc*` also logs the G1 region size and the humongous regions at each GC, and the
report charts those too.

Without the `time` decoration, as with `-Xlog:gc*:file=gc.log` (its default decorations are `uptime,level,tags`),
each line has the JVM uptime only, such as `[3829.549s][info][gc] GC(13) Pause Young (Normal) ...`. The report then
places the log with its member's diagnostics:

- The `runtime.uptime` metric gives the JVM start of each of the member's lifetimes: the dump time minus the uptime.
- Each JVM run in the log (after a restart, the uptime starts again from 0) goes to the lifetime whose GC count
  (`gc.minorCount`) matches its young pauses.
- A run that matches no lifetime, for example a JVM whose diagnostics aren't there, is listed but not charted.

With GC logs alone and the uptime only, the time axis is the JVM uptime (0 is the JVM start), with one line per JVM.
The *Times from* column of the GC log files table says how each file was placed.

A GC log is matched to a member in this order:

1. The member whose diagnostics or member logs are in the same folder.
2. The member whose host name (and port) is in the GC log's file name, then in its folder names, for example
   `gc_logs_uklpapscl101a_20261102/gc.log`.
3. The member whose files are in the nearest folder. When several are as near, the one whose lifetime started with
   that JVM, comparing the GC log's wall time minus its uptime.
4. For a log with the uptime only that none of these matched, the one member whose GC counts match its pauses.

A GC log that matches no member still gets its own line on the GC tab, but it isn't counted as a member: the title
shows it apart ("3 members + 1 GC log not linked to a member"). A folder of GC logs alone makes a report too.
Rotated files and copies of the same log are read once. The GC tab lists every file and how it was matched.

### Thread dumps and heap dumps (optional)

Add the thread dumps and heap dumps taken during the incident, from the members or the clients:

```bash
jstack -l <pid> > threads-$(date +%Y%m%d-%H%M%S).txt      # or: jcmd <pid> Thread.print, or kill -3 <pid> (console output)
jcmd <pid> GC.heap_dump /var/log/hazelcast/heap-$(date +%Y%m%d-%H%M%S).hprof
-XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/var/log/hazelcast
```

**Thread dumps** are found in files of their own and inside console output and member logs (the output of
`kill -3`). These formats are read: jstack (with or without `-l`), `jcmd Thread.print`, `jcmd Thread.dump_to_file`
(text or JSON), and the plain format applications write from `Thread.getAllStackTraces()` (a
`"name" daemon=false state=WAITING` line, then `at ...` lines). A file can hold several dumps.

**Heap dumps** are HPROF files: `.hprof`, gzipped (`jcmd GC.heap_dump -gz=1`, or `.hprof.gz`), or with no extension.

**When** each was taken, in this order: the date line jstack, jcmd and `kill -3` print above the dump; the time
written in the dump (`Thread.dump_to_file` and HPROF dumps have one, in UTC, moved to the members' time zone); a date
and time in the file name (`threads-20261112-152146.txt`, taken as local time). Name the files with the date and time
when your tool prints none.

**Whose** it is, in this order: the member or client whose threads have the same names (`hz.<instance name>.`...,
taken from the thread tags in the metrics and the threads in the logs); the one whose file names have every word of
the dump's file name (`member-2.out` goes with `member-2-diagnostics-...`); the only one with files in the same
folder; for a heap dump, the one of a thread dump taken with it (same file name words, within 2 minutes). A dump
that matches none is listed as *not matched to a member*.

Each dump is a dotted line on every time chart ("3 thread dumps + 1 heap dump" when they are close), a row of
*What happened* on the Overview, and a row of the **Dumps** tab. There, a thread dump shows:

- the threads by state, and how many are doing something (not waiting for work in a pool, a queue or a selector)
- the threads with the same stack (same state, same top 12 frames), the busy ones first, each with its stack
- the contended locks: who holds each one and who waits for it
- a deadlock, as the JVM reported it, or found from the monitors the threads hold and wait for
- the threads by name (one line per pool), the threads that used the most CPU, and the whole dump

A heap dump shows the objects and classes, the classes by shallow size and by number of instances, the largest
objects, the Hazelcast classes and the GC roots. A shallow size is an object's own size, estimated from its fields
(with compressed references when the object ids allow them), not what it keeps alive: for retained sizes, leak
suspects and the paths to the GC roots, open the dump in Eclipse MAT or VisualVM. Reading a heap dump stops after
`--heap-seconds` (120 s by default): the tables then cover the part read, and `--heap-seconds 0` keeps its time only.

## Options

| Option | |
|---|---|
| `input_dir` | Folder with the diagnostics files, member and client logs, GC logs, thread and heap dumps (searched recursively, `.gz` too) |
| `output_dir` | Where `report.html` goes (default `./hz-diag-report`) |
| `-m`, `--member HOST[:PORT]` | Only these members (repeatable). A client by its name (`hz.client_1`), or `client` for every client. A folder name works for files without an address |
| `--from TIME`, `--to TIME` | Only data in this window, in the time shown in the logs: `27-10-2026 15:00`, `2026-10-27 15:00:00`, `15:00` (the first day), or a date |
| `--points max\|N` | `max` (default) keeps every metric and profiler dump. `N` merges them into about N points across the time range, for a smaller file |
| `--top N` | Per metric, chart data for the N most active maps, threads, pipelines, AtomicLongs... (default 40). The others still count in the totals |
| `--budget N` | At most about N values for the per-item lines. Past that they get coarser and the rest keeps its resolution (default 8,000,000; 0 means no limit) |
| `--heap-seconds N` | At most N seconds reading each heap dump for its class histogram (default 120). Past that, the histogram covers what was read. `0` keeps only the time it was taken |
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
| **Overview** | Members and restarts, membership changes, slow executions, slow operations, stuck threads, saturated executors, OutOfMemoryErrors, longest GC pause, heap use, late heartbeats, thread and heap dumps, and what happened when |
| **Latency** | From the operation and invocation profilers: calls per latency group, average and max latency, when each operation was slow, and a table per operation and member. You set the latency groups and the slow threshold, from 1 ms to over 8 s |
| **Slow operations** | What the slow operation detector reported: each slow invocation once, as a bar from its start to its end, with its stack trace and details. Invocations still running are marked |
| **Invocations** | From the invocation sampler: how many invocations each member was waiting on at each sample, by operation, and how many of them for longer than the slow threshold; every invocation listed as slow, once, with its data structure, partition, target and how long it stayed pending, and which ones went past their call timeout; the partitions with the most (and their partition thread on the target). Only when the sampler is on |
| **Operations** | Operation, response and executor queues, pending invocations, and executors whose pool was full while tasks waited (saturated) |
| **Threads** | Stuck IO and operation threads: idle while work waited in their queue, at two dumps in a row or more. Per-thread metrics too |
| **Dumps** | The thread dumps and heap dumps found: when each was taken and whose it is. For a thread dump, the threads with the same stack, contended locks, deadlocks, thread names, CPU and the whole dump; for a heap dump, the classes by shallow size and by instances, the largest objects, the Hazelcast classes and the GC roots. Find searches their text and class names. Only when there are dumps |
| **Network** | Bytes in and out, connections and connection pipelines |
| **Transfer** | How much each member received from and sent to each peer (other members, client hosts, WAN targets), from the per-connection counters |
| **WAN** | WAN replication per publisher: outbound queues, events published, acknowledgements, failures, sync and consistency checks, WAN bytes and WAN operations |
| **Data structures** | Maps, multimaps, replicated maps, caches, queues, topics, ringbuffers, executors, PN counters... one kind and one metric at a time, with a table of every structure and every metric of the one you click |
| **MapStore** | How long each map's MapStore and MapLoader methods took (load, loadAll, loadAllKeys, store, storeAll, delete, deleteAll): calls per latency group, average and max latency and when calls were slow, per map and method, plus the totals since each member started with each method's calls by duration. Queue, ringbuffer and JCache stores appear too when the diagnostics have them. Only when the store latency plugin is on |
| **System** | Heap, GC time from the metrics, CPU, load, threads, file descriptors, memory |
| **GC** | From the GC logs: every pause as a dot (young and mixed; remark, cleanup and others; caused by a humongous allocation), full GCs and evacuation failures, GCs caused by humongous allocations over time, humongous regions, heap after GC, time in pauses, the longest pauses and the GC log files |
| **Cluster** | Cluster size as each member saw it, member list changes and every event |
| **CP Subsystem** | Raft groups (term, commits, log, snapshots, leader elections), CP members, CP objects (AtomicLong, AtomicReference, FencedLock, semaphore, CountDownLatch, CPMap, sessions) and CP operations. One CP group can be picked |
| **Configuration** | From the BuildInfo, SystemProperties and ConfigProperties sections: Hazelcast and Java versions, OS, heap and GC per member, and every Hazelcast property, system property and JVM argument side by side, with what differs between the members and what changed at a restart |
| **Other** | Every metric that fits none of the other tabs |

### Member logs tabs

When member logs (or client logs) are found, a *Source* switch adds these tabs:

| Tab | What it shows |
|---|---|
| **Overview** | Warnings and errors over time, and the lines that matter: OutOfMemoryError, partitions lost (`partitionId=N is completely lost!`), WAN replication problems (event queue full and events dropped, failures, warnings and errors of the WAN classes), thread starvation and clock leaps, clock jumps, suspected members, split-brain protection failures, slow and delayed operations, closed connections, lifecycle and membership. Lost partitions and WAN problems are also on the diagnostics Overview, and WAN problems on the WAN tab |
| **Health monitor** | The HealthMonitor lines, charted |
| **Exceptions** | Every exception, grouped by class and root cause, over time, with the stack trace of the first one |
| **Migrations** | What the master logged about migrations: each migration plan (`Repartitioning cluster data`), its progress (`Remaining migration tasks`) and how it ended (completed, or failed or aborted and planned again), how long it took; lost partitions, failed migrations and promotions, and partition table changes |
| **CP Subsystem** | CP groups, Raft roles and terms, elections, sessions and persistence, from the log lines |

## How the numbers are read

- **Counters** that only grow, such as `map.getCount` or `tcp.bytesReceived`, are charted as a rate per second
  between two dumps of the same lifetime. A drop, from a restart, is skipped rather than drawn as a negative rate.
- **Sizes**, such as used heap or queue sizes, are charted as the highest value in each time bin.
- **Unchanged metrics:** a metric that never changed is listed in the *Unchanged* table of its tab instead of drawn
  as a flat line. A data structure or thread whose value never changed is left out of that metric.
- **Profilers:** the operation and invocation profilers count calls in buckets that double in size (powers of 2 µs).
  The report keeps every bucket from 1 ms up for every interval, so the latency groups can be changed in the report.
- **Store latency** counters work like the profilers', cumulative since the member started. If
  `hazelcast.diagnostics.storeLatency.reset.period.seconds` is set, the counters are reset after some dumps: a dump
  with lower counters than the one before counts as a reset, and the totals keep what was counted before it.
- **Invocations:** the sampler's History and SlowHistory count, since the member started, one per pending
  invocation at each sample, so the growth between two dumps is the number pending at the later sample. Its list of
  slow invocations stops at 99 per dump (`hazelcast.diagnostics.invocation.slow.max.count`); the counts don't.
- **Times** are the members' server time when every member logs in the same time zone, and UTC when they don't.
  With member logs or GC logs alone, times are shown as printed.
- **Heap dumps:** the sizes are shallow sizes, estimated from each class's fields the way heap analysers do (a
  12-byte header and 4-byte references with compressed oops, else 16 and 8, rounded up to 8 bytes). Retained sizes,
  what an object keeps alive, need a heap analyser such as Eclipse MAT.
- **Resolution:** with `--points max`, every dump is in the report. When there are more points than pixels,
  neighbouring points are merged for drawing, and zooming in shows them all.

The *How this works* section at the bottom of every report has the details.

## For an AI agent: hz_diag_agent.py

`hz_diag_agent.py` reads the same folder the same way (it uses `hz_diag_parser.py`, which must be in the same
folder) and writes a folder for an AI agent instead of a report for a browser:

```bash
python3 hz_diag_agent.py /path/to/diagnostics agent-out/
```

| File | What it holds |
|---|---|
| `summary.md` | The digest, to read first: scope, members, the findings ranked by severity (each with its numbers, its times and the file and key that hold the details), what was checked and found clean, the key events in time order, and a short section per area |
| `findings.json` | The same findings as data: severity, area, members, start and end, facts, where the details are |
| `timeline.jsonl` | Every notable event of every source in time order, one JSON object per line: membership and lifecycle events, restarts, late heartbeats, stuck threads, saturated executors, slow operations and invocations, slow calls, long and full GC pauses, member log lines (OutOfMemoryError, lost partitions, split brain, WAN problems...), migrations, CP Subsystem |
| `cluster.json` | Members, lifetimes and restarts, cluster views, membership events, late heartbeats, the build, JVM and Hazelcast properties and what differs between the members |
| `metrics.json` | Every metric that changed: per member min, mean, max (and when) and last, and a series of at most `--points` values; per metric the most active maps, threads, executors...; the key metrics (heap, CPU, GC time, queues, pending invocations) |
| `latency.json` | Per operation, profiler and member: calls, mean, max, the calls of 131 ms, 524 ms and 1 s or more, the worst intervals; MapStore totals |
| `slow.json` | Slow operations, slow pending invocations, stuck threads, saturated executors |
| `gc.json` | GC pauses per member: percentiles, by kind and cause, the longest, full GCs, evacuation failures, humongous allocations, heap after GC |
| `logs.json` | Member logs: warnings and errors, notable lines, exceptions, OutOfMemoryError, health monitor, migrations, CP Subsystem |
| `network.json` | Bytes received and sent per member and peer, the busiest connections |

Only the files with something in them are written. Times are ISO 8601 with the members' UTC offset, units are in
the field names (`_ms`, `_us`, `_bytes`, `_per_s`), and every JSON file starts with `about`, what it holds.

It doesn't know about clients and dumps yet: a client is listed as one more member, and thread and heap dumps are
left out (the HTML report has them).

Why not one big JSON file: an agent does best with a short digest it can read whole, and details it opens only when
it needs them. A dump of every series would not fit in its context, and most of it would be noise. Give an agent
with file access (Claude Code, for example) the whole folder: it reads `summary.md`, then queries the rest with `jq`
or `grep`. In a chat, attach `summary.md`, plus the JSON files it points to if you want the agent to dig.

The findings are heuristics on what was read (a GC pause of 1 s or more, heap use at 90% of the max, an invocation
past its call timeout, a stuck thread...): each one points to the details that confirm it.

| Option | |
|---|---|
| `-m`, `--member`, `--from`, `--to` | As for `hz_diag_parser.py` |
| `--points N` | Values per series in the JSON files (default 60). The stats always use every value; zoom in with `--from`/`--to` for more detail |
| `--top N` | Per metric, the N most active maps, threads, executors... (default 10) |
| `--max-events N` | At most N events in `timeline.jsonl`, the most severe kept first (default 20,000) |
| `--html` | Also write `report.html`, the same report as `hz_diag_parser.py`, from the same reading |

## Tips

- **Large captures:** use `--points` and `--budget` for a smaller report.
- **Big heap dumps** are read as a stream, in little memory, at about 10 s per GB (a 600 MB dump of 15 million
  objects takes about 6 s, plain or `.gz`). Past `--heap-seconds` (120 s), the tables cover the part read; 0 keeps only the
  time they were taken.
- **Thread dumps with no date** (from tools that print none): put the date and time in the file name,
  `threads-20261112-152146.txt`, so they land on the timeline.
- **More items:** raise `--top` to keep chart lines for more maps, threads or AtomicLongs.
- **Per-connection details:** some need the debug metric level (`-Dhazelcast.diagnostics.metric.level=debug`), for
  example the type of each connection on the Transfer tab.
- **Long captures:** keep the members' rolled files for the whole period you need. Older files are deleted when
  the maximum number of rolled files is reached (`hazelcast.diagnostics.max.rolled.file.count`).
