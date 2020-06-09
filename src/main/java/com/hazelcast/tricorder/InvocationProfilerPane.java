package com.hazelcast.tricorder;

import org.jfree.chart.ChartFactory;
import org.jfree.chart.ChartPanel;
import org.jfree.chart.JFreeChart;
import org.jfree.chart.plot.PlotOrientation;
import org.jfree.data.category.DefaultCategoryDataset;

import javax.swing.*;
import java.text.ParseException;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;
import java.util.Map.Entry;
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
                "XY Series Demo",
                "X",
                "Y",
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

    @SuppressWarnings("unchecked")
    private void updateWithProfiles(String startProfileStr, String endProfileStr) throws ParseException {
        Map<String, Object> startProfile = parseProfile(startProfileStr);
        Map<String, Object> endProfile = parseProfile(endProfileStr);

        Iterator<String> it = endProfile.keySet().iterator();
        if (!it.hasNext()) {
            return;
        }
        String service = it.next();
        Map<String, Object> profile = (Map<String, Object>) endProfile.get(service);
        Map<String, String> latencyDistribution = (Map<String, String>) profile.get("latency-distribution");
        System.out.format("%s: %s%n", service, latencyDistribution);

        dataset.clear();
        for (Entry<String, String> dataPoint : latencyDistribution.entrySet()) {
            String bracket = dataPoint.getKey();
            Long latency = Long.parseLong(bracket.substring(0, bracket.indexOf('.')));
            long count = Long.parseLong(dataPoint.getValue());
            dataset.addValue(count, latency, latency);
        }

    }

    private static Map<String, Object> parseProfile(String profileStr) throws ParseException {
        int markerStart = profileStr.indexOf(INVOCATION_PROFILE_MARKER);
        if (markerStart < 0) {
            throw new ParseException("Didn't find start of profile, '" + INVOCATION_PROFILE_MARKER + "'", 0);
        }
        return new MetricsParser(profileStr, markerStart + INVOCATION_PROFILE_MARKER.length() - 1).parseMap();
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
