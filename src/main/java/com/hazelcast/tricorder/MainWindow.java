package com.hazelcast.tricorder;

import javax.swing.*;
import java.awt.*;
import java.io.File;
import java.util.HashMap;
import java.util.Map;

public class MainWindow {

    private JFrame window;
    private Map<File, InstanceDiagnostics> machines = new HashMap<>();

    private SystemPropertiesPane systemPropertiesPane = new SystemPropertiesPane();
    private BuildInfoPane buildInfoPane = new BuildInfoPane();
    private InstancesPane machinesPane;
    private InvocationProfilerPane invocationProfilerPane = new InvocationProfilerPane();
    private MemoryPane memoryPane = new MemoryPane();
    private CpuUtilizationPane cpuUtilizationPane = new CpuUtilizationPane();
    private MetricsPane metricsPane = new MetricsPane();
    private TimeSelectorPane timeSelectorPane = new TimeSelectorPane();
    private InvocationsPlane invocationsPlane = new InvocationsPlane();
    private OperationsPlane operationsPlane = new OperationsPlane();

    public JFrame getJFrame() {
        return window;
    }

    public void add(InstanceDiagnostics instanceDiagnostics) {
        machines.put(instanceDiagnostics.getDirectory(), instanceDiagnostics);

        update();
    }

    private void update() {
        timeSelectorPane.setInstanceDiagnostics(machines.values());
//        systemPropertiesPane.setDiagnostics(instanceDiagnostics);
//        buildInfoPane.setInstanceDiagnostics(instanceDiagnostics);
        invocationProfilerPane.setInstanceDiagnostics(machines.values());
        invocationProfilerPane.update();

        memoryPane.setInstanceDiagnostics(machines.values());
        memoryPane.update();

        cpuUtilizationPane.setInstanceDiagnostics(machines.values());
        cpuUtilizationPane.update();

        metricsPane.setInstanceDiagnostics(machines.values());
        metricsPane.update();

        invocationsPlane.setInstanceDiagnostics(machines.values());
        invocationsPlane.update();

        operationsPlane.setInstanceDiagnostics(machines.values());
        operationsPlane.update();
    }

    public void remove(File directory) {
        machines.remove(directory);
        update();
    }

    public MainWindow() {
        window = new JFrame();
        Dimension dim = Toolkit.getDefaultToolkit().getScreenSize();
        window.setSize(1440, 900);
        window.setLocation(dim.width / 2 - window.getSize().width / 2, dim.height / 2 - window.getSize().height / 2);
        window.setLocationRelativeTo(null);
        window.setDefaultCloseOperation(WindowConstants.EXIT_ON_CLOSE);

        machinesPane = new InstancesPane(this);
        machinesPane.addDirectory(new File("data/member"));
        machinesPane.addDirectory(new File("data/litemember"));

        buildMenu(window);

        JTabbedPane tabbedPane = newTabbedPane();

        timeSelectorPane.addChangeListener(e -> {
            long begin = timeSelectorPane.getStartMs();
            long end = timeSelectorPane.getEndMs();

            cpuUtilizationPane.setRange(begin, end);
            cpuUtilizationPane.update();

            memoryPane.setRange(begin, end);
            memoryPane.update();

            invocationProfilerPane.setRange(begin, end);
            invocationProfilerPane.update();

            metricsPane.setRange(begin, end);
            metricsPane.update();

            invocationsPlane.setRange(begin, end);
            invocationsPlane.update();

            operationsPlane.setRange(begin, end);
            operationsPlane.update();
        });


        JPanel analysisPanel= new JPanel();
        analysisPanel.setLayout(new BorderLayout());
        analysisPanel.add(timeSelectorPane.getComponent(), BorderLayout.NORTH);
        analysisPanel.add(tabbedPane, BorderLayout.CENTER);

        JSplitPane splitPane = new JSplitPane(JSplitPane.HORIZONTAL_SPLIT, machinesPane.getComponent(), analysisPanel);
        splitPane.setDividerLocation(250);


        window.setContentPane(splitPane);
    }

    private JTabbedPane newTabbedPane() {
        JTabbedPane tabbedPane = new JTabbedPane();
        tabbedPane.setTabPlacement(JTabbedPane.BOTTOM);
        tabbedPane.addTab("Memory", null, memoryPane.getComponent());
        tabbedPane.addTab("CPU", null, cpuUtilizationPane.getComponent());
        tabbedPane.addTab("Metrics", null, metricsPane.getComponent());
        tabbedPane.addTab("Build Info", null, buildInfoPane.getComponent());
        tabbedPane.addTab("System properties", null, systemPropertiesPane.getComponent());
        tabbedPane.addTab("Slow Operations", null, new JPanel());
        tabbedPane.addTab("Invocations", null, invocationsPlane.getComponent());
        tabbedPane.addTab("Invocation Profiler", null, invocationProfilerPane.getComponent());
        tabbedPane.addTab("Operations", null, operationsPlane.getComponent());
        tabbedPane.addTab("Operation profiler", null, new JPanel());
        tabbedPane.addTab("Slow operation thread sampler", null, new JPanel());
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
