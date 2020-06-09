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
import java.util.Iterator;
import java.util.Map;

public class InvocationProfilerPane {


    private final JFreeChart invocationChart;
    private final ChartPanel pane;

    public InvocationProfilerPane() {
        Function2D normal = new NormalDistributionFunction2D(0.0, 1.0);
        XYDataset dataset = org.jfree.data.general.DatasetUtils.sampleFunction2D(normal, -5.0, 5.0, 100, "Normal");
        invocationChart = ChartFactory.createXYLineChart(
                "XY Series Demo",
                "X",
                "Y",
                dataset,
                PlotOrientation.VERTICAL,
                true,
                true,
                false);
        ChartPanel chartPanel = new ChartPanel(invocationChart);
        this.pane = chartPanel;
    }

    public void setInstanceDiagnostics(InstanceDiagnostics instanceDiagnostics) {
        Iterator<Map.Entry<Long, String>> between = instanceDiagnostics.between(InstanceDiagnostics.TYPE_INVOCATION_PROFILER, 0, Long.MAX_VALUE);
        for (; ; ) {
            if (!between.hasNext()) {
                return;
            }
            String invocationProfileData = between.next().getValue();
            int start = invocationProfileData.indexOf("com.hazelcast.cache.impl.operation.CacheGetOperation[");
            if (start == -1) {
                continue;
            }
            int end = invocationProfileData.indexOf("]]", start);
            //System.out.println("start:" + start + " end:" + end);
            String s = invocationProfileData.substring(start, end);
            String[] distribution = s.substring(s.indexOf("latency-distribution[") + "latency-distribution[".length() + 1).split("\\n");

            final DefaultCategoryDataset dataset = new DefaultCategoryDataset();

            for (String dist : distribution) {
                dist = dist.trim();
                int indexEquals = dist.indexOf("=");
                long value = Long.parseLong(dist.substring(indexEquals + 1).replace(",", ""));
                String key = dist.substring(0, indexEquals);
                dataset.addValue(value, key, key);
            }
            // invocationChart.set.
            System.out.println(Arrays.asList(distribution));
            break;
        }
    }

    public JComponent getComponent() {
        return pane;
    }
}
