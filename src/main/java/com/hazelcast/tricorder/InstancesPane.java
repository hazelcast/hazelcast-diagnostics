package com.hazelcast.tricorder;

import javax.swing.*;
import javax.swing.filechooser.FileFilter;
import java.awt.*;
import java.io.File;
import java.io.IOException;

public class InstancesPane {

    private static final String FILE_CHOOSER_DIALOG_TITLE = "Choose instance directory";
    private static final String FILE_CHOOSER_FILTER_TEXT = "Instance directory";
    private static final String ADD_INSTANCE_BUTTON_LABEL = "Add instance";
    private static final String REMOVE_INSTANCE_LABEL = "Remove instance";

    private final JPanel component;

    private final MainWindow window;
    private final DefaultListModel<File> listModel;

    public InstancesPane(MainWindow window) {
        this.window = window;
        this.listModel = new DefaultListModel<>();

        JPanel panel = new JPanel(new BorderLayout(), true);

        JPanel buttonsPanel = new JPanel();
        buttonsPanel.add(createAddInstanceButton(buttonsPanel));
        panel.add(buttonsPanel, BorderLayout.NORTH);

        JList<File> instancesList = new JList<>(listModel);
        instancesList.setComponentPopupMenu(createInstanceListMenu(instancesList));
        panel.add(instancesList, BorderLayout.CENTER);

        this.component = panel;
    }

    private JButton createAddInstanceButton(Component parent) {
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

        JButton addInstanceButton = new JButton();
        addInstanceButton.setText(ADD_INSTANCE_BUTTON_LABEL);
        addInstanceButton.addActionListener(e -> {
            int returnVal = fc.showOpenDialog(parent);
            if (returnVal == JFileChooser.APPROVE_OPTION) {
                File[] directories = fc.getSelectedFiles();
                for (File directory : directories) {
                    addDirectory(directory);
                }
            }
        });

        return addInstanceButton;
    }

    private JPopupMenu createInstanceListMenu(JList<File> list) {
        return new JPopupMenu() {
            {
                JMenuItem removeItem = new JMenuItem(REMOVE_INSTANCE_LABEL);
                removeItem.addActionListener(e -> {
                    if (list.getSelectedIndex() >= 0) {
                        removeDirectory(listModel.remove(list.getSelectedIndex()));
                    }
                });
                add(removeItem);
            }

            @Override
            public void show(Component invoker, int x, int y) {
                int row = list.locationToIndex(new Point(x, y));
                if (row != -1) {
                    list.setSelectedIndex(row);
                }
                super.show(invoker, x, y);
            }
        };
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
