package com.hazelcast.tricorder;

import javax.swing.*;
import javax.swing.table.DefaultTableModel;
import java.awt.*;

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

        //buildInfoTextPane.setEditable(false);
        pane =  new JScrollPane(table);
    }

    public JComponent getComponent(){
        return pane;
    }

    public void setMachine(Diagnostics machine) {
        String[] lines = machine.between(Diagnostics.TYPE_BUILD_INFO, 0, Long.MAX_VALUE).next().getValue().split("\\n");
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
