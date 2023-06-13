package com.hazelcast.diagnostics;

import javax.swing.*;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

public class MemberPane {

    private final JComponent component;
    private final JTextArea textArea;
    private Collection<InstanceDiagnostics> diagnosticsList;
    private long startMs = Long.MIN_VALUE;
    private long endMs = Long.MAX_VALUE;

    public MemberPane() {
        this.textArea = new JTextArea();
        textArea.setEditable(false);
        this.component = new JScrollPane(textArea);
    }

    public void setRange(long fromMs, long toMs) {
        this.startMs = fromMs;
        this.endMs = toMs;
    }

    public void setInstanceDiagnostics(Collection<InstanceDiagnostics> diagnosticsList) {
        this.diagnosticsList = diagnosticsList;
    }

    public void update() {
        this.textArea.setText("");
        if (diagnosticsList == null) {
            return;
        }

        TreeMap<Long, List<String>> treeMap = new TreeMap();
        for (InstanceDiagnostics diagnostics : diagnosticsList) {
            Iterator<Map.Entry<Long, String>> iterator = diagnostics.between(InstanceDiagnostics.TYPE_MEMBER, startMs, endMs);

            if (!iterator.hasNext()) {
                continue;
            }

            while (iterator.hasNext()) {
                Map.Entry<Long, String> entry = iterator.next();
                Long key = entry.getKey();
                List<String> list = treeMap.computeIfAbsent(key, k -> new ArrayList<>());
                list.add(entry.getValue());
            }
        }

        for (Map.Entry<Long, List<String>> entry : treeMap.entrySet()){
           for(String s: entry.getValue()){
               textArea.append(s);
               textArea.append("\n");
           }
        }
    }

    public JComponent getComponent() {
        return component;
    }
}
