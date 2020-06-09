package com.hazelcast.tricorder;

import org.jfree.chart.ChartFactory;
import org.jfree.chart.ChartPanel;
import org.jfree.chart.JFreeChart;
import org.jfree.chart.plot.PlotOrientation;
import org.jfree.data.function.Function2D;
import org.jfree.data.function.NormalDistributionFunction2D;
import org.jfree.data.general.DatasetUtils;
import org.jfree.data.general.SeriesException;
import org.jfree.data.time.Second;
import org.jfree.data.time.TimeSeries;
import org.jfree.data.time.TimeSeriesCollection;
import org.jfree.data.xy.XYDataset;

import javax.swing.*;
import java.util.Iterator;
import java.util.Map;

public class MemoryPane {

    private final JPanel component;
    private final JFreeChart invocationChart;
    private final ChartPanel chartPanel;
    private final TimeSeriesCollection collection;

    public MemoryPane(){
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
        this.component=chartPanel;
    }

    public void setInstanceDiagnostics(InstanceDiagnostics diagnostics) {
        collection.removeAllSeries();

        Iterator<Map.Entry<Long, Long>> iterator = diagnostics.longMetricsBetween("[metric=runtime.usedMemory]",Long.MIN_VALUE, Long.MAX_VALUE);

        if(!iterator.hasNext()){
            System.out.println("No BuildInfo found in directory: "+diagnostics.getDirectory());
            return;
        }

        final TimeSeries series = new TimeSeries( "Random Data" );
        Second current = new Second( );

        while (iterator.hasNext()){
            try {
                Map.Entry<Long,Long> entry = iterator.next();
                Long value = entry.getValue();
                System.out.println(value);
                series.add(current, value);
                current = ( Second ) current.next( );
            } catch ( SeriesException e ) {
                System.err.println("Error adding to series");
            }
        }

        collection.removeAllSeries();
        collection.addSeries(series);
    }

    public JComponent getComponent(){
        return component;
    }
}
