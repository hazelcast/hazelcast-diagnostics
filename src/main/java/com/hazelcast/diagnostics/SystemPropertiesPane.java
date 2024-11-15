package com.hazelcast.diagnostics;

import javax.swing.JComponent;
import javax.swing.JScrollPane;
import javax.swing.JTable;
import javax.swing.table.DefaultTableModel;
import java.util.Iterator;
import java.util.Map;

import static com.hazelcast.diagnostics.InstanceDiagnostics.DiagnosticType.TYPE_SYSTEM_PROPERTIES;

public class SystemPropertiesPane {

    private final DefaultTableModel model;
    private final JScrollPane panel;

    public SystemPropertiesPane() {
        JTable table = new JTable();
        model = new DefaultTableModel();
        table.setModel(model);
        model.addColumn("Key");
        model.addColumn("Value");
        panel = new JScrollPane(table);
    }

    public JComponent getComponent() {
        return panel;
    }

    public void setInstanceDiagnostics(InstanceDiagnostics machine) {
        model.setRowCount(0);
        if(machine == null){
            return;
        }

        Iterator<Map.Entry<Long, String>> it = machine.between(TYPE_SYSTEM_PROPERTIES, 0, Long.MAX_VALUE);
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

            String key = line.substring(0, indexEquals).trim();
            String value = line.substring(indexEquals + 1).replace("]", "");
            model.addRow(new Object[]{key, value});
        }
    }
}
