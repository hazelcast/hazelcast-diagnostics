package com.hazelcast.diagnostics;

import org.jfree.chart.ChartPanel;
import org.jfree.chart.JFreeChart;
import org.jfree.chart.plot.DialShape;
import org.jfree.chart.plot.MeterInterval;
import org.jfree.chart.plot.MeterPlot;
import org.jfree.data.Range;
import org.jfree.data.general.DefaultValueDataset;
import org.jfree.data.general.ValueDataset;

import javax.swing.JComponent;
import javax.swing.JPanel;
import javax.swing.JTabbedPane;
import java.awt.BasicStroke;
import java.awt.Color;
import java.awt.GridLayout;
import java.awt.Label;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Iterator;
import java.util.Map;

public class DashboardPane {

    private final JPanel pane;
    private final MeterPlot heapPlot;
    private final MeterPlot cpuPlot;

    private ValueDataset heapDataset;
    private ValueDataset cpuDataset;
    private long startMs = Long.MIN_VALUE;
    private long endMs = Long.MAX_VALUE;
    private Collection<InstanceDiagnostics> instanceDiagnosticsColl = new ArrayList<>();

    public DashboardPane() {

        heapDataset = new DefaultValueDataset();
        cpuDataset = new DefaultValueDataset();

        cpuPlot = new MeterPlot();
        heapPlot = new MeterPlot();

        configureCPUPlot();
        configureHeapPlot();

        JFreeChart heapChart = new JFreeChart("Heap Memory", JFreeChart.DEFAULT_TITLE_FONT, heapPlot, false);
        JFreeChart cpuChart = new JFreeChart("CPU", JFreeChart.DEFAULT_TITLE_FONT, cpuPlot, false);

        JTabbedPane jTabbedPane = new JTabbedPane();
        jTabbedPane.setTabPlacement(JTabbedPane.TOP);
        jTabbedPane.addTab("Heap", new Label("heap"));
        jTabbedPane.addTab("CPU", new Label("cpu"));

        ChartPanel heapChartPanel = new ChartPanel(heapChart);
        ChartPanel cpuChartPanel = new ChartPanel(cpuChart);

        JPanel gaugePanel = new JPanel();
        gaugePanel.setLayout(new GridLayout(1, 2));
        gaugePanel.add(heapChartPanel);
        gaugePanel.add(cpuChartPanel);

        JPanel container = new JPanel();
        container.setLayout(new GridLayout(2, 1));
        container.add(gaugePanel);
        container.add(jTabbedPane);

        this.pane = container;
    }

    private void updateCPUPlot(ValueDataset cpuDataset) {

        cpuPlot.setDataset(cpuDataset);
    }

    private void configureCPUPlot() {


        cpuPlot.addInterval(new MeterInterval("All", new Range(0.0, 100)));
        cpuPlot.addInterval(new MeterInterval("Low", new Range(0.00, 80)));
        //cpuPlot.setDialOutlinePaint(Color.white);
        cpuPlot.addInterval(new MeterInterval("High", new Range(80.00, 100), Color.RED, new BasicStroke(2.0f), null));

        cpuPlot.setUnits("%, Max CPU Usage");
        cpuPlot.setTickLabelsVisible(true);
        cpuPlot.setDialShape(DialShape.CHORD);

        cpuPlot.setValuePaint(Color.BLUE);
        cpuPlot.setTickLabelsVisible(true);
        cpuPlot.setMeterAngle(180);

        cpuPlot.setTickLabelPaint(Color.ORANGE);
    }

    private void updateHeapPlot(ValueDataset heapDataset, Long maxHeap) {

        heapPlot.clearIntervals();
        heapPlot.setDataset(heapDataset);

        heapPlot.addInterval(new MeterInterval("Low", new Range(0.00, (80.0 * maxHeap) / 100), Color.YELLOW, new BasicStroke(2.0f), null));
        heapPlot.setDialOutlinePaint(Color.white);
        heapPlot.addInterval(new MeterInterval("High", new Range((20.0 * maxHeap) / 100, maxHeap), Color.RED, new BasicStroke(2.0f), null));

    }

    private void configureHeapPlot() {

        heapPlot.setUnits("Bytes, Max Heap Usage");
        heapPlot.setTickLabelsVisible(true);
        heapPlot.setDialShape(DialShape.CHORD);
        heapPlot.setValuePaint(Color.BLUE);
        heapPlot.setTickLabelsVisible(true);
        heapPlot.setMeterAngle(180);

        heapPlot.setTickLabelPaint(Color.ORANGE);
    }

    public void setInstanceDiagnostics(Collection<InstanceDiagnostics> instanceDiagnostics) {
        this.instanceDiagnosticsColl = instanceDiagnostics;
    }

    public void setRange(long startMs, long endMs) {
        this.startMs = startMs;
        this.endMs = endMs;
    }

    public JComponent getComponent() {
        return pane;
    }

    public void update() {

        Long maxUsedHeap = 0L;
        long maxHeap = 0L;
        Double maxCPULoad = 0D;
        long totalMemory = 0L;

        for (InstanceDiagnostics diagnostics : instanceDiagnosticsColl) {
            Iterator<Map.Entry<Long, Number>> iteratorUsedHeap = diagnostics.metricsBetween(
                    InstanceDiagnostics.METRIC_MEMORY_USED_HEAP, startMs, endMs);
            Iterator<Map.Entry<Long, Number>> iteratorMaxHeap = diagnostics.metricsBetween(
                    InstanceDiagnostics.METRIC_MEMORY_MAX_HEAP, startMs, endMs);
            Iterator<Map.Entry<Long, Number>> iteratorCPULoad = diagnostics.metricsBetween(
                    InstanceDiagnostics.METRIC_OS_PROCESS_CPU_LOAD, startMs, endMs);
            Iterator<Map.Entry<Long, Number>> iteratorTotalMemory = diagnostics.metricsBetween(
                    InstanceDiagnostics.METRIC_RUNTIME_TOTAL_MEMORY, startMs, endMs);

            while (iteratorUsedHeap.hasNext()) {
                Map.Entry<Long, Number> entry = iteratorUsedHeap.next();
                maxUsedHeap = entry.getValue().longValue() > maxUsedHeap ? entry.getValue().longValue() : maxUsedHeap;
            }

            while (iteratorMaxHeap.hasNext()) {
                Map.Entry<Long, Number> entry = iteratorMaxHeap.next();
                maxHeap = entry.getValue().longValue() > maxUsedHeap ? entry.getValue().longValue() : maxUsedHeap;
            }

            while (iteratorCPULoad.hasNext()) {
                Map.Entry<Long, Number> entry = iteratorCPULoad.next();
                maxCPULoad = entry.getValue().doubleValue() > maxCPULoad ? entry.getValue().doubleValue() : maxCPULoad;
            }

            while (iteratorTotalMemory.hasNext()) {
                Map.Entry<Long, Number> entry = iteratorTotalMemory.next();
                totalMemory = Math.max(entry.getValue().longValue(), totalMemory);

            }
        }

        heapDataset = new DefaultValueDataset(maxUsedHeap);
        updateHeapPlot(heapDataset, totalMemory);
        cpuDataset = new DefaultValueDataset(maxCPULoad);
        updateCPUPlot(cpuDataset);
    }
}