package com.hazelcast.diagnostics;

import javax.swing.*;
import java.awt.*;
import java.io.File;
import java.util.Collection;
import java.util.HashMap;
import java.util.Map;

public class MainWindow {

    private JFrame window;
    private Map<File, InstanceDiagnostics> machines = new HashMap<>();

    private DashboardPane dashboardPane = new DashboardPane();
    private SystemPropertiesPane systemPropertiesPane = new SystemPropertiesPane();
    private BuildInfoPane buildInfoPane = new BuildInfoPane();
    private InstancesPane machinesPane;
    private InvocationProfilerPane invocationProfilerPane = new InvocationProfilerPane();
    private LatencyDistributionPane invocationLatencyDistributionPane = new LatencyDistributionPane(InstanceDiagnostics.TYPE_INVOCATION_PROFILER);
    private LatencyDistributionPane operationLatencyDistributionPane = new LatencyDistributionPane(InstanceDiagnostics.TYPE_OPERATION_PROFILER);
    private MemoryPane memoryPane = new MemoryPane();
    private CpuUtilizationPane cpuUtilizationPane = new CpuUtilizationPane();
    private MetricsPane metricsPane = new MetricsPane();
    private TimeSelectorPane timeSelectorPane = new TimeSelectorPane();
    private InvocationsPlane invocationsPlane = new InvocationsPlane();
    private OperationsPlane operationsPlane = new OperationsPlane();
    private PendingPane invocationsPendingPane = new PendingPane(InstanceDiagnostics.METRIC_OPERATION_INVOCATIONS_PENDING, "Invocations Pending");
    private PendingPane operationsPendingPane = new PendingPane(InstanceDiagnostics.METRIC_OPERATION_QUEUE_SIZE, "Operations Pending");
    private StatusBar statusBar = new StatusBar();
    private SlowOperationsPane slowOperationsPane = new SlowOperationsPane();
    private ConnectionPane connectionPane = new ConnectionPane();
    private MemberPane memberPane = new MemberPane();
    private WANPane wanPane = new WANPane();
    private HeartbeatPane heartbeatPane = new HeartbeatPane();

    public JFrame getJFrame() {
        return window;
    }

    public void add(InstanceDiagnostics instanceDiagnostics) {
        machines.put(instanceDiagnostics.getDirectory(), instanceDiagnostics);
        update();
    }

    public void remove(File directory) {
        machines.remove(directory);
        update();
    }

    private void update() {
        Collection<InstanceDiagnostics> machines = this.machines.values();

        timeSelectorPane.setInstanceDiagnostics(machines);

        if (machines.size() == 1) {
            InstanceDiagnostics instanceDiagnostics = machines.iterator().next();
            systemPropertiesPane.setInstanceDiagnostics(instanceDiagnostics);
            buildInfoPane.setInstanceDiagnostics(instanceDiagnostics);
        } else {
            systemPropertiesPane.setInstanceDiagnostics(null);
            buildInfoPane.setInstanceDiagnostics(null);
        }

        dashboardPane.setInstanceDiagnostics(machines);
        dashboardPane.update();

        invocationProfilerPane.setInstanceDiagnostics(machines);
        invocationProfilerPane.update();

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

        invocationsPendingPane.setInstanceDiagnostics(machines);
        invocationsPendingPane.update();

        invocationLatencyDistributionPane.setInstanceDiagnostics(machines);
        invocationLatencyDistributionPane.update();

        operationLatencyDistributionPane.setInstanceDiagnostics(machines);
        operationLatencyDistributionPane.update();

        operationsPendingPane.setInstanceDiagnostics(machines);
        operationsPendingPane.update();

        slowOperationsPane.setInstanceDiagnostics(machines);
        slowOperationsPane.update();

        connectionPane.setInstanceDiagnostics(machines);
        connectionPane.update();

        memberPane.setInstanceDiagnostics(machines);
        memberPane.update();

        wanPane.setInstanceDiagnostics(machines);
        wanPane.update();

        heartbeatPane.setInstanceDiagnostics(machines);
        heartbeatPane.update();
    }

    public MainWindow() {
        window = new JFrame();
        Dimension dim = Toolkit.getDefaultToolkit().getScreenSize();
        window.setSize(1440, 900);
        window.setTitle("Diagnostics Analyzer");
        window.setLocation(dim.width / 2 - window.getSize().width / 2, dim.height / 2 - window.getSize().height / 2);
        window.setLocationRelativeTo(null);
        window.setDefaultCloseOperation(WindowConstants.EXIT_ON_CLOSE);

        machinesPane = new InstancesPane(this);
        machinesPane.addDirectories(new File("data/member"), new File("data/litemember"));

        buildMenu(window);

        JTabbedPane tabbedPane = newTabbedPane();

        timeSelectorPane.addChangeListener(e -> {
            long startMs = timeSelectorPane.getStartMs();
            long endMs = timeSelectorPane.getEndMs();

            dashboardPane.setRange(startMs, endMs);
            dashboardPane.update();

            cpuUtilizationPane.setRange(startMs, endMs);
            cpuUtilizationPane.update();

            memoryPane.setRange(startMs, endMs);
            memoryPane.update();

            invocationProfilerPane.setRange(startMs, endMs);
            invocationProfilerPane.update();

            metricsPane.setRange(startMs, endMs);
            metricsPane.update();

            invocationsPlane.setRange(startMs, endMs);
            invocationsPlane.update();

            operationsPlane.setRange(startMs, endMs);
            operationsPlane.update();

            invocationsPendingPane.setRange(startMs, endMs);
            invocationsPendingPane.update();

            invocationLatencyDistributionPane.setRange(startMs, endMs);
            invocationLatencyDistributionPane.update();

            operationLatencyDistributionPane.setRange(startMs, endMs);
            operationLatencyDistributionPane.update();

            operationsPendingPane.setRange(startMs, endMs);
            operationsPendingPane.update();

            slowOperationsPane.setRange(startMs, endMs);
            slowOperationsPane.update();

            connectionPane.setRange(startMs, endMs);
            connectionPane.update();

            memberPane.setRange(startMs, endMs);
            memberPane.update();

            wanPane.setRange(startMs, endMs);
            wanPane.update();

            heartbeatPane.setRange(startMs, endMs);
            heartbeatPane.update();
        });

        JPanel analysisPanel = new JPanel();
        analysisPanel.setLayout(new BorderLayout());
        analysisPanel.add(timeSelectorPane.getComponent(), BorderLayout.NORTH);
        analysisPanel.add(tabbedPane, BorderLayout.CENTER);

        JSplitPane splitPane = new JSplitPane(JSplitPane.HORIZONTAL_SPLIT, machinesPane.getComponent(), analysisPanel);
        splitPane.setDividerLocation(250);


        JPanel mainPanel = new JPanel();
        mainPanel.setLayout(new BorderLayout());
        mainPanel.add(splitPane, BorderLayout.CENTER);
        mainPanel.add(statusBar.getComponent(), BorderLayout.SOUTH);

        window.setContentPane(mainPanel);
    }

    private JTabbedPane newTabbedPane() {
        JTabbedPane tabbedPane = new JTabbedPane();
        tabbedPane.setTabPlacement(JTabbedPane.BOTTOM);
        tabbedPane.addTab("Dashboard", null, dashboardPane.getComponent());
        tabbedPane.addTab("Memory", null, memoryPane.getComponent());
        tabbedPane.addTab("CPU", null, cpuUtilizationPane.getComponent());
        tabbedPane.addTab("Metrics", null, metricsPane.getComponent());
        tabbedPane.addTab("Build Info", null, buildInfoPane.getComponent());
        tabbedPane.addTab("System properties", null, systemPropertiesPane.getComponent());
        tabbedPane.addTab("Invocation Throughput", null, invocationsPlane.getComponent());
        tabbedPane.addTab("Invocation Profiler", null, invocationProfilerPane.getComponent());
        tabbedPane.addTab("Invocation Latency Distribution", null, invocationLatencyDistributionPane.getComponent());
        tabbedPane.addTab("Invocation Pending", null, invocationsPendingPane.getComponent());
        tabbedPane.addTab("Operations Throughput", null, operationsPlane.getComponent());
        tabbedPane.addTab("Operation Latency Distribution", null, operationLatencyDistributionPane.getComponent());
        tabbedPane.addTab("Operation Pending", null, operationsPendingPane.getComponent());
        // tabbedPane.addTab("Slow operation thread sampler", null, new JPanel());
        tabbedPane.addTab("Slow Operations", null, slowOperationsPane.getComponent());
        tabbedPane.addTab("Connections", null, connectionPane.getComponent());
        tabbedPane.addTab("Members", null, memberPane.getComponent());
        tabbedPane.addTab("WAN", null, wanPane.getComponent());
        tabbedPane.addTab("Heartbeats", null, heartbeatPane.getComponent());
        return tabbedPane;
    }

    private static void buildMenu(JFrame window) {
        JMenuBar menuBar = new JMenuBar();

        JMenu fileMenu = new JMenu("File");
        menuBar.add(fileMenu);
        window.setJMenuBar(menuBar);

        JMenuItem exitItem = new JMenuItem("Exit");
        exitItem.addActionListener(e -> System.exit(0));
        fileMenu.add(exitItem);

        JMenuItem gcItem = new JMenuItem("GC");
        gcItem.addActionListener(e -> System.gc());
        fileMenu.add(gcItem);
    }
}
