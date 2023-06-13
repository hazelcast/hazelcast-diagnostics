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
import java.util.Collection;
import java.util.Iterator;
import java.util.LinkedList;
import java.util.Map;

public class CpuUtilizationPane {
    private final JPanel component;
    private final JFreeChart chart;
    private final ChartPanel chartPanel;
    private final TimeSeriesCollection collection;
    private long startMs = Long.MIN_VALUE;
    private long endMs = Long.MAX_VALUE;
    private Collection<InstanceDiagnostics> diagnosticsList = new LinkedList<>();

    public CpuUtilizationPane() {
        collection = new TimeSeriesCollection();
        chart = ChartFactory.createTimeSeriesChart(
                "CPU Utilization",
                "Time",
                "Utilization",
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

        for (InstanceDiagnostics diagnostics : diagnosticsList) {
            Iterator<Map.Entry<Long, Number>> iterator = diagnostics.metricsBetween("[metric=os.processCpuLoad]", startMs, endMs);

            if (!iterator.hasNext()) {
                continue;
            }

            final TimeSeries series = new TimeSeries(diagnostics.getDirectory().getName());

            while (iterator.hasNext()) {
                try {
                    Map.Entry<Long, Number> entry = iterator.next();
                    long ms = entry.getKey();
                    double value = entry.getValue().doubleValue();
                    // System.out.println(value);
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
