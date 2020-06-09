package com.hazelcast.tricorder;

import com.jidesoft.swing.RangeSlider;
import org.jfree.chart.ChartFactory;
import org.jfree.chart.ChartPanel;
import org.jfree.chart.JFreeChart;
import org.jfree.chart.plot.PlotOrientation;
import org.jfree.data.category.DefaultCategoryDataset;
import org.jfree.data.function.Function2D;
import org.jfree.data.function.NormalDistributionFunction2D;
import org.jfree.data.general.DatasetUtilities;
import org.jfree.data.xy.XYDataset;

import javax.swing.*;
import java.awt.*;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Iterator;
import java.util.List;
import java.util.Map;

public class MainWindow {
    private final RangeSlider rangeSlider;
    private JFrame window;
    private List<Diagnostics> machines = new ArrayList<>();
    private JTextPane propertiesTextPane;
    private JTextPane buildInfoTextPane;
    private JFreeChart invocationChart;

    public JFrame getJFrame() {
        return window;
    }

    public void add(Diagnostics machine) {
        machines.add(machine);


        BoundedRangeModel model = rangeSlider.getModel();
        model.setMinimum((int) machine.startMillis());
        model.setMaximum((int) machine.endMillis());
        model.setValue((int) machine.startMillis());
        model.setExtent((int) (machine.endMillis() - machine.startMillis()));
        // model.setValueIsAdjusting(true);
        //  rangeSlider.setValue();

        System.out.println(machine.startMillis());
        System.out.println(machine.endMillis());

        updateSystemProperties(machine);
        updateBuildInfo(machine);

        Iterator<Map.Entry<Long, String>> between = machine.between(Diagnostics.TYPE_INVOCATION_PROFILER, 0, Long.MAX_VALUE);
        for (; ; ) {
            if (!between.hasNext()) {
                return;
            }
            String invocationProfileData = between.next().getValue();
            int start = invocationProfileData.indexOf("com.hazelcast.cache.impl.operation.CacheGetOperation[");
            if (start == -1) {
                continue;
            }
            int end = invocationProfileData.indexOf("]]", start);
            //System.out.println("start:" + start + " end:" + end);
            String s = invocationProfileData.substring(start, end);
            String[] distribution = s.substring(s.indexOf("latency-distribution[") + "latency-distribution[".length() + 1).split("\\n");

            final DefaultCategoryDataset dataset = new DefaultCategoryDataset();

            for (String dist : distribution) {
                dist = dist.trim();
                int indexEquals = dist.indexOf("=");
                long value = Long.parseLong(dist.substring(indexEquals + 1).replace(",", ""));
                String key = dist.substring(0, indexEquals);
                dataset.addValue(value, key, key);
            }
            // invocationChart.set.
            System.out.println(Arrays.asList(distribution));
            break;
        }
    }

    private void updateBuildInfo(Diagnostics machine) {
        String[] lines = machine.between(Diagnostics.TYPE_BUILD_INFO, 0, Long.MAX_VALUE).next().getValue().split("\\n");
        StringBuffer sb = new StringBuffer();
        for (String line: lines) {
            int indexEquals = line.indexOf('=');
            if (indexEquals == -1) {
                continue;
            }

            sb.append(line.trim().replace("]","")).append("\n");
        }
        buildInfoTextPane.setText(sb.toString());
    }

    private void updateSystemProperties(Diagnostics machine) {
        String[] lines = machine.between(Diagnostics.TYPE_SYSTEM_PROPERTIES, 0, Long.MAX_VALUE).next().getValue().split("\\n");
        StringBuffer sb = new StringBuffer();
        for (String line: lines) {
            int indexEquals = line.indexOf('=');
            if (indexEquals == -1) {
                continue;
            }

            sb.append(line.trim().replace("]","")).append("\n");
        }
        propertiesTextPane.setText(sb.toString());
    }

    public MainWindow() {
        window = new JFrame();
        Dimension dim = Toolkit.getDefaultToolkit().getScreenSize();
        window.setSize(1024, 768);
        window.setLocation(dim.width / 2 - window.getSize().width / 2, dim.height / 2 - window.getSize().height / 2);
        window.setLocationRelativeTo(null);
        window.setDefaultCloseOperation(WindowConstants.EXIT_ON_CLOSE);


        buildMenu(window);

        JTabbedPane tabbedPane = newTabbedPane();

//        public static final int TYPE_OPERATION_THREAD_SAMPLES = 9;
//        public static final int TYPE_CONNECTION_REMOVED = 10;
//        public static final int TYPE_HAZELCAST_INSTANCE = 11;
//        public static final int TYPE_MEMBER_REMOVED = 12;
//        public static final int TYPE_MEMBER_ADDED = 13;
//        public static final int TYPE_CLUSTER_VERSION_CHANGE = 14;
//        public static final int TYPE_LIFECYCLE = 15;
//        public static final int TYPE_CONNECTION_ADDED = 16;
//        public static final int TYPES = TYPE_CONNECTION_ADDED + 1;


        JPanel mainPanel = new JPanel();
        mainPanel.setLayout(new BorderLayout());
        rangeSlider = newRangeSlider();
        mainPanel.add(rangeSlider, BorderLayout.NORTH);
        mainPanel.add(tabbedPane, BorderLayout.CENTER);

        window.setContentPane(mainPanel);
    }

    private RangeSlider newRangeSlider() {
        RangeSlider rangeSlider = new RangeSlider();
        rangeSlider.addChangeListener(e -> System.out.println(e));
        rangeSlider.setRangeDraggable(true);
        return rangeSlider;
    }

    private JTabbedPane newTabbedPane() {
        JTabbedPane tabbedPane = new JTabbedPane();
        tabbedPane.setTabPlacement(JTabbedPane.BOTTOM);


        tabbedPane.addTab("Machines", null, newMachinesPanel());

        JComponent panel1 = new JPanel();
        tabbedPane.addTab("Metrics", null, panel1);

        JComponent panel2 = new JPanel();
        tabbedPane.addTab("Build Info", null, newBuildInfoPane());

        tabbedPane.addTab("System properties", null, newPropertiesPane());

        JComponent panel4 = new JPanel();
        tabbedPane.addTab("Slow Operations", null, panel4);

        JComponent panel5 = new JPanel();
        tabbedPane.addTab("Invocations", null, panel5);

        tabbedPane.addTab("Invocation Profiler", null, newInvocationProfilerPane());

        JComponent panel7 = new JPanel();
        tabbedPane.addTab("Operation profiler", null, panel7);

        JComponent panel8 = new JPanel();
        tabbedPane.addTab("Slow operation thread sampler", null, panel8);

        JComponent panel9 = new JPanel();
        tabbedPane.addTab("Slow Operations", null, panel9);

        JComponent panel10 = new JPanel();
        tabbedPane.addTab("Slow Operations", null, panel10);
        return tabbedPane;
    }

    private Component newMachinesPanel() {
        return new JPanel();
    }

    private Component newInvocationProfilerPane() {
        Function2D normal = new NormalDistributionFunction2D(0.0, 1.0);
        XYDataset dataset = DatasetUtilities.sampleFunction2D(normal, -5.0, 5.0, 100, "Normal");
        invocationChart = ChartFactory.createXYLineChart(
                "XY Series Demo",
                "X",
                "Y",
                dataset,
                PlotOrientation.VERTICAL,
                true,
                true,
                false);
        ChartPanel chartPanel = new ChartPanel(invocationChart);
        return chartPanel;
    }

    private Component newBuildInfoPane() {
        buildInfoTextPane = new JTextPane();
        buildInfoTextPane.setEditable(false);
        return new JScrollPane(buildInfoTextPane);
    }

    private JComponent newPropertiesPane() {
        propertiesTextPane = new JTextPane();
        propertiesTextPane.setEditable(false);
        return new JScrollPane(propertiesTextPane);
    }

    private static void buildMenu(JFrame window) {
        JMenuBar menuBar = new JMenuBar();

        JMenu fileMenu = new JMenu("File");
        menuBar.add(fileMenu);
        window.setJMenuBar(menuBar);

        JMenuItem menuItem = new JMenuItem("Load");
        fileMenu.add(menuItem);

        JMenuItem exitItem = new JMenuItem("Exit");
        exitItem.addActionListener(e -> System.exit(0));
        fileMenu.add(exitItem);
    }
}
