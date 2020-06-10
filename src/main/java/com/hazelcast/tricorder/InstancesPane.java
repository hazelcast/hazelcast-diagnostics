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

import static com.hazelcast.tricorder.DiagnosticsLoader.load;
import static java.util.stream.IntStream.range;

public class InstancesPane {

    private static final String FILE_CHOOSER_DIALOG_TITLE = "Choose instance directory";
    private static final String FILE_CHOOSER_FILTER_TEXT = "Instance directory";
    private static final String ADD_INSTANCE_BUTTON_LABEL = "Add";
    private static final String CLEAR_INSTANCES_LABEL = "Clear";
    private static final String REMOVE_INSTANCE_LABEL = "Remove";

    private final JPanel component;

    private final InstanceListModel listModel;
    private final JList<?> list;

    public InstancesPane(MainWindow window) {
        this.listModel = new InstanceListModel(window);
        this.list = createInstanceList(listModel);

        JPanel panel = new JPanel(new BorderLayout(), true);

        JPanel buttonsPanel = new JPanel();
        buttonsPanel.add(createAddInstanceButton(buttonsPanel, listModel), BorderLayout.WEST);
        buttonsPanel.add(createClearInstancesButton(listModel), BorderLayout.EAST);
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
            if (fileChooser.showOpenDialog(parent) == JFileChooser.APPROVE_OPTION) {
                listModel.addElements(fileChooser.getSelectedFiles());
            }
        });
        return button;
    }

    private JButton createClearInstancesButton(InstanceListModel listModel) {
        JButton button = new JButton();
        button.setText(CLEAR_INSTANCES_LABEL);
        button.addActionListener(e -> listModel.clearElements());
        return button;
    }

    private JList<String> createInstanceList(InstanceListModel listModel) {
        JList<String> list = new JList<>(listModel);
        list.getSelectionModel().addListSelectionListener(listModel);
        list.setComponentPopupMenu(createInstanceListMenu(listModel, list));
        return list;
    }

    private JPopupMenu createInstanceListMenu(InstanceListModel listModel, JList<?> list) {
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
        listModel.addElements(directories);
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

        void addElements(File... dirs) {
            List<File> canonicalDirs = new ArrayList<>();
            for (File dir : dirs) {
                try {
                    File canonicalDir = dir.getCanonicalFile();
                    if (!directories.contains(canonicalDir)) {
                        canonicalDirs.add(canonicalDir);
                    }
                } catch (IOException e) {
                    throw new RuntimeException(e);
                }
            }

            for (InstanceDiagnostics diagnostics : load(canonicalDirs)) {
                directories.add(diagnostics.getDirectory());
                instances.add(diagnostics);

                int index = directories.size() - 1;
                fireIntervalAdded(this, index, index);
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

        void clearElements() {
            removeElements(range(0, directories.size()).toArray());
        }

        @Override
        public int getSize() {
            return directories.size();
        }

        @Override
        public String getElementAt(int i) {
            return directories.get(i).getName();
        }

        @Override
        public void valueChanged(ListSelectionEvent e) {
            ListSelectionModel model = (ListSelectionModel) e.getSource();
            if (!model.getValueIsAdjusting()) {
                for (int i = 0; i < directories.size(); i++) {
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
