package com.hazelcast.tricorder;

import javax.swing.*;
import javax.swing.filechooser.FileFilter;
import java.awt.*;
import java.io.File;

public class InstancesPane {

    private static final String FILE_CHOOSER_DIALOG_TITLE = "Choose instance directory";
    private static final String FILE_CHOOSER_FILTER_TEXT = "Instance directory";
    private static final String BUTTON_LABEL = "Add instance directories";

    private final JPanel component;

    public InstancesPane(MainWindow window) {
        JPanel panel = new JPanel();

        JFileChooser fc = new JFileChooser();
        fc.setDialogTitle(FILE_CHOOSER_DIALOG_TITLE);
        fc.setCurrentDirectory(new File("."));
        fc.setFileSelectionMode(JFileChooser.DIRECTORIES_ONLY);
        fc.setMultiSelectionEnabled(true);
        fc.setAcceptAllFileFilterUsed(false);
        fc.setFileFilter(new FileFilter() {
            @Override
            public boolean accept(File f) {
                return f.isDirectory();
            }

            @Override
            public String getDescription() {
                return FILE_CHOOSER_FILTER_TEXT;
            }
        });

        JList<File> list = new JList<>();
        DefaultListModel<File> listModel = new DefaultListModel<>();
        list.setModel(listModel);

        JButton button = new JButton();
        button.setText(BUTTON_LABEL);
        button.addActionListener(e -> {
            int returnVal = fc.showOpenDialog(panel);
            if (returnVal == JFileChooser.APPROVE_OPTION) {
                File[] files = fc.getSelectedFiles();
                for (File file : files) {
                    if (!listModel.contains(file)) {
                        listModel.addElement(file);

                        InstanceDiagnostics instance = new InstanceDiagnostics(file);
                        instance.analyze();
                        window.add(instance);
                    }
                }
            }
        });

        panel.setLayout(new BorderLayout());
        panel.add(list, BorderLayout.CENTER);
        panel.add(button, BorderLayout.WEST);
        this.component = panel;
    }

    public JComponent getComponent() {
        return component;
    }
}
