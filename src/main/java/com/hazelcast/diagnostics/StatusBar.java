package com.hazelcast.diagnostics;

import javax.swing.JComponent;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.Timer;
import javax.swing.border.EmptyBorder;
import java.awt.BorderLayout;

public class StatusBar {

    private final JComponent component;

    public StatusBar(){
        JPanel statusPanel = new JPanel();
        statusPanel.setBorder(new EmptyBorder(5,5,5,5));
        statusPanel.setLayout(new BorderLayout());

        JLabel label = new JLabel();
        label.setText("foobar");
        statusPanel.add(label, BorderLayout.EAST);

        this.component = statusPanel;

        Timer timer = new Timer(1000, e -> {
            Runtime runtime = Runtime.getRuntime();
            long totalMemory = runtime.totalMemory();
            long freeMemory = runtime.freeMemory();
            long usedMemory = totalMemory - freeMemory;
            label.setText(toMegabytes(usedMemory) + " of " + toMegabytes(totalMemory) + "M");
        });
        timer.setInitialDelay(0);
        timer.start();
    }

    public static long toMegabytes(long l){
        return l / (1024*1024);
    }

    public JComponent getComponent(){
        return component;
    }
}
