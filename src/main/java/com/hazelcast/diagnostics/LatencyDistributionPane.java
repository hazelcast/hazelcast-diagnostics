package com.hazelcast.diagnostics;

import org.jfree.chart.ChartFactory;
import org.jfree.chart.ChartPanel;
import org.jfree.chart.JFreeChart;
import org.jfree.chart.plot.PlotOrientation;
import org.jfree.data.category.DefaultCategoryDataset;

import javax.swing.*;
import java.awt.*;
import java.text.ParseException;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Map;
import java.util.Map.Entry;
import java.util.Set;
import java.util.SortedMap;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class LatencyDistributionPane {
    private static final String INVOCATION_PROFILE_MARKER = "InvocationProfiler[";

    private final DefaultCategoryDataset dataset = new DefaultCategoryDataset();
    private final ChartPanel chart;
    private final JPanel component;
    private final DefaultComboBoxModel<Object> comboBoxModel;
    private long startMs = Long.MIN_VALUE;
    private long endMs = Long.MAX_VALUE;
    private Collection<InstanceDiagnostics> instanceDiagnosticsColl = new ArrayList<>();
    private JComboBox comboBox;
    private Map<InstanceDiagnostics, Map<String, SortedMap<Long,Long>>> distributionMap = new HashMap<>();
    private String active;
    private int type;

    public LatencyDistributionPane(int type) {
        this.type = type;
        comboBoxModel = new DefaultComboBoxModel<>();
        comboBox = new JComboBox(comboBoxModel);
        comboBox.addActionListener(e -> {
            active = (String) comboBox.getSelectedItem();
            render();
        });
        JPanel panel = new JPanel();
        panel.setLayout(new BorderLayout());
        JFreeChart barChart = ChartFactory.createBarChart(
                "Latency distribution",
                "Category",
                "Score",
                dataset,
                PlotOrientation.VERTICAL,
                true, true, false);
        chart = new ChartPanel(barChart);
        panel.add(comboBox, BorderLayout.NORTH);
        panel.add(chart, BorderLayout.CENTER);
        component = panel;
    }

    public void setInstanceDiagnostics(Collection<InstanceDiagnostics> instanceDiagnostics) {
        this.instanceDiagnosticsColl = instanceDiagnostics;
    }

    public void setRange(long startMs, long endMs) {
        this.startMs = startMs;
        this.endMs = endMs;
    }

    public JComponent getComponent() {
        return component;
    }

    public void render() {
        dataset.clear();
        if (active == null) {
            System.out.println("nothing selected");
        } else {
            for(Map.Entry<InstanceDiagnostics,Map<String,SortedMap<Long,Long>>> entry: distributionMap.entrySet()){
                InstanceDiagnostics diagnostics = entry.getKey();
                Map<String,SortedMap<Long,Long>> map = entry.getValue();
                SortedMap<Long,Long> distribution = map.get(active);
                if(distribution == null){
                    continue;
                }

                System.out.println(distribution);
                for (Map.Entry<Long, Long> e : distribution.entrySet()) {
                    dataset.addValue(e.getValue(), diagnostics.getDirectory().getName(),e.getKey());
                }
            }
        }
    }

    public void update() {
        distributionMap.clear();

        for (InstanceDiagnostics diagnostics : instanceDiagnosticsColl) {
            Iterator<Entry<Long, String>> iter = diagnostics.between(type, startMs, endMs);
            if (!iter.hasNext()) {
                continue;
            }

            String startProfile = iter.next().getValue();
            String endProfile = null;
            while (iter.hasNext()) {
                endProfile = iter.next().getValue();
                // System.out.println(endProfile);
            }

            try {
               Map<String,SortedMap<Long,Long>> result = calc(startProfile, endProfile);
               distributionMap.put(diagnostics, result);
            } catch (ParseException e) {
                e.printStackTrace();
            }
        }

        Set<String> operations = new HashSet<>();
        for(Map<String,SortedMap<Long,Long>> m: distributionMap.values()){
            operations.addAll(m.keySet());
        }

        java.util.List<String> operationsList = new ArrayList<>(operations);
        Collections.sort(operationsList);
        for (String operation : operationsList) {
            comboBoxModel.addElement(operation);
        }

        render();
    }


    private Map<String,SortedMap<Long,Long>> calc(String startProfileStr, String endProfileStr) throws ParseException {
        Map<String, SortedMap<Long, Long>> startLatencies = extractOperationToLatencyProfile(parseProfile(startProfileStr));
        Map<String, SortedMap<Long, Long>> endLatencies = extractOperationToLatencyProfile(parseProfile(endProfileStr));
        subtractStartFromEnd(startLatencies, endLatencies);


        Map<String,SortedMap<Long,Long>> result  = new HashMap<>();

        for (Entry<String, SortedMap<Long, Long>> endLatency : endLatencies.entrySet()) {
            String operation = endLatency.getKey();

            operation = operation.substring(operation.lastIndexOf('.') + 1);

            SortedMap<Long, Long> startProfile = startLatencies.get(operation);

            SortedMap<Long, Long> endProfile = endLatency.getValue();

            for (Map.Entry<Long, Long> entry : endProfile.entrySet()) {
                Long key = entry.getKey();
                long endValue = entry.getValue();
                long startValue = 0;
                if (startProfile != null) {
                    Long s = startProfile.get(key);
                    if (s != null) {
                        startValue = s;
                    }
                }

                long delta = endValue - startValue;
                SortedMap<Long, Long> exist = result.computeIfAbsent(operation, k -> new TreeMap<>());

                Long finalValue = exist.get(key);
                if (finalValue == null) {
                    exist.put(key, delta);
                } else {
                    exist.put(key, finalValue + delta);
                }
            }

//            double[][] percentilePlot = transposeToPercentilePlot(endProfile);
//            dataset.addSeries(operation, percentilePlot);
        }

        return result;
    }

    private double[][] transposeToPercentilePlot(SortedMap<Long, Long> latencyProfile) {
        double[][] result = new double[2][latencyProfile.size()];
        long totalCount = 0;
        for (long count : latencyProfile.values()) {
            totalCount += count;
        }
        long runningCount = 0;
        int index = 0;
        for (Entry<Long, Long> latencyAndCount : latencyProfile.entrySet()) {
            result[0][index] = Math.log10(100.0 / (1.0 - (double) runningCount / totalCount));
            result[1][index++] = 1.5 * latencyAndCount.getKey();
            runningCount += latencyAndCount.getValue();
        }
        return result;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, SortedMap<Long, Long>> extractOperationToLatencyProfile(
            Map<String, Object> invocationProfile
    ) {
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
        profileStr = profileStr.replace("OperationsProfiler[", INVOCATION_PROFILE_MARKER);

        int markerStart = profileStr.indexOf(INVOCATION_PROFILE_MARKER);
        if (markerStart < 0) {
            throw new ParseException("Didn't find start of profile, '" + INVOCATION_PROFILE_MARKER + "'", 0);
        }
        return new MetricsParser(profileStr, markerStart + INVOCATION_PROFILE_MARKER.length() - 1).parseMap();
    }

    private static void subtractStartFromEnd(
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
