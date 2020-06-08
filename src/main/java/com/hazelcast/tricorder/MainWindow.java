package com.hazelcast.tricorder;

import com.jidesoft.swing.RangeSlider;

import javax.swing.*;
import javax.swing.event.ChangeEvent;
import javax.swing.event.ChangeListener;
import java.awt.*;
import java.util.ArrayList;
import java.util.List;

public class MainWindow {
    private final RangeSlider rangeSlider;
    private JFrame window;
    private List<Machine> machines = new ArrayList<Machine>();
    private JTextPane propertiesTextPane;
    private JTextPane buildInfoTextPane;

    public JFrame getJFrame() {
        return window;
    }

    public void add(Machine machine) {
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

        propertiesTextPane.setText(machine.getItems(Machine.TYPE_SYSTEM_PROPERTIES, 0, Long.MAX_VALUE).next().getValue());
        buildInfoTextPane.setText(machine.getItems(Machine.TYPE_BUILD_INFO, 0, Long.MAX_VALUE).next().getValue());
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
        rangeSlider.addChangeListener(new ChangeListener() {
            @Override
            public void stateChanged(ChangeEvent e) {
                System.out.println(e);
            }
        });
        rangeSlider.setRangeDraggable(true);
        //     JPanel timePanel = new JPanel();
        return rangeSlider;
    }

    private JTabbedPane newTabbedPane() {
        JTabbedPane tabbedPane = new JTabbedPane();
        tabbedPane.setTabPlacement(JTabbedPane.BOTTOM);

        JComponent panel1 = new JPanel();
        tabbedPane.addTab("Metrics", null, panel1);

        JComponent panel2 = new JPanel();
        tabbedPane.addTab("Build Info", null, newBuildInfoPane());

        tabbedPane.addTab("System properties", null, newPropertiesPane());

        JComponent panel4 = new JPanel();
        tabbedPane.addTab("Slow Operations", null, panel4);

        JComponent panel5 = new JPanel();
        tabbedPane.addTab("Invocations", null, panel5);

        JComponent panel6 = new JPanel();
        tabbedPane.addTab("Invocation Profiler", null, panel6);

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
