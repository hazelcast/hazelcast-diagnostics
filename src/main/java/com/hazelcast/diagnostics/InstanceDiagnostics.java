package com.hazelcast.diagnostics;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.FileReader;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.util.AbstractMap;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.SortedMap;
import java.util.TreeMap;

public class InstanceDiagnostics {

    public static final String METRIC_OPERATION_INVOCATIONS_PENDING = "[unit=count,metric=operation.invocations.pending]";
    public static final String METRIC_OPERATION_QUEUE_SIZE = "[unit=count,metric=operation.queueSize]";
    public static final String METRIC_RUNTIME_USED_MEMORY = "[metric=runtime.usedMemory]";
    public static final String METRIC_OPERATION_INVOCATIONS_LAST_CALL_ID = "[unit=count,metric=operation.invocations.lastCallId]";
    public static final String METRIC_OPERATION_COMPLETED_COUNT = "[unit=count,metric=operation.completedCount]";
    public static final String METRIC_MEMORY_USED_HEAP = "[unit=bytes,metric=memory.usedHeap]";
    public static final String METRIC_MEMORY_MAX_HEAP = "[unit=bytes,metric=memory.maxHeap]";
    public static final String METRIC_OS_PROCESS_CPU_LOAD = "[metric=os.processCpuLoad]";
    public static final String METRIC_RUNTIME_TOTAL_MEMORY = "[metric=runtime.totalMemory]";

    private final File directory;
    private final Set<String> availableMetrics = new HashSet<>();

    private long startMs = Long.MAX_VALUE;
    private long endMs = Long.MIN_VALUE;

    private final SortedMap<Long, DiagnosticsIndex> metricsIndices = new TreeMap<>();

    public InstanceDiagnostics(File directory) {
        this.directory = directory;
    }

    public Set<String> getAvailableMetrics() {
        return availableMetrics;
    }

    public File getDirectory() {
        return directory;
    }

    public InstanceDiagnostics analyze() {
        try {
            List<DiagnosticsFile> diagnosticsFiles = diagnosticsFiles();
            for (DiagnosticsFile diagnosticsFile : diagnosticsFiles) {
                analyze(diagnosticsFile);
            }
            return this;
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }

    private void analyze(DiagnosticsFile file) throws IOException {
        FileReader fr = new FileReader(file.file);
        int depth = 0;
        int offset = 0;
        int startOffset = 0;
        char[] buffer = new char[1024];
        StringBuilder sb = new StringBuilder();

        for (; ; ) {
            int read = fr.read(buffer);
            if (read == -1) {
                break;
            }
            for (int k = 0; k < read; k++) {
                char c = buffer[k];
                sb.append(c);
                if (c == '\n') {
                    if (depth == 0) {
                        startOffset = offset;
                    }
                } else if (c == '[') {
                    depth++;
                } else if (c == ']') {
                    depth--;
                    if (depth == 0) {
                        analyze(sb, file, offset, startOffset);
                        sb.setLength(0);
                    }
                }
                offset++;
            }
        }
    }

    private void analyze(StringBuilder sb, DiagnosticsFile file, int offset, int startOffset) {
        int spaces = 0;
        long timestamp = 0;
        int k;
        for (k = 0; k < sb.length(); k++) {
            char c = sb.charAt(k);
            if (c == ' ') {
                spaces++;
            } else if (spaces == 2) {
                timestamp = timestamp * 10 + Character.getNumericValue(c);
            } else if (spaces == 3) {
                break;
            }
        }

        int closeType = sb.indexOf("[");
        DiagnosticType type;
        if (closeType == -1) {
            type = DiagnosticType.TYPE_UNKNOWN;
        } else {
            type = DiagnosticType.from(sb.substring(k, closeType));
        }

        if (type == DiagnosticType.TYPE_UNKNOWN) {
            System.out.println("------------------------------------");
            System.out.println(sb);
            System.out.println("------------------------------------");
        }

        if (timestamp > file.endMs) {
            file.endMs = timestamp;
        }
        if (timestamp > endMs) {
            endMs = timestamp;
        }
        if (timestamp < file.startMs) {
            file.startMs = timestamp;
        }
        if (timestamp < startMs) {
            startMs = timestamp;
        }

        DiagnosticsIndexEntry fragment = new DiagnosticsIndexEntry(file, startOffset, (offset - startOffset) + 1);

        DiagnosticKey diagnosticKey;
        if (DiagnosticType.TYPE_METRIC.equals(type)) {
            int indexLastEquals = sb.lastIndexOf("=");
            int indexFirstSquareBracket = sb.indexOf("[");
            String metricName = sb.substring(indexFirstSquareBracket + 1, indexLastEquals).intern();
            availableMetrics.add(metricName);
            diagnosticKey = new DiagnosticKey(type, metricName);
        } else {
            diagnosticKey = new DiagnosticKey(type);
        }

        DiagnosticsIndex index = metricsIndices.get(timestamp);
        if (index == null) {
            index = new DiagnosticsIndex();
            metricsIndices.put(timestamp, index);
        }
        index.add(diagnosticKey, fragment);
    }

    private List<DiagnosticsFile> diagnosticsFiles() {
        List<DiagnosticsFile> files = new ArrayList<>();
        for (File file : directory.listFiles()) {
            String name = file.getName();
            if (name.startsWith("diagnostics-")) {
                files.add(new DiagnosticsFile(file));
            }
        }
        return files;
    }

    public long startMs() {
        return startMs;
    }

    public long endMs() {
        return endMs;
    }

    public Iterator<Map.Entry<Long, String>> between(DiagnosticType type, long startMs, long endMs) {
        return new IteratorImpl(type, startMs, endMs);
    }

    public Iterator<Map.Entry<Long, Number>> metricsBetween(String metricName, long startMs, long endMs) {
        return new LongMetricsIterator(fixMetricName(metricName), startMs, endMs);
    }

    public String fixMetricName(String metricName) {
        if (availableMetrics.contains(metricName)) {
            return metricName;
        }
        switch (metricName) {
            case METRIC_OS_PROCESS_CPU_LOAD:
                return "os.processCpuLoad";
            case METRIC_RUNTIME_USED_MEMORY:
                return "runtime.usedMemory";
            case METRIC_OPERATION_COMPLETED_COUNT:
                return "operation.completedCount";
            case METRIC_OPERATION_INVOCATIONS_PENDING:
                return "operation.invocations.pending";
            case METRIC_OPERATION_QUEUE_SIZE:
                return "operation.queueSize";
            case METRIC_OPERATION_INVOCATIONS_LAST_CALL_ID:
                return "operation.invocations.lastCallId";
        }
        return metricName;
    }

    public enum DiagnosticType {
        TYPE_UNKNOWN("!!Unknown!!"),
        TYPE_METRIC("Metric"),
        TYPE_BUILD_INFO("BuildInfo"),
        TYPE_SYSTEM_PROPERTIES("SystemProperties"),
        TYPE_CONFIG_PROPERTIES("ConfigProperties"),
        TYPE_SLOW_OPERATIONS("SlowOperations"),
        TYPE_INVOCATIONS("Invocations"),
        TYPE_INVOCATION_PROFILER("InvocationProfiler"),
        TYPE_OPERATION_PROFILER("OperationsProfiler"),
        TYPE_OPERATION_THREAD_SAMPLES("OperationThreadSamples"),
        TYPE_CONNECTION("ConnectionAdded", "ConnectionRemoved"),
        TYPE_HAZELCAST_INSTANCE("HazelcastInstance"),
        TYPE_MEMBER("MemberAdded", "MemberRemoved"),
        TYPE_CLUSTER_VERSION_CHANGE("ClusterVersionChanged"),
        TYPE_LIFECYCLE("Lifecycle"),
        TYPE_WAN("WAN"),
        TYPE_HEARTBEAT("OperationHeartbeat");

        private final String[] logPrefixes;

        private static final Map<String, DiagnosticType> byPrefix = new HashMap<>();

        DiagnosticType(String... logPrefixes) {
            this.logPrefixes = logPrefixes;
        }

        static {
            for (DiagnosticType value : DiagnosticType.values()) {
                for (String logPrefix : value.logPrefixes) {
                    if (byPrefix.put(logPrefix, value) != null) {
                        throw new IllegalArgumentException("Duplicate logPrefix! " + logPrefix);
                    }
                }
            }
        }

        public static DiagnosticType from(String prefix) {
            return byPrefix.getOrDefault(prefix, TYPE_UNKNOWN);
        }
    }

    private class LongMetricsIterator implements Iterator<Map.Entry<Long, Number>> {
        private final Iterator<Map.Entry<Long, DiagnosticsIndex>> iterator;
        private final DiagnosticKey diagnosticKey;
        private Map.Entry<Long, DiagnosticsIndex> entry;

        public LongMetricsIterator(String metricName, long startMs, long endMs) {
            this.diagnosticKey = new DiagnosticKey(DiagnosticType.TYPE_METRIC, metricName);
            iterator = metricsIndices.subMap(startMs, endMs).entrySet().iterator();
        }

        @Override
        public boolean hasNext() {
            if (entry != null) {
                return true;
            }
            for (; ; ) {
                if (iterator.hasNext()) {
                    Map.Entry<Long, DiagnosticsIndex> e = iterator.next();
                    DiagnosticsIndexEntry indexEntry = e.getValue().metricsMap.get(diagnosticKey);
                    if (indexEntry != null) {
                        entry = e;
                        return true;
                    }
                } else {
                    return false;
                }
            }
        }

        @Override
        public Map.Entry<Long, Number> next() {
            Map.Entry<Long, DiagnosticsIndex> next = entry;
            DiagnosticsIndex index = next.getValue();
            DiagnosticsIndexEntry indexEntry = index.metricsMap.get(diagnosticKey);

            String s = indexEntry.file.load(indexEntry.offset, indexEntry.length);
            int indexOfLastEquals = s.lastIndexOf('=');
            String value = s.substring(indexOfLastEquals + 1).replace("]", "");
            int indexDot = value.indexOf('.');
            Number n;
            if (indexDot == -1) {
                n = Long.parseLong(value);
            } else {
                n = Double.parseDouble(value);
            }
            entry = null;
            return new AbstractMap.SimpleEntry<>(next.getKey(), n);
        }
    }

    private class IteratorImpl implements Iterator<Map.Entry<Long, String>> {
        private Map.Entry<Long, DiagnosticsIndex> entry;
        private final Iterator<Map.Entry<Long, DiagnosticsIndex>> iterator;
        private final DiagnosticKey diagnosticKey;

        public IteratorImpl(DiagnosticType type, long startMs, long endMs) {
            this.diagnosticKey = new DiagnosticKey(type);
            this.iterator = metricsIndices.subMap(startMs, endMs).entrySet().iterator();
        }

        @Override
        public boolean hasNext() {
            if (entry != null) {
                return true;
            }
            for (; ; ) {
                if (iterator.hasNext()) {
                    Map.Entry<Long, DiagnosticsIndex> e = iterator.next();
                    DiagnosticsIndexEntry indexEntry = e.getValue().metricsMap.get(diagnosticKey);
                    if (indexEntry != null) {
                        entry = e;
                        return true;
                    }
                } else {
                    return false;
                }
            }
        }

        @Override
        public Map.Entry<Long, String> next() {
            DiagnosticsIndexEntry indexEntry = entry.getValue().metricsMap.get(diagnosticKey);
            String value = indexEntry.file.load(indexEntry.offset, indexEntry.length);
            Long key = entry.getKey();
            entry = null;
            return new AbstractMap.SimpleEntry<>(key, value);
        }
    }

    private static class DiagnosticsFile {
        private final File file;
        private final RandomAccessFile randomAccessFile;
        private long startMs = Long.MIN_VALUE;
        private long endMs = Long.MAX_VALUE;

        public DiagnosticsFile(File file) {
            this.file = file;
            try {
                randomAccessFile = new RandomAccessFile(file, "r");
            } catch (FileNotFoundException e) {
                throw new RuntimeException(e);
            }
        }

        public String load(int offset, int length) {
            byte[] bytes = new byte[length];
            try {
                randomAccessFile.seek(offset);
                if (randomAccessFile.read(bytes, 0, length) != length) {
                    throw new RuntimeException();
                }
                return new String(bytes);
            } catch (IOException e) {
                throw new RuntimeException(e);
            }
        }
    }

    private static class DiagnosticKey {
        private final DiagnosticType type;
        private final String name;

        private DiagnosticKey(DiagnosticType type, String name) {
            this.type = type;
            this.name = name;
        }

        private DiagnosticKey(DiagnosticType type) {
            this(type, null);
        }

        @Override
        public boolean equals(Object o) {
            if (this == o) {
                return true;
            }
            if (o == null || getClass() != o.getClass()) {
                return false;
            }
            DiagnosticKey that = (DiagnosticKey) o;
            return type == that.type && Objects.equals(name, that.name);
        }

        @Override
        public int hashCode() {
            return Objects.hash(type, name);
        }

        @Override
        public String toString() {
            return "DiagnosticKey{" + "type=" + type + ", name='" + name + '\'' + '}';
        }
    }

    private static class DiagnosticsIndex {
        private final Map<DiagnosticKey, DiagnosticsIndexEntry> metricsMap = new HashMap<>();

        public void add(DiagnosticKey key, DiagnosticsIndexEntry indexEntry) {
            metricsMap.put(key, indexEntry);
        }
    }

    private static class DiagnosticsIndexEntry {
        private final DiagnosticsFile file;
        private final int offset;
        private final int length;

        private DiagnosticsIndexEntry(DiagnosticsFile file, int offset, int length) {
            this.file = file;
            this.offset = offset;
            this.length = length;
        }

        @Override
        public String toString() {
            return "DiagnosticsIndexEntry{" + "file=" + file.file + ", offset=" + offset + ", length=" + length + '}';
        }
    }
}