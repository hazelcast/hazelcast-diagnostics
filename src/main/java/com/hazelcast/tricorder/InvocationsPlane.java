package com.hazelcast.tricorder;

import org.jfree.chart.ChartFactory;
import org.jfree.chart.ChartPanel;
import org.jfree.chart.JFreeChart;
import org.jfree.chart.axis.DateAxis;
import org.jfree.chart.plot.XYPlot;
import org.jfree.data.general.SeriesException;
import org.jfree.data.time.FixedMillisecond;
import org.jfree.data.time.TimeSeries;
import org.jfree.data.time.TimeSeriesCollection;

import javax.swing.*;
import java.text.SimpleDateFormat;
import java.util.Collection;
import java.util.Iterator;
import java.util.Map;

public class InvocationsPlane {

    private final JPanel component;
    private final JFreeChart chart;
    private final ChartPanel chartPanel;
    private final TimeSeriesCollection collection;
    private Collection<InstanceDiagnostics> diagnosticsList;
    private long startMs = Long.MIN_VALUE;
    private long endMs = Long.MAX_VALUE;

    public InvocationsPlane() {
        collection = new TimeSeriesCollection();
        chart = ChartFactory.createTimeSeriesChart(
                "Invocation Throughput",
                "Time",
                "Throughput",
                collection,
                true,
                true,
                false);
        this.chartPanel = new ChartPanel(chart);
        XYPlot plot = (XYPlot)chartPanel.getChart().getPlot();
        DateAxis axis = (DateAxis)plot.getDomainAxis();
        axis.setDateFormatOverride(new SimpleDateFormat("HH:mm:ss"));
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
            Iterator<Map.Entry<Long, Number>> iterator = diagnostics.metricsBetween("[unit=count,metric=operation.invocations.lastCallId]", startMs, endMs);

            if (!iterator.hasNext()) {
                continue;
            }

            TimeSeries series = new TimeSeries(diagnostics.getDirectory().getName());
            long previousMs = 0;
            long previousCount = 0;
            while (iterator.hasNext()) {
                try {
                    Map.Entry<Long, Number> entry = iterator.next();
                    long currentMs = entry.getKey();

                    long count = entry.getValue().longValue();
                    long delta = count - previousCount;
                    long durationMs = currentMs - previousMs;
                    double throughput = (delta * 1000d) / durationMs;
                    series.add(new FixedMillisecond(currentMs), throughput);
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
