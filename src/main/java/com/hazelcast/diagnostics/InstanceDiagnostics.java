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
import java.util.Set;
import java.util.SortedMap;
import java.util.TreeMap;

public class InstanceDiagnostics {

    public static final String TYPE_UNKNOWN = "__builtin_type-0";
    public static final String TYPE_METRIC = "__builtin_type-1";
    public static final String TYPE_BUILD_INFO = "__builtin_type-2";
    public static final String TYPE_SYSTEM_PROPERTIES = "__builtin_type-3";
    public static final String TYPE_CONFIG_PROPERTIES = "__builtin_type-4";
    public static final String TYPE_SLOW_OPERATIONS = "__builtin_type-5";
    public static final String TYPE_INVOCATIONS = "__builtin_type-6";
    public static final String TYPE_INVOCATION_PROFILER = "__builtin_type-7";
    public static final String TYPE_OPERATION_PROFILER = "__builtin_type-8";
    public static final String TYPE_OPERATION_THREAD_SAMPLES = "__builtin_type-9";
    public static final String TYPE_CONNECTION = "__builtin_type-10";
    public static final String TYPE_HAZELCAST_INSTANCE = "__builtin_type-11";
    public static final String TYPE_MEMBER = "__builtin_type-12";
    public static final String TYPE_CLUSTER_VERSION_CHANGE = "__builtin_type-13";
    public static final String TYPE_LIFECYCLE = "__builtin_type-14";
    public static final String TYPE_WAN = "__builtin_type-15";
    public static final String TYPE_HEARTBEAT = "__builtin_type-16";

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
        for (int k = 0; k < sb.length(); k++) {
            char c = sb.charAt(k);
            if (c == ' ') {
                spaces++;
            } else if (spaces == 2) {
                timestamp = timestamp * 10 + Character.getNumericValue(c);
            }
        }

        String type;
        // very inefficient.
        if (sb.indexOf("Metric[") != -1) {
            type = TYPE_METRIC;
        } else if (sb.indexOf("BuildInfo[") != -1) {
            type = TYPE_BUILD_INFO;
        } else if (sb.indexOf("SystemProperties[") != -1) {
            type = TYPE_SYSTEM_PROPERTIES;
        } else if (sb.indexOf("ConfigProperties[") != -1) {
            type = TYPE_CONFIG_PROPERTIES;
        } else if (sb.indexOf("SlowOperations[") != -1) {
            type = TYPE_SLOW_OPERATIONS;
        } else if (sb.indexOf("Invocations[") != -1) {
            type = TYPE_INVOCATIONS;
        } else if (sb.indexOf("InvocationProfiler[") != -1) {
            type = TYPE_INVOCATION_PROFILER;
        } else if (sb.indexOf("OperationsProfiler[") != -1) {
            type = TYPE_OPERATION_PROFILER;
        } else if (sb.indexOf("ConnectionRemoved[") != -1) {
            type = TYPE_CONNECTION;
        } else if (sb.indexOf("OperationThreadSamples[") != -1) {
            type = TYPE_OPERATION_THREAD_SAMPLES;
        } else if (sb.indexOf("HazelcastInstance[") != -1) {
            type = TYPE_HAZELCAST_INSTANCE;
        } else if (sb.indexOf("MemberRemoved[") != -1) {
            type = TYPE_MEMBER;
        } else if (sb.indexOf("MemberAdded[") != -1) {
            type = TYPE_MEMBER;
        } else if (sb.indexOf("ClusterVersionChanged[") != -1) {
            type = TYPE_CLUSTER_VERSION_CHANGE;
        } else if (sb.indexOf("Lifecycle[") != -1) {
            type = TYPE_LIFECYCLE;
        } else if (sb.indexOf("ConnectionAdded[") != -1) {
            type = TYPE_CONNECTION;
        } else if (sb.indexOf("WAN[") != -1) {
            type = TYPE_WAN;
        } else if (sb.indexOf("OperationHeartbeat[") != -1) {
            type = TYPE_HEARTBEAT;
        } else if (sb.indexOf("MemberHeartbeats[") != -1) {
            type = TYPE_HEARTBEAT;
        } else {
            System.out.println("------------------------------------");
            System.out.println(sb.toString());
            System.out.println("------------------------------------");
            type = TYPE_UNKNOWN;
        }

        if (timestamp > file.endMs) file.endMs = timestamp;
        if (timestamp > endMs) endMs = timestamp;
        if (timestamp < file.startMs) file.startMs = timestamp;
        if (timestamp < startMs) startMs = timestamp;

        DiagnosticsIndexEntry fragment = new DiagnosticsIndexEntry(file, startOffset, (offset - startOffset) + 1);

        String metricName = type;
        if (TYPE_METRIC.equals(type)) {
            int indexLastEquals = sb.lastIndexOf("=");
            int indexFirstSquareBracket = sb.indexOf("[");
            metricName = sb.substring(indexFirstSquareBracket + 1, indexLastEquals).intern();
            availableMetrics.add(metricName);
        }

        DiagnosticsIndex index = metricsIndices.get(timestamp);
        if (index == null) {
            index = new DiagnosticsIndex();
            metricsIndices.put(timestamp, index);
        }
        index.add(metricName, fragment);
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

    public Iterator<Map.Entry<Long, String>> between(String type, long startMs, long endMs) {
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

    private class LongMetricsIterator implements Iterator<Map.Entry<Long, Number>> {
        private final Iterator<Map.Entry<Long, DiagnosticsIndex>> iterator;
        private final String metricName;
        private Map.Entry<Long, DiagnosticsIndex> entry;

        public LongMetricsIterator(String metricName, long startMs, long endMs) {
            this.metricName = metricName;
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
                    DiagnosticsIndexEntry indexEntry = e.getValue().metricsMap.get(metricName);
                    if(indexEntry != null) {
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
            DiagnosticsIndexEntry indexEntry = index.metricsMap.get(metricName);

            String s = indexEntry.file.load(indexEntry.offset, indexEntry.length);
            int indexOfLastEquals = s.lastIndexOf('=');
            String value = s.substring(indexOfLastEquals + 1).replace("]", "");
            int indexDot = value.indexOf('.');
            Number n;
            if(indexDot == -1){
                n = Long.parseLong(value);
            }else{
                n = Double.parseDouble(value);
            }
            entry = null;
            return new AbstractMap.SimpleEntry<>(next.getKey(), n);
        }
    }


    private class IteratorImpl implements Iterator<Map.Entry<Long, String>> {
        private Map.Entry<Long, DiagnosticsIndex> entry;
        private final Iterator<Map.Entry<Long, DiagnosticsIndex>> iterator;
        private final String type;

        public IteratorImpl(String type, long startMs, long endMs) {
            this.type = type;
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
                    DiagnosticsIndexEntry indexEntry = e.getValue().metricsMap.get(type);
                    if(indexEntry != null) {
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
            DiagnosticsIndexEntry indexEntry = entry.getValue().metricsMap.get(type);
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

    private static class DiagnosticsIndex {
        private final Map<String, DiagnosticsIndexEntry> metricsMap = new HashMap<>();
        public void add(String key, DiagnosticsIndexEntry indexEntry) {
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
            return "DiagnosticsIndexEntry{" +
                    "file="+file.file +
                    ", offset=" + offset +
                    ", length=" + length +
                    '}';
        }
    }
}