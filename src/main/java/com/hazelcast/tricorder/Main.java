package com.hazelcast.tricorder;

import javax.swing.*;
import java.awt.*;
import java.io.File;
import java.util.Iterator;
import java.util.Map;

public class Main {

    public static void main(String[] args) {
        JFrame frame = newFrame();
        frame.setVisible(true);

//        Machine client = new Machine();
//        client.setDirectory(new File("/java/tests/profiler/2020-06-03__11_13_44/A2_W1-3.80.130.125-litemember/"));
//        client.analyze();

        Machine server = new Machine();
        server.setDirectory(new File("/java/tests/profiler/2020-06-03__11_13_44/A1_W1-3.83.248.216-member/"));
        server.analyze();

        Iterator<Map.Entry<Long, String>> s = server.getItems(Machine.TYPE_INVOCATION_PROFILER, Long.MIN_VALUE, Long.MAX_VALUE);
        while (s.hasNext()) {
            System.out.println(s.next().getValue());
        }
    }

    private static JFrame newFrame() {
        JFrame window = new JFrame();
        Dimension dim = Toolkit.getDefaultToolkit().getScreenSize();
        window.setSize(1024, 768);
        window.setLocation(dim.width / 2 - window.getSize().width / 2, dim.height / 2 - window.getSize().height / 2);
        window.setLocationRelativeTo(null);

        JMenuBar menuBar = new JMenuBar();

        JMenu fileMenu = new JMenu("File");
        menuBar.add(fileMenu);
        window.setJMenuBar(menuBar);


        JMenuItem menuItem = new JMenuItem("Load");
        fileMenu.add(menuItem);

        JMenuItem exitItem = new JMenuItem("Exit");
        exitItem.addActionListener(e -> System.exit(0));
        fileMenu.add(exitItem);


        JTabbedPane tabbedPane = new JTabbedPane();
        tabbedPane.setTabPlacement(JTabbedPane.LEFT);

        JComponent panel1 = new JPanel();
        tabbedPane.addTab("Metrics", null, panel1);

        JComponent panel2 = new JPanel();
        tabbedPane.addTab("Build Info", null, panel2);

        JComponent panel3 = new JPanel();
        tabbedPane.addTab("System properties", null, panel3);

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

//        public static final int TYPE_OPERATION_THREAD_SAMPLES = 9;
//        public static final int TYPE_CONNECTION_REMOVED = 10;
//        public static final int TYPE_HAZELCAST_INSTANCE = 11;
//        public static final int TYPE_MEMBER_REMOVED = 12;
//        public static final int TYPE_MEMBER_ADDED = 13;
//        public static final int TYPE_CLUSTER_VERSION_CHANGE = 14;
//        public static final int TYPE_LIFECYCLE = 15;
//        public static final int TYPE_CONNECTION_ADDED = 16;
//        public static final int TYPES = TYPE_CONNECTION_ADDED + 1;


        window.setContentPane(tabbedPane);
        return window;
    }
}
