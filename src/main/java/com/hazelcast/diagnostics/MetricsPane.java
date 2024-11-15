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

import javax.swing.BoxLayout;
import javax.swing.DefaultListModel;
import javax.swing.JButton;
import javax.swing.JComponent;
import javax.swing.JList;
import javax.swing.JOptionPane;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
import javax.swing.JTextField;
import java.awt.BorderLayout;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Iterator;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.regex.PatternSyntaxException;

public class MetricsPane {

    private final JList<String> metricsList;
    private final DefaultListModel<String> metricsListModel;
    private final LinkedHashSet<String> metricsNames = new LinkedHashSet<>();
    private final JPanel component;
    private final JTextField filterTextField;
    private final Set<String> activeMetrics = new LinkedHashSet<>();
    private final TimeSeriesCollection collection;
    private Collection<InstanceDiagnostics> diagnosticsList;
    private long startMs = Long.MIN_VALUE;
    private long endMs = Long.MAX_VALUE;

    public MetricsPane() {
        this.metricsList = new JList<>();
        JScrollPane metricsScrollPane = new JScrollPane(metricsList);
        this.metricsList.grabFocus();
        this.metricsListModel = new DefaultListModel<>();
        this.metricsList.addListSelectionListener(e -> {
            activeMetrics.clear();
            for (int selectedIndex : metricsList.getSelectedIndices()) {
                activeMetrics.add(metricsListModel.elementAt(selectedIndex));
            }
            update();
        });
        metricsList.setModel(metricsListModel);

        this.collection = new TimeSeriesCollection();
        JFreeChart chart = ChartFactory.createTimeSeriesChart("Metrics", "Time", "Whatever", collection, true, true, false);
        ChartPanel chartPanel = new ChartPanel(chart);
        XYPlot plot = (XYPlot) chartPanel.getChart().getPlot();
        DateAxis axis = (DateAxis) plot.getDomainAxis();
        axis.setDateFormatOverride(new SimpleDateFormat("HH:mm:ss"));

        this.filterTextField = new JTextField();
        filterTextField.setText(".*");
        filterTextField.setToolTipText("A regex based filter over the metrics.");
        filterTextField.addActionListener(e -> updateCombobox());

        JButton clearButton = new JButton("Clear");
        clearButton.addActionListener(e -> {
            filterTextField.setText(".*");
            activeMetrics.clear();
            metricsList.setSelectedIndices(new int[] {});
            updateCombobox();
        });

        JPanel filterPanel = new JPanel();
        filterPanel.setLayout(new BoxLayout(filterPanel, BoxLayout.X_AXIS));
        filterPanel.add(filterTextField);
        filterPanel.add(clearButton);

        JPanel selectionPanel = new JPanel();
        selectionPanel.setLayout(new BoxLayout(selectionPanel, BoxLayout.Y_AXIS));
        selectionPanel.add(filterPanel);
        selectionPanel.add(metricsScrollPane);

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
            activeMetrics.clear();
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

        metricsListModel.removeAllElements();
        for (String metricName : filteredMetrics) {
            metricsListModel.addElement(metricName);
        }
    }

    public void update() {
        collection.removeAllSeries();
        if (activeMetrics.isEmpty()) {
            return;
        }

        for (InstanceDiagnostics diagnostics : diagnosticsList) {
            for (String activeMetric : activeMetrics) {
                Iterator<Map.Entry<Long, Number>> iterator = diagnostics.metricsBetween(activeMetric, startMs, endMs);
                if (!iterator.hasNext()) {
                    continue;
                }

                TimeSeries series = new TimeSeries(diagnostics.getDirectory().getName() + ":" + activeMetric);

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
}