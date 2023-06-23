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
import java.awt.*;
import java.awt.event.ActionEvent;
import java.awt.event.ActionListener;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Iterator;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.regex.PatternSyntaxException;

public class MetricsPane {
    private final JComboBox<Object> comboBox;
    private final DefaultComboBoxModel comboBoxModel;
    private LinkedHashSet<String> metricsNames = new LinkedHashSet<>();

    private JPanel component;
    private JTextField filterTextField;
    private final JFreeChart chart;
    private final ChartPanel chartPanel;
    private final TimeSeriesCollection collection;
    private Collection<InstanceDiagnostics> diagnosticsList;
    private long startMs = Long.MIN_VALUE;
    private long endMs = Long.MAX_VALUE;
    private String activeMetric;

    public MetricsPane() {
        this.comboBox = new JComboBox<>();
        this.comboBox.grabFocus();
        this.comboBox.addActionListener(e -> {
            activeMetric = (String) comboBox.getSelectedItem();
            update();
        });
        this.comboBoxModel = new DefaultComboBoxModel();
        comboBox.setModel(comboBoxModel);

        this.collection = new TimeSeriesCollection();
        this.chart = ChartFactory.createTimeSeriesChart(
                "Metrics",
                "Time",
                "Whatever",
                collection,
                true,
                true,
                false);
        this.chartPanel = new ChartPanel(chart);
        XYPlot plot = (XYPlot) chartPanel.getChart().getPlot();
        DateAxis axis = (DateAxis) plot.getDomainAxis();
        axis.setDateFormatOverride(new SimpleDateFormat("HH:mm:ss"));

        this.filterTextField = new JTextField();
        filterTextField.setText(".*");
        filterTextField.setToolTipText("A regex based filter over the metrics.");
        filterTextField.addActionListener(e -> updateCombobox());

        JPanel selectionPanel = new JPanel();
        selectionPanel.setLayout(new BoxLayout(selectionPanel, BoxLayout.Y_AXIS));
        selectionPanel.add(filterTextField);
        selectionPanel.add(comboBox);

        JPanel mainPanel = new JPanel();
        mainPanel.setLayout(new BorderLayout());
        mainPanel.add(selectionPanel, BorderLayout.NORTH);
        mainPanel.add(chartPanel, BorderLayout.CENTER);
        this.component = mainPanel;
    }

    public JComponent getComponent() {
        return component;
    }

    public void setRange(long fromMs, long toMs) {
        this.startMs = fromMs;
        this.endMs = toMs;
    }

    public void setInstanceDiagnostics(Collection<InstanceDiagnostics> diagnosticsList) {
        this.diagnosticsList = diagnosticsList;

        this.metricsNames.clear();
        for (InstanceDiagnostics diagnostics : diagnosticsList) {
            metricsNames.addAll(diagnostics.getAvailableMetrics());
        }
        if (diagnosticsList.isEmpty()) {
            activeMetric = null;
        }

        updateCombobox();
    }

    private void updateCombobox() {
        String patternString = filterTextField.getText().trim();
        Pattern p;
        try {
            p = Pattern.compile(patternString);
        } catch (PatternSyntaxException ex) {
            JOptionPane.showMessageDialog(null, "Invalid regex [" + patternString + "]", "Error", JOptionPane.ERROR_MESSAGE);
            return;
        }

        java.util.List<String> filteredMetrics = new ArrayList<>();
        for (String metric : metricsNames) {
            Matcher m = p.matcher(metric);
            if (m.matches()) {
                filteredMetrics.add(metric);
            }
        }

        Collections.sort(filteredMetrics);

        comboBoxModel.removeAllElements();
        for (String metricName : filteredMetrics) {
            comboBoxModel.addElement(metricName);
        }
    }

    public void update() {
        collection.removeAllSeries();
        if (activeMetric == null) {
            return;
        }

        for (InstanceDiagnostics diagnostics : diagnosticsList) {
            Iterator<Map.Entry<Long, Number>> iterator = diagnostics.metricsBetween(activeMetric, startMs, endMs);

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

}
