package com.hazelcast.tricorder;

import org.jfree.chart.ChartFactory;
import org.jfree.chart.ChartPanel;
import org.jfree.chart.JFreeChart;
import org.jfree.chart.plot.PlotOrientation;
import org.jfree.data.category.CategoryDataset;
import org.jfree.data.category.DefaultCategoryDataset;

import javax.swing.*;
import java.text.ParseException;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;
import java.util.Map.Entry;
import java.util.Set;
import java.util.SortedMap;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class InvocationProfilerPane {
    private static final String INVOCATION_PROFILE_MARKER = "InvocationProfiler[";

    private final ChartPanel pane;
    private final DefaultCategoryDataset dataset = new DefaultCategoryDataset();
    private long startMs = Long.MIN_VALUE;
    private long endMs = Long.MAX_VALUE;
    private Collection<InstanceDiagnostics> instanceDiagnosticsColl = new ArrayList<>();

    public InvocationProfilerPane() {
        JFreeChart latencyChart = ChartFactory.createBarChart(
                "Operation Latency Profile",
                "Latency",
                "Count",
                dataset,
                PlotOrientation.VERTICAL,
                true,
                true,
                false);
        pane = new ChartPanel(latencyChart);
    }

    public void setInstanceDiagnostics(Collection<InstanceDiagnostics> instanceDiagnostics) {
        this.instanceDiagnosticsColl = instanceDiagnostics;
    }

    public void setRange(long startMs, long endMs) {
        this.startMs = startMs;
        this.endMs = endMs;
    }

    public JComponent getComponent() {
        return pane;
    }

    public void update() {
        for (InstanceDiagnostics profile : instanceDiagnosticsColl) {
            Iterator<Map.Entry<Long, String>> iter = profile
                    .between(InstanceDiagnostics.TYPE_INVOCATION_PROFILER, startMs, endMs);
            if (!iter.hasNext()) {
                continue;
            }
            String startProfile = iter.next().getValue();
            String endProfile = null;
            while (iter.hasNext()) {
                endProfile = iter.next().getValue();
            }
            if (endProfile != null) {
                try {
                    updateWithProfiles(startProfile, endProfile);
                } catch (ParseException e) {
                    e.printStackTrace();
                }
            }
        }
    }

    private void updateWithProfiles(String startProfileStr, String endProfileStr) throws ParseException {
        Map<String, SortedMap<Long, Long>> startLatencies = operationToLatencyProfile(parseProfile(startProfileStr));
        Map<String, SortedMap<Long, Long>> endLatencies = operationToLatencyProfile(parseProfile(endProfileStr));
        subtractStartFromEnd(startLatencies, endLatencies);
        workaroundToSortXAxis(endLatencies);
        for (Entry<String, SortedMap<Long, Long>> operationAndProfile : endLatencies.entrySet()) {
            String operation = operationAndProfile.getKey();
            operation = operation.substring(operation.lastIndexOf('.') + 1);
            SortedMap<Long, Long> profile = operationAndProfile.getValue();
            for (Entry<Long, Long> latAndCount : profile.entrySet()) {
                dataset.addValue(latAndCount.getValue(), operation, latAndCount.getKey());
            }
        }
    }

    private void workaroundToSortXAxis(Map<String, SortedMap<Long, Long>> endLatencies) {
        Set<Long> latencyKeys = allLatencyKeys(endLatencies);
        String someOperation = endLatencies.keySet().iterator().next();
        dataset.clear();
        for (Long latency : latencyKeys) {
            dataset.addValue(0, someOperation, latency);
        }
    }

    private Set<Long> allLatencyKeys(Map<String, SortedMap<Long, Long>> endLatencies) {
        Set<Long> latencyKeys = new TreeSet<>();
        for (Entry<String, SortedMap<Long, Long>> operationAndProfile : endLatencies.entrySet()) {
            SortedMap<Long, Long> profile = operationAndProfile.getValue();
            for (Entry<Long, Long> latAndCount : profile.entrySet()) {
                latencyKeys.add(latAndCount.getKey());
            }
        }
        return latencyKeys;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, SortedMap<Long, Long>> operationToLatencyProfile(Map<String, Object> invocationProfile) {
        Map<String, SortedMap<Long, Long>> result = new HashMap<>();
        for (Entry<String, Object> e : invocationProfile.entrySet()) {
            SortedMap<Long, Long> latencyProfile = new TreeMap<>();
            result.put(e.getKey(), latencyProfile);
            Map<String, Object> profile = (Map<String, Object>) e.getValue();
            Map<String, String> latencyProfileStrs = (Map<String, String>) profile.get("latency-distribution");
            for (Entry<String, String> dataPoint : latencyProfileStrs.entrySet()) {
                String bracket = dataPoint.getKey();
                Long latency = Long.parseLong(bracket.substring(0, bracket.indexOf('.')));
                Long count = Long.parseLong(dataPoint.getValue().replace(",", ""));
                latencyProfile.put(latency, count);
            }
        }
        return result;
    }

    private static Map<String, Object> parseProfile(String profileStr) throws ParseException {
        int markerStart = profileStr.indexOf(INVOCATION_PROFILE_MARKER);
        if (markerStart < 0) {
            throw new ParseException("Didn't find start of profile, '" + INVOCATION_PROFILE_MARKER + "'", 0);
        }
        return new MetricsParser(profileStr, markerStart + INVOCATION_PROFILE_MARKER.length() - 1).parseMap();
    }

    private void subtractStartFromEnd(
            Map<String, SortedMap<Long, Long>> startLatencies,
            Map<String, SortedMap<Long, Long>> endLatencies
    ) {
        for (Entry<String, SortedMap<Long, Long>> operationAndProfile : endLatencies.entrySet()) {
            String operation = operationAndProfile.getKey();
            SortedMap<Long, Long> profileAtStart = startLatencies.get(operation);
            if (profileAtStart == null) {
                continue;
            }
            for (Entry<Long, Long> latAndCount : operationAndProfile.getValue().entrySet()) {
                Long latency = latAndCount.getKey();
                Long countAtStart = profileAtStart.get(latency);
                if (countAtStart == null) {
                    continue;
                }
                latAndCount.setValue(latAndCount.getValue() - countAtStart);
            }
        }
    }

    private static class MetricsParser {
        private static final Pattern RE_KEY = Pattern.compile("\\s*(.+?)([=\\[])");
        private static final Pattern RE_VALUE = Pattern.compile("(.+?)([\\s\\]])");

        private final String string;
        private int offset;

        public MetricsParser(String string, int offset) {
            this.string = string;
            this.offset = offset;
        }

        Map<String, Object> parseMap() throws ParseException {
            if (string.charAt(offset) != '[') {
                throw new ParseException("[ expected: " + string.substring(offset), offset);
            }
            Map<String, Object> parsed = new HashMap<>();
            offset++;
            while (true) {
                if (string.charAt(offset) == ']') {
                    offset++;
                    break;
                }
                Matcher keyAndDelimiter = find(RE_KEY);
                if (keyAndDelimiter == null) {
                    throw new ParseException("key expected: " + string.substring(offset), offset);
                }
                String key = keyAndDelimiter.group(1);
                String delimiter = keyAndDelimiter.group(2);
                if (delimiter.equals("=")) {
                    offset = keyAndDelimiter.end(2);
                    Matcher valueAndDelimiter = find(RE_VALUE);
                    if (valueAndDelimiter == null) {
                        throw new ParseException("Failed to parse value", key.length() + 1);
                    }
                    String value = valueAndDelimiter.group(1);
                    parsed.put(key, value);
                    if (valueAndDelimiter.group(2).equals("]")) {
                        offset = valueAndDelimiter.end(2);
                        break;
                    }
                    offset = valueAndDelimiter.end(2);
                } else if (delimiter.equals("[")) {
                    offset = keyAndDelimiter.start(2);
                    parsed.put(key, parseMap());
                } else {
                    throw new ParseException("= or [ expected: " + string.substring(offset), offset);
                }
            }
            return parsed;
        }

        private Matcher find(Pattern pattern) {
            Matcher m = pattern.matcher(string);
            m.region(offset, string.length());
            if (!m.find()) {
                return null;
            }
            return m;
        }
    }
}
