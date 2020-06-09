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
import java.util.Iterator;
import java.util.Map;

public class MemoryPane {

    private final JPanel component;
    private final JFreeChart invocationChart;
    private final ChartPanel chartPanel;
    private final TimeSeriesCollection collection;
    private InstanceDiagnostics diagnostics;
    private long startMs = Long.MIN_VALUE;
    private long endMs = Long.MAX_VALUE;

    public MemoryPane() {
//        Function2D normal = new NormalDistributionFunction2D(0.0, 1.0);
//        XYDataset dataset = DatasetUtils.sampleFunction2D(normal, -5.0, 5.0, 100, "Normal");
        collection = new TimeSeriesCollection();
        invocationChart = ChartFactory.createXYLineChart(
                "Memory Usage",
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

    public void setInstanceDiagnostics(InstanceDiagnostics diagnostics) {
        this.diagnostics = diagnostics;
    }

    public void update() {
        collection.removeAllSeries();
        if (diagnostics == null) {
            return;
        }

        Iterator<Map.Entry<Long, Long>> iterator = diagnostics.longMetricsBetween("[metric=runtime.usedMemory]", startMs, endMs);

        if (!iterator.hasNext()) {
            System.out.println("No [metric=runtime.usedMemory] found in directory: " + diagnostics.getDirectory());
            return;
        }

        final TimeSeries series = new TimeSeries("Random Data");
        Second current = new Second();

        while (iterator.hasNext()) {
            try {
                Map.Entry<Long, Long> entry = iterator.next();
                Long value = entry.getValue();
                series.add(current, value);
                current = (Second) current.next();
            } catch (SeriesException e) {
                System.err.println("Error adding to series");
            }
        }

        collection.removeAllSeries();
        collection.addSeries(series);
    }

    public JComponent getComponent() {
        return component;
    }
}
