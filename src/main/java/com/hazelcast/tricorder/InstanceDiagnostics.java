package com.hazelcast.tricorder;

import java.io.BufferedReader;
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
import java.util.TreeMap;

public class InstanceDiagnostics {
    public static final int TYPE_UNKNOWN = 0;
    public static final int TYPE_METRIC = 1;
    public static final int TYPE_BUILD_INFO = 2;
    public static final int TYPE_SYSTEM_PROPERTIES = 3;
    public static final int TYPE_CONFIG_PROPERTIES = 4;
    public static final int TYPE_SLOW_OPERATIONS = 5;
    public static final int TYPE_INVOCATIONS = 6;
    public static final int TYPE_INVOCATION_PROFILER = 7;
    public static final int TYPE_OPERATION_PROFILER = 8;
    public static final int TYPE_OPERATION_THREAD_SAMPLES = 9;
    public static final int TYPE_CONNECTION_REMOVED = 10;
    public static final int TYPE_HAZELCAST_INSTANCE = 11;
    public static final int TYPE_MEMBER_REMOVED = 12;
    public static final int TYPE_MEMBER_ADDED = 13;
    public static final int TYPE_CLUSTER_VERSION_CHANGE = 14;
    public static final int TYPE_LIFECYCLE = 15;
    public static final int TYPE_CONNECTION_ADDED = 16;
    public static final int TYPES = TYPE_CONNECTION_ADDED + 1;

    private File directory;
    private List<DiagnosticsFile> diagnosticsFiles;
    private long startMs = Long.MAX_VALUE;
    private long endMs = Long.MIN_VALUE;
    private Set<String> availableMetrics = new HashSet<>();

    public InstanceDiagnostics(File directory){
        this.directory = directory;
    }

    public File getDirectory() {
        return directory;
    }

    public void analyze() {
        try {
            diagnosticsFiles = diagnosticsFiles();
            for (DiagnosticsFile diagnosticsFile : diagnosticsFiles) {
                analyze(diagnosticsFile);
            }
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }

    private void analyze(DiagnosticsFile file) throws IOException {
        FileReader fr = new FileReader(file.file);

        BufferedReader br = new BufferedReader(fr);
        int depth = 0;
        int offset = 0;
        int startOffset = 0;
        char[] buffer = new char[1024];
        StringBuilder sb = new StringBuilder();

        for (; ; ) {
            int read = br.read(buffer);
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

        int type;
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
            type = TYPE_CONNECTION_REMOVED;
        } else if (sb.indexOf("OperationThreadSamples[") != -1) {
            type = TYPE_OPERATION_THREAD_SAMPLES;
        } else if (sb.indexOf("HazelcastInstance[") != -1) {
            type = TYPE_HAZELCAST_INSTANCE;
        } else if (sb.indexOf("MemberRemoved[") != -1) {
            type = TYPE_MEMBER_REMOVED;
        } else if (sb.indexOf("MemberAdded[") != -1) {
            type = TYPE_MEMBER_ADDED;
        } else if (sb.indexOf("ClusterVersionChanged[") != -1) {
            type = TYPE_CLUSTER_VERSION_CHANGE;
        } else if (sb.indexOf("Lifecycle[") != -1) {
            type = TYPE_LIFECYCLE;
        } else if (sb.indexOf("ConnectionAdded[") != -1) {
            type = TYPE_CONNECTION_ADDED;
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

        DiagnosticsIndexEntry fragment = new DiagnosticsIndexEntry();
        fragment.offset = startOffset;
        fragment.length = (offset - startOffset) + 1;

        if (type == TYPE_METRIC) {
            int indexLastEquals = sb.lastIndexOf("=");
            int indexFirstSquareBracket = sb.indexOf("[");
            String key = sb.substring(indexFirstSquareBracket + 1, indexLastEquals);

            System.out.println(key);
            availableMetrics.add(key);

            DiagnosticsIndex index = file.metricsIndices.get(key);
            if (index == null) {
                index = new DiagnosticsIndex(file);
                file.metricsIndices.put(key, index);
            }
            index.treeMap.put(timestamp, fragment);
        } else {
            file.indices[type].treeMap.put(timestamp, fragment);
        }
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

    public Iterator<Map.Entry<Long, String>> between(int type, long startMs, long endMs) {
        return new IteratorImpl(type, startMs, endMs);
    }

    public Iterator<Map.Entry<Long, Long>> longMetricsBetween(String metricName, long startMs, long endMs) {
        return (Iterator) new LongMetricsIterator(metricName, startMs, endMs, true);
    }

    public Iterator<Map.Entry<Long, Double>> doubleMetricsBetween(String metricName, long startMs, long endMs) {
        return (Iterator) new LongMetricsIterator(metricName, startMs, endMs, false);
    }

    private class LongMetricsIterator implements Iterator<Map.Entry<Long, Number>> {
        private final String name;
        private final long startMs;
        private final long endMs;
        private final boolean isLong;
        private Map.Entry<Long, Number> entry;
        private Iterator<Map.Entry<Long, DiagnosticsIndexEntry>> iterator;
        private Iterator<DiagnosticsFile> diagnosticsFileIterator = diagnosticsFiles.iterator();
        private DiagnosticsFile diagnosticsFile;

        public LongMetricsIterator(String name, long startMs, long endMs, boolean isLong) {
            this.name = name;
            this.startMs = startMs;
            this.endMs = endMs;
            this.isLong = isLong;
        }

        @Override
        public boolean hasNext() {
            if (entry != null) {
                return true;
            }

            for (; ; ) {
                if (iterator != null && iterator.hasNext()) {
                    Map.Entry<Long, DiagnosticsIndexEntry> e = iterator.next();
                    DiagnosticsIndexEntry indexEntry = e.getValue();
                    String s = diagnosticsFile.load(indexEntry.offset, indexEntry.length);
                    int indexOfLastEquals = s.lastIndexOf('=');
                    String value = s.substring(indexOfLastEquals + 1).replace("]", "");
                    Number n;
                    if(isLong) {
                        n = Long.parseLong(value);
                    }else {
                        n = Double.parseDouble(value);
                    }
                    entry = new AbstractMap.SimpleEntry<>(e.getKey(), n);
                    return true;
                }

                if (!diagnosticsFileIterator.hasNext()) {
                    return false;
                }

                diagnosticsFile = diagnosticsFileIterator.next();
                iterator = diagnosticsFile.metricsIndices.get(name).treeMap.subMap(startMs, true, endMs, true).entrySet().iterator();
            }
        }

        @Override
        public Map.Entry<Long, Number> next() {
            if (hasNext()) {
                Map.Entry<Long, Number> tmp = entry;
                entry = null;
                return tmp;
            }
            return null;
        }
    }


    private class IteratorImpl implements Iterator<Map.Entry<Long, String>> {
        private final int type;
        private final long startMs;
        private final long endMs;
        private Map.Entry<Long, String> entry;
        private Iterator<Map.Entry<Long, DiagnosticsIndexEntry>> iterator;
        private Iterator<DiagnosticsFile> diagnosticsFileIterator = diagnosticsFiles.iterator();
        private DiagnosticsFile diagnosticsFile;

        public IteratorImpl(int type, long startMs, long endMs) {
            this.type = type;
            this.startMs = startMs;
            this.endMs = endMs;
        }

        @Override
        public boolean hasNext() {
            if (entry != null) {
                return true;
            }

            for (; ; ) {
                if (iterator != null && iterator.hasNext()) {
                    Map.Entry<Long, DiagnosticsIndexEntry> e = iterator.next();
                    DiagnosticsIndexEntry indexEntry = e.getValue();
                    String value = diagnosticsFile.load(indexEntry.offset, indexEntry.length);
                    entry = new AbstractMap.SimpleEntry<>(e.getKey(), value);
                    return true;
                }

                if (!diagnosticsFileIterator.hasNext()) {
                    return false;
                }

                diagnosticsFile = diagnosticsFileIterator.next();
                iterator = diagnosticsFile.indices[type].treeMap.subMap(startMs, true, endMs, true).entrySet().iterator();
            }
        }

        @Override
        public Map.Entry<Long, String> next() {
            if (hasNext()) {
                Map.Entry<Long, String> tmp = entry;
                entry = null;
                return tmp;
            }
            return null;
        }
    }

    private static class DiagnosticsFile {
        private final DiagnosticsIndex[] indices = new DiagnosticsIndex[TYPES];
        private final Map<String, DiagnosticsIndex> metricsIndices = new HashMap<>();
        private final File file;
        private final RandomAccessFile randomAccessFile;
        private long startMs = Long.MIN_VALUE;
        private long endMs = Long.MAX_VALUE;

        public DiagnosticsFile(File file) {
            this.file = file;
            for (int k = 0; k < indices.length; k++) {
                indices[k] = new DiagnosticsIndex(this);
            }
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
        private final DiagnosticsFile diagnosticsFile;
        private final TreeMap<Long, DiagnosticsIndexEntry> treeMap = new TreeMap<>();

        public DiagnosticsIndex(DiagnosticsFile diagnosticsFile) {
            this.diagnosticsFile = diagnosticsFile;
        }
    }

    private static class DiagnosticsIndexEntry {
        private int offset;
        private int length;

        @Override
        public String toString() {
            return "DiagnosticsIndexEntry{" +
                    "offset=" + offset +
                    ", length=" + length +
                    '}';
        }
    }
}
