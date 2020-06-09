package com.hazelcast.tricorder;

import javax.swing.*;
import javax.swing.filechooser.FileFilter;
import java.awt.*;
import java.io.File;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

public class InstancesPane {

    private static final String FILE_CHOOSER_DIALOG_TITLE = "Choose instance directory";
    private static final String FILE_CHOOSER_FILTER_TEXT = "Instance directory";
    private static final String ADD_INSTANCE_BUTTON_LABEL = "Add";
    private static final String REMOVE_INSTANCE_LABEL = "Remove";

    private final JPanel component;

    private final MainWindow window;
    private final InstanceListModel listModel;

    public InstancesPane(MainWindow window) {
        this.window = window;
        this.listModel = new InstanceListModel();

        JPanel panel = new JPanel(new BorderLayout(), true);

        JPanel buttonsPanel = new JPanel();
        buttonsPanel.add(createAddInstanceButton(buttonsPanel));
        panel.add(buttonsPanel, BorderLayout.NORTH);

        JList<String> instancesList = new JList<>(listModel);
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
                    listModel.addElement(directory);
                }
            }
        });

        return addInstanceButton;
    }

    private JPopupMenu createInstanceListMenu(JList<String> list) {
        return new JPopupMenu() {
            {
                JMenuItem removeItem = new JMenuItem(REMOVE_INSTANCE_LABEL);
                removeItem.addActionListener(e -> {
                    if (list.getSelectedIndex() >= 0) {
                        listModel.removeElement(list.getSelectedIndex());
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

    @Deprecated
    void addDirectory(File directory) {
        listModel.addElement(directory);
    }

    public JComponent getComponent() {
        return component;
    }

    private class InstanceListModel extends AbstractListModel<String> {

        private final List<File> instances;

        public InstanceListModel() {
            this.instances = new ArrayList<>();
        }

        void addElement(File directory) {
            File canonicalDirectory = canonicalize(directory);

            if (!instances.contains(canonicalDirectory)) {
                instances.add(canonicalDirectory);
                fireIntervalAdded(this, instances.size(), instances.size());

                InstanceDiagnostics instance = new InstanceDiagnostics(canonicalDirectory);
                instance.analyze();
                window.add(instance);
            }
        }

        void removeElement(int i) {
            File directory = instances.remove(i);
            fireIntervalRemoved(this, instances.size(), instances.size());

            window.remove(directory);
        }

        private File canonicalize(File file) {
            try {
                return file.getCanonicalFile();
            } catch (IOException e) {
                throw new RuntimeException(e);
            }
        }

        @Override
        public int getSize() {
            return instances.size();
        }

        @Override
        public String getElementAt(int i) {
            return instances.get(i).getName();
        }
    }
}
