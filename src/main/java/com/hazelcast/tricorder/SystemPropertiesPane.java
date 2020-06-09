package com.hazelcast.tricorder;

import javax.swing.*;
import javax.swing.table.DefaultTableModel;
import java.util.Iterator;
import java.util.Map;

public class SystemPropertiesPane {

    private final JTable table;
    private final DefaultTableModel model;
    private final JScrollPane panel;

    public SystemPropertiesPane() {
        table = new JTable();
        model = new DefaultTableModel();
        table.setModel(model);
        model.addColumn("Key");
        model.addColumn("Value");
        panel = new JScrollPane(table);
    }

    public JComponent getComponent() {
        return panel;
    }

    public void setDiagnostics(InstanceDiagnostics machine) {
        model.setRowCount(0);
        Iterator<Map.Entry<Long, String>> it = machine.between(InstanceDiagnostics.TYPE_SYSTEM_PROPERTIES, 0, Long.MAX_VALUE);
        if(!it.hasNext()){
            System.out.println("No System Properties found in directory: "+machine.getDirectory());
            return;
        }

        String[] lines = it.next().getValue().split("\\n");
        for (String line : lines) {
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
