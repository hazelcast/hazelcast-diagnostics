package com.hazelcast.tricorder;

import com.jidesoft.swing.RangeSlider;

import javax.swing.*;
import javax.swing.event.ChangeEvent;
import javax.swing.event.ChangeListener;
import java.awt.*;
import java.util.ArrayList;
import java.util.List;

public class MainWindow {
    private RangeSlider rangeSlider;
    private JFrame window;
    private List<InstanceDiagnostics> machines = new ArrayList<>();
    private SystemPropertiesPane systemPropertiesPane = new SystemPropertiesPane();
    private BuildInfoPane buildInfoPane = new BuildInfoPane();
    private InstancesPane machinesPane = new InstancesPane();
    private InvocationProfilerPane invocationProfilerPane = new InvocationProfilerPane();
    private MemoryPane memoryPane = new MemoryPane();
    private CpuUtilizationPane cpuUtilizationPane = new CpuUtilizationPane();
    private MetricsPane metricsPane = new MetricsPane();
    private long durationMs;
    private long startMs;

    public JFrame getJFrame() {
        return window;
    }

    public void add(InstanceDiagnostics instanceDiagnostics) {
        machines.add(instanceDiagnostics);

        this.startMs = instanceDiagnostics.startMs();
        this.durationMs = instanceDiagnostics.endMs() - startMs;

        BoundedRangeModel model = rangeSlider.getModel();
        model.setMinimum(0);
        model.setMaximum((int) durationMs);
        model.setValue(0);
        model.setExtent((int) durationMs);
        // model.setValueIsAdjusting(true);
        //  rangeSlider.setValue();

        System.out.println(instanceDiagnostics.startMs());
        System.out.println(instanceDiagnostics.endMs());

        systemPropertiesPane.setDiagnostics(instanceDiagnostics);
        buildInfoPane.setInstanceDiagnostics(instanceDiagnostics);
        invocationProfilerPane.setInstanceDiagnostics(instanceDiagnostics);

        memoryPane.setInstanceDiagnostics(machines);
        memoryPane.update();

        cpuUtilizationPane.setInstanceDiagnostics(machines);
        cpuUtilizationPane.update();

        metricsPane.setInstanceDiagnostics(machines);
        metricsPane.update();
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
        JPanel mainPanel = new JPanel();
        mainPanel.setLayout(new BorderLayout());
        rangeSlider = newRangeSlider();
        mainPanel.add(rangeSlider, BorderLayout.NORTH);
        mainPanel.add(tabbedPane, BorderLayout.CENTER);

        window.setContentPane(mainPanel);
    }

    private RangeSlider newRangeSlider() {
        RangeSlider rangeSlider = new RangeSlider();
        rangeSlider.addChangeListener(e -> {
            long begin = rangeSlider.getLowValue() + startMs;
            long end = rangeSlider.getHighValue() + startMs;

            // System.out.println(begin + " " + end);
            cpuUtilizationPane.setRange(begin, end);
            cpuUtilizationPane.update();

            memoryPane.setRange(begin, end);
            memoryPane.update();

            metricsPane.setRange(begin, end);
            metricsPane.update();
        });
        rangeSlider.setRangeDraggable(true);
        return rangeSlider;
    }

    private JTabbedPane newTabbedPane() {
        JTabbedPane tabbedPane = new JTabbedPane();
        tabbedPane.setTabPlacement(JTabbedPane.BOTTOM);

        tabbedPane.addTab("Machines", null, machinesPane.getComponent());
        tabbedPane.addTab("Metrics", null, metricsPane.getComponent());
        tabbedPane.addTab("Memory", null, memoryPane.getComponent());
        tabbedPane.addTab("CPU", null, cpuUtilizationPane.getComponent());
        tabbedPane.addTab("Build Info", null, buildInfoPane.getComponent());
        tabbedPane.addTab("System properties", null, systemPropertiesPane.getComponent());
        tabbedPane.addTab("Slow Operations", null, new JPanel());
        tabbedPane.addTab("Invocations", null, new JPanel());
        tabbedPane.addTab("Invocation Profiler", null, invocationProfilerPane.getComponent());
        tabbedPane.addTab("Operation profiler", null, new JPanel());
        tabbedPane.addTab("Slow operation thread sampler", null, new JPanel());
        tabbedPane.addTab("Slow Operations", null, new JPanel());
        tabbedPane.addTab("Slow Operations", null, new JPanel());
        return tabbedPane;
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
