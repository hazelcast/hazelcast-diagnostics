package com.hazelcast.tricorder;

import javax.swing.*;
import java.awt.*;
import java.util.ArrayList;
import java.util.List;

public class MainWindow {
    private JFrame window;
    private List<InstanceDiagnostics> machines = new ArrayList<>();
    private SystemPropertiesPane systemPropertiesPane = new SystemPropertiesPane();
    private BuildInfoPane buildInfoPane = new BuildInfoPane();
    private InstancesPane machinesPane = new InstancesPane();
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
        machines.add(instanceDiagnostics);

        timeSelectorPane.setInstanceDiagnostics(machines);
        systemPropertiesPane.setDiagnostics(instanceDiagnostics);
        buildInfoPane.setInstanceDiagnostics(instanceDiagnostics);
        invocationProfilerPane.setInstanceDiagnostics(instanceDiagnostics);

        memoryPane.setInstanceDiagnostics(machines);
        memoryPane.update();

        cpuUtilizationPane.setInstanceDiagnostics(machines);
        cpuUtilizationPane.update();

        metricsPane.setInstanceDiagnostics(machines);
        metricsPane.update();

        invocationsPlane.setInstanceDiagnostics(machines);
        invocationsPlane.update();

        operationsPlane.setInstanceDiagnostics(machines);
        operationsPlane.update();
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

        timeSelectorPane.addChangeListener(e -> {
            long begin = timeSelectorPane.getStartMs();
            long end = timeSelectorPane.getEndMs();

            // System.out.println(begin + " " + end);
            cpuUtilizationPane.setRange(begin, end);
            cpuUtilizationPane.update();

            memoryPane.setRange(begin, end);
            memoryPane.update();

            metricsPane.setRange(begin, end);
            metricsPane.update();

            invocationsPlane.setRange(begin, end);
            invocationsPlane.update();

            operationsPlane.setRange(begin, end);
            operationsPlane.update();
        });

        mainPanel.add(timeSelectorPane.getComponent(), BorderLayout.NORTH);
        mainPanel.add(tabbedPane, BorderLayout.CENTER);

        window.setContentPane(mainPanel);
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
        tabbedPane.addTab("Invocations", null, invocationsPlane.getComponent());
        tabbedPane.addTab("Invocation Profiler", null, invocationProfilerPane.getComponent());
        tabbedPane.addTab("Operations", null, operationsPlane.getComponent());
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
