package com.hazelcast.tricorder;

import javax.swing.*;
import java.awt.*;
import java.awt.event.ActionEvent;
import java.awt.event.ActionListener;
import java.io.File;

public class InstancesPane {

    private final JPanel component;

    public InstancesPane() {
        JPanel panel = new JPanel();

        final JFileChooser fc = new JFileChooser();
        fc.setFileSelectionMode(JFileChooser.DIRECTORIES_ONLY);
        fc.setMultiSelectionEnabled(true);

        JList list = new JList();
        DefaultListModel listModel = new DefaultListModel();
        list.setModel(listModel);
        JButton button = new JButton();
        button.setText("Add instance directories");
        button.addActionListener(new ActionListener() {
            @Override
            public void actionPerformed(ActionEvent e) {
                int returnVal = fc.showOpenDialog(panel);
                if (returnVal == JFileChooser.APPROVE_OPTION) {
                    File[] files = fc.getSelectedFiles();
                    for (File file : files) {
                        if (!listModel.contains(file)) {
                            listModel.addElement(file);
                        }
                    }
                }
            }
        });

        panel.setLayout(new BorderLayout());
        panel.add(list, BorderLayout.CENTER);
        panel.add(button, BorderLayout.WEST);
        this.component = panel;
    }

    public JComponent getComponent(){
        return component;
    }
}
