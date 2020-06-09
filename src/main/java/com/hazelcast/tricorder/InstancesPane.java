package com.hazelcast.tricorder;

import javax.swing.*;
import javax.swing.event.ListSelectionEvent;
import javax.swing.event.ListSelectionListener;
import javax.swing.filechooser.FileFilter;
import java.awt.*;
import java.io.File;
import java.io.IOException;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

public class InstancesPane {

    private static final String FILE_CHOOSER_DIALOG_TITLE = "Choose instance directory";
    private static final String FILE_CHOOSER_FILTER_TEXT = "Instance directory";
    private static final String ADD_INSTANCE_BUTTON_LABEL = "Add";
    private static final String REMOVE_INSTANCE_LABEL = "Remove";

    private final JPanel component;

    private final InstanceListModel listModel;
    private final JList<String> list;

    public InstancesPane(MainWindow window) {
        this.listModel = new InstanceListModel(window);
        this.list = createInstanceList(listModel);

        JPanel panel = new JPanel(new BorderLayout(), true);

        JPanel buttonsPanel = new JPanel();
        buttonsPanel.add(createAddInstanceButton(buttonsPanel, listModel));
        panel.add(buttonsPanel, BorderLayout.NORTH);

        panel.add(list, BorderLayout.CENTER);

        this.component = panel;
    }

    private JButton createAddInstanceButton(Component parent, InstanceListModel listModel) {
        JFileChooser fileChooser = new JFileChooser();
        fileChooser.setDialogTitle(FILE_CHOOSER_DIALOG_TITLE);
        fileChooser.setCurrentDirectory(new File("."));
        fileChooser.setFileSelectionMode(JFileChooser.DIRECTORIES_ONLY);
        fileChooser.setMultiSelectionEnabled(true);
        fileChooser.setAcceptAllFileFilterUsed(false);
        fileChooser.setFileFilter(new FileFilter() {
            @Override
            public boolean accept(File file) {
                return file.isDirectory();
            }

            @Override
            public String getDescription() {
                return FILE_CHOOSER_FILTER_TEXT;
            }
        });

        JButton button = new JButton();
        button.setText(ADD_INSTANCE_BUTTON_LABEL);
        button.addActionListener(e -> {
            int returnVal = fileChooser.showOpenDialog(parent);
            if (returnVal == JFileChooser.APPROVE_OPTION) {
                File[] directories = fileChooser.getSelectedFiles();
                for (File directory : directories) {
                    listModel.addElement(directory);
                }
            }
        });
        return button;
    }

    private JList<String> createInstanceList(InstanceListModel listModel) {
        JList<String> list = new JList<>(listModel);
        list.getSelectionModel().addListSelectionListener(listModel);
        list.setComponentPopupMenu(createInstanceListMenu(list, listModel));
        return list;
    }

    private JPopupMenu createInstanceListMenu(JList<String> list, InstanceListModel listModel) {
        return new JPopupMenu() {
            {
                JMenuItem removeItem = new JMenuItem(REMOVE_INSTANCE_LABEL);
                removeItem.addActionListener(e -> {
                    listModel.removeElements(list.getSelectedIndices());
                    list.clearSelection();
                });
                add(removeItem);
            }

            @Override
            public void show(Component invoker, int x, int y) {
                int row = list.locationToIndex(new Point(x, y));
                if (row != -1) {
                    int index = list.getSelectedIndex();
                    list.getSelectionModel().addSelectionInterval(index, index);
                }
                super.show(invoker, x, y);
            }
        };
    }

    @Deprecated
    void addDirectories(File... directories) {
        for (File directory : directories) {
            listModel.addElement(directory);
        }
        list.getSelectionModel().setSelectionInterval(0, directories.length - 1);
    }

    public JComponent getComponent() {
        return component;
    }

    private static class InstanceListModel extends AbstractListModel<String> implements ListSelectionListener {

        private final MainWindow window;

        private final List<File> directories;
        private final List<InstanceDiagnostics> instances;
        private final Set<Integer> selectedInstances;

        public InstanceListModel(MainWindow window) {
            this.window = window;

            this.directories = new ArrayList<>();
            this.instances = new ArrayList<>();
            this.selectedInstances = new HashSet<>();
        }

        void addElement(File directory) {
            File canonicalDirectory;
            try {
                canonicalDirectory = directory.getCanonicalFile();
            } catch (IOException e) {
                throw new RuntimeException(e);
            }

            if (!directories.contains(canonicalDirectory)) {
                directories.add(canonicalDirectory);
                instances.add(new InstanceDiagnostics(canonicalDirectory).analyze());

                fireIntervalAdded(this, instances.size(), instances.size());
            }
        }

        void removeElements(int... indices) {
            for (int i = indices.length - 1; i >= 0; i--) {
                int index = indices[i];
                fireIntervalRemoved(this, index, index);

                instances.remove(index);
                directories.remove(index);
            }
        }

        @Override
        public int getSize() {
            return instances.size();
        }

        @Override
        public String getElementAt(int i) {
            return directories.get(i).getName();
        }

        @Override
        public void valueChanged(ListSelectionEvent e) {
            ListSelectionModel model = (ListSelectionModel) e.getSource();
            if (!model.getValueIsAdjusting()) {
                for (int i = 0; i <= instances.size(); i++) {
                    if (model.isSelectedIndex(i) && selectedInstances.add(i)) {
                        window.add(instances.get(i));
                    }

                    if (!model.isSelectedIndex(i) && selectedInstances.remove(i)) {
                        window.remove(directories.get(i));
                    }
                }
            }
        }
    }
}
