package com.hazelcast.diagnostics;

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
import java.util.ArrayList;
import java.util.Collection;
import java.util.Iterator;
import java.util.Map;

public class MemoryPane {

    private final JPanel component;
    private final TimeSeriesCollection collection;
    private Collection<InstanceDiagnostics> diagnosticsList = new ArrayList<>();
    private long startMs = Long.MIN_VALUE;
    private long endMs = Long.MAX_VALUE;

    public MemoryPane() {
        collection = new TimeSeriesCollection();
        JFreeChart chart = ChartFactory.createTimeSeriesChart("Memory Usage", "Time", "Memory Usage", collection, true, true,
                false);
        ChartPanel chartPanel = new ChartPanel(chart);
        XYPlot plot = (XYPlot) chartPanel.getChart().getPlot();
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

        for (InstanceDiagnostics diagnostics : diagnosticsList) {
            Iterator<Map.Entry<Long, Number>> iterator = diagnostics.metricsBetween(
                    InstanceDiagnostics.METRIC_RUNTIME_USED_MEMORY, startMs, endMs);

            if (!iterator.hasNext()) {
                continue;
            }

            TimeSeries series = new TimeSeries(diagnostics.getDirectory().getName());
            while (iterator.hasNext()) {
                try {
                    Map.Entry<Long, Number> entry = iterator.next();
                    long ms = entry.getKey();
                    Long value = entry.getValue().longValue();
                    series.add(new FixedMillisecond(ms), value);
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