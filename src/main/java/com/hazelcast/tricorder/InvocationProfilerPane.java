package com.hazelcast.tricorder;

import org.jfree.chart.ChartFactory;
import org.jfree.chart.ChartPanel;
import org.jfree.chart.JFreeChart;
import org.jfree.chart.plot.PlotOrientation;
import org.jfree.data.category.DefaultCategoryDataset;
import org.jfree.data.function.Function2D;
import org.jfree.data.function.NormalDistributionFunction2D;
import org.jfree.data.xy.XYDataset;

import javax.swing.*;
import java.util.Arrays;
import java.util.Collection;
import java.util.Iterator;
import java.util.Map;
import java.util.Map.Entry;

public class InvocationProfilerPane {


    private final ChartPanel pane;
    private long startMs = Long.MIN_VALUE;
    private long endMs = Long.MAX_VALUE;
    private Collection<InstanceDiagnostics> instanceDiagnosticsColl;

    public InvocationProfilerPane() {
        Function2D normal = new NormalDistributionFunction2D(0.0, 1.0);
        XYDataset dataset = org.jfree.data.general.DatasetUtils.sampleFunction2D(normal, -5.0, 5.0, 100, "Normal");
        JFreeChart invocationChart = ChartFactory.createXYLineChart(
                "XY Series Demo",
                "X",
                "Y",
                dataset,
                PlotOrientation.VERTICAL,
                true,
                true,
                false);
        this.pane = new ChartPanel(invocationChart);
    }

    public void setInstanceDiagnostics(Collection<InstanceDiagnostics> instanceDiagnostics) {
        this.instanceDiagnosticsColl = instanceDiagnostics;
    }

    public void setRange(long startMs, long endMs) {
        this.startMs = startMs;
        this.endMs = endMs;
    }

    public void update() {
        for (int i = 0; i < 100; i++) {
            System.out.println();
        }
        for (InstanceDiagnostics profile : instanceDiagnosticsColl) {
            Iterator<Map.Entry<Long, String>> iter = profile
                    .between(InstanceDiagnostics.TYPE_INVOCATION_PROFILER, 0, Long.MAX_VALUE);
            if (!iter.hasNext()) {
                continue;
            }
            String startProfile = iter.next().getValue();
            String endProfile = null;

            while (iter.hasNext()) {
                endProfile = iter.next().getValue();
            }
            if (endProfile == null) {
                continue;
            }
            System.out.format("Invocation profile data: %s%n", endProfile);
            int start = endProfile.indexOf("com.hazelcast.cache.impl.operation.CacheGetOperation[");
            if (start == -1) {
                continue;
            }
            int end = endProfile.indexOf("]]", start);
            String s = endProfile.substring(start, end);
            String[] distribution = s
                    .substring(s.indexOf("latency-distribution[") + "latency-distribution[".length() + 1)
                    .split("\\n");

            final DefaultCategoryDataset dataset = new DefaultCategoryDataset();

            for (String dist : distribution) {
                dist = dist.trim();
                int indexEquals = dist.indexOf("=");
                long value = Long.parseLong(dist.substring(indexEquals + 1).replace(",", ""));
                String key = dist.substring(0, indexEquals);
                dataset.addValue(value, key, key);
            }
            break;
        }
    }

    public JComponent getComponent() {
        return pane;
    }
}
