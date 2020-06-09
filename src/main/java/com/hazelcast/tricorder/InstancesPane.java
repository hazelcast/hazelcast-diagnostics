package com.hazelcast.tricorder;

import javax.swing.*;
import javax.swing.filechooser.FileFilter;
import java.awt.*;
import java.io.File;

public class InstancesPane {

    private static final String FILE_CHOOSER_DIALOG_TITLE = "Choose instance directory";
    private static final String FILE_CHOOSER_FILTER_TEXT = "Instance directory";
    private static final String ADD_INSTANCE_BUTTON_LABEL = "Add instance directories";

    private final JPanel component;

    private final MainWindow window;
    private final DefaultListModel<File> listModel;

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

        this.window = window;
        this.listModel = new DefaultListModel<>();
        JList<File> list = new JList<>();
        list.setModel(listModel);

        JButton addInstanceButton = new JButton();
        addInstanceButton.setText(ADD_INSTANCE_BUTTON_LABEL);
        addInstanceButton.addActionListener(e -> {
            int returnVal = fc.showOpenDialog(panel);
            if (returnVal == JFileChooser.APPROVE_OPTION) {
                File[] files = fc.getSelectedFiles();
                for (File file : files) {
                    if (!listModel.contains(file)) {
                        addFile(file);
                    }
                }
            }
        });

        panel.setLayout(new BorderLayout());
        panel.add(list, BorderLayout.CENTER);
        panel.add(addInstanceButton, BorderLayout.WEST);
        this.component = panel;
    }

    @Deprecated
    void addFile(File file) {
        listModel.addElement(file);

        InstanceDiagnostics instance = new InstanceDiagnostics(file);
        instance.analyze();
        window.add(instance);
    }

    public JComponent getComponent() {
        return component;
    }
}
