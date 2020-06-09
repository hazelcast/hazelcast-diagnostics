package com.hazelcast.tricorder;

import javax.swing.*;
import javax.swing.filechooser.FileFilter;
import java.awt.*;
import java.io.File;
import java.io.IOException;

public class InstancesPane {

    private static final String FILE_CHOOSER_DIALOG_TITLE = "Choose instance directory";
    private static final String FILE_CHOOSER_FILTER_TEXT = "Instance directory";
    private static final String ADD_INSTANCE_BUTTON_LABEL = "Add instance directories";
    private static final String REMOVE_INSTANCE_BUTTON_LABEL = "Remove instance directories";

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
                File[] directories = fc.getSelectedFiles();
                for (File directory : directories) {
                    addDirectory(directory);
                }
            }
        });

        JButton removeInstanceButton = new JButton();
        removeInstanceButton.setText(REMOVE_INSTANCE_BUTTON_LABEL);
        removeInstanceButton.addActionListener(e -> {
            if (list.getSelectedIndex() >= 0) {
                removeDirectory(listModel.remove(list.getSelectedIndex()));
            }
        });

        panel.setLayout(new BorderLayout());
        panel.add(list, BorderLayout.CENTER);

        JPanel buttonPanel = new JPanel();
        buttonPanel.add(addInstanceButton);
        buttonPanel.add(removeInstanceButton);
        panel.add(buttonPanel, BorderLayout.NORTH);
        this.component = panel;
    }

    void addDirectory(File directory) {
        try {
            File canonicalDirectory = directory.getCanonicalFile();

            if (!listModel.contains(canonicalDirectory)) {
                listModel.addElement(canonicalDirectory);

                InstanceDiagnostics instance = new InstanceDiagnostics(canonicalDirectory);
                instance.analyze();
                window.add(instance);
            }
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }

    void removeDirectory(File directory) {
        try {
            File canonicalDirectory = directory.getCanonicalFile();

            window.remove(canonicalDirectory);
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }

    public JComponent getComponent() {
        return component;
    }
}
