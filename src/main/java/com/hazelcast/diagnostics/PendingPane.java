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

import javax.swing.JComponent;
import javax.swing.JPanel;
import java.text.SimpleDateFormat;
import java.util.Collection;
import java.util.Iterator;
import java.util.Map;

public class PendingPane {

    private final JPanel component;
    private final TimeSeriesCollection collection;
    private final String metric;
    private Collection<InstanceDiagnostics> diagnosticsList;
    private long startMs = Long.MIN_VALUE;
    private long endMs = Long.MAX_VALUE;

    public PendingPane(String metric, String title) {
        this.collection = new TimeSeriesCollection();
        this.metric = metric;
        JFreeChart chart = ChartFactory.createTimeSeriesChart(title, "Time", "Pending", collection, true, true, false);
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
        if (diagnosticsList == null) {
            return;
        }

        for (InstanceDiagnostics diagnostics : diagnosticsList) {
            Iterator<Map.Entry<Long, Number>> iterator = diagnostics.metricsBetween(metric, startMs, endMs);

            if (!iterator.hasNext()) {
                continue;
            }

            TimeSeries series = new TimeSeries(diagnostics.getDirectory().getName());
            while (iterator.hasNext()) {
                try {
                    Map.Entry<Long, Number> entry = iterator.next();
                    series.add(new FixedMillisecond(entry.getKey()), entry.getValue());
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
