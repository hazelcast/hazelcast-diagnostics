package com.hazelcast.tricorder;

import javax.swing.*;
import java.util.Iterator;
import java.util.Map;

public class MemoryPane {

    private final JPanel component;

    public MemoryPane(){
        JPanel panel = new JPanel();
        this.component = panel;
    }

    public void setInstanceDiagnostics(InstanceDiagnostics diagnostics) {
        Iterator<Map.Entry<Long, String>> iterator = diagnostics.metricsbetween("[metric=runtime.usedMemory]",Long.MIN_VALUE, Long.MAX_VALUE);

        if(!iterator.hasNext()){
            System.out.println("No BuildInfo found in directory: "+diagnostics.getDirectory());
            return;
        }

        do{
            System.out.println(iterator.next());
        }while (iterator.hasNext());
    }

    public JComponent getComponent(){
        return component;
    }
}
