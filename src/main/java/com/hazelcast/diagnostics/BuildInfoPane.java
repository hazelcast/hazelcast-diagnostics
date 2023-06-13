package com.hazelcast.diagnostics;

import javax.swing.*;
import javax.swing.table.DefaultTableModel;
import java.util.Iterator;
import java.util.Map;

public class BuildInfoPane {

    private final JTable table;
    private final DefaultTableModel model;
    private final JScrollPane pane;

    public BuildInfoPane() {
        table = new JTable();
        model = new DefaultTableModel();
        table.setModel(model);
        model.addColumn("Key");
        model.addColumn("Value");
        pane = new JScrollPane(table);
    }

    public JComponent getComponent() {
        return pane;
    }

    public void setInstanceDiagnostics(InstanceDiagnostics diagnostics) {
        model.setRowCount(0);

        if(diagnostics==null){
            return;
        }

        Iterator<Map.Entry<Long, String>> it = diagnostics.between(InstanceDiagnostics.TYPE_BUILD_INFO, 0, Long.MAX_VALUE);
        if (!it.hasNext()) {
            System.out.println("No BuildInfo found in directory: " + diagnostics.getDirectory());
            return;
        }

        String[] lines = it.next().getValue().split("\\n");
        for (String line : lines) {
            line = line.trim();
            int indexEquals = line.indexOf('=');
            if (indexEquals == -1) {
                continue;
            }

            String key = line.substring(0, indexEquals);
            String value = line.substring(indexEquals + 1).replace("]", "");
            model.addRow(new Object[]{key, value});
        }
    }
}
