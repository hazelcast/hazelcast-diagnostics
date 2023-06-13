package com.hazelcast.diagnostics;

import javax.swing.*;
import javax.swing.border.EmptyBorder;
import java.awt.*;
import java.awt.event.ActionEvent;
import java.awt.event.ActionListener;

public class StatusBar {

    private final JComponent component;
    private final Timer timer;

    public StatusBar(){
        JPanel statusPanel = new JPanel();
        statusPanel.setBorder(new EmptyBorder(5,5,5,5));
        statusPanel.setLayout(new BorderLayout());

        JLabel label = new JLabel();
        label.setText("foobar");
        statusPanel.add(label, BorderLayout.EAST);

        this.component = statusPanel;

        timer = new Timer(1000, new ActionListener() {
            @Override
            public void actionPerformed(ActionEvent e) {
                Runtime runtime = Runtime.getRuntime();
                long totalMemory = runtime.totalMemory();
                long freeMemory = runtime.freeMemory();
                long usedMemory = totalMemory-freeMemory;
                label.setText(toMegabytes(usedMemory)+" of "+toMegabytes(totalMemory)+"M");
            }
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
