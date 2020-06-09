package com.hazelcast.tricorder;

import org.jfree.chart.ChartFactory;
import org.jfree.chart.ChartPanel;
import org.jfree.chart.JFreeChart;
import org.jfree.chart.plot.PlotOrientation;
import org.jfree.data.general.SeriesException;
import org.jfree.data.time.Second;
import org.jfree.data.time.TimeSeries;
import org.jfree.data.time.TimeSeriesCollection;

import javax.swing.*;
import java.util.Collection;
import java.util.Iterator;
import java.util.Map;

public class InvocationsPlane {

    private final JPanel component;
    private final JFreeChart invocationChart;
    private final ChartPanel chartPanel;
    private final TimeSeriesCollection collection;
    private Collection<InstanceDiagnostics> diagnosticsList;
    private long startMs = Long.MIN_VALUE;
    private long endMs = Long.MAX_VALUE;

    public InvocationsPlane() {
        this.collection = new TimeSeriesCollection();
        this.invocationChart = ChartFactory.createXYLineChart(
                "Invocation/s",
                "X",
                "Y",
                collection,
                PlotOrientation.VERTICAL,
                true,
                true,
                false);
        this.chartPanel = new ChartPanel(invocationChart);
        this.component = chartPanel;
    }

    public void setRange(long fromMs, long toMs) {
        this.startMs = fromMs;
        this.endMs = toMs;
    }

    public void setInstanceDiagnostics(Collection<InstanceDiagnostics> diagnosticsList) {
        this.diagnosticsList = diagnosticsList;
    }

    public void update() {
        collection.removeAllSeries();
        if (diagnosticsList == null) {
            return;
        }

        for (InstanceDiagnostics diagnostics : diagnosticsList) {
            Iterator<Map.Entry<Long, Long>> iterator = diagnostics.longMetricsBetween("[unit=count,metric=operation.invocations.lastCallId]", startMs, endMs);

            if (!iterator.hasNext()) {
                continue;
            }

            TimeSeries series = new TimeSeries(diagnostics.getDirectory().getName());
            Second current = new Second();
            long previousMs = 0;
            long previousCount = 0;
            while (iterator.hasNext()) {
                try {
                    Map.Entry<Long, Long> entry = iterator.next();
                    long currentMs = entry.getKey();

                    long count = entry.getValue();
                    long delta = count - previousCount;
                    long durationMs = currentMs - previousMs;
                    double throughput = (delta * 1000d) / durationMs;
                    series.add(current, throughput);
                    current = (Second) current.next();
                    previousMs = currentMs;
                    previousCount = count;
                } catch (SeriesException e) {
                    System.err.println("Error adding to series");
                }
            }
            collection.addSeries(series);
        }
    }

    public JComponent getComponent() {
        return component;
    }
}
