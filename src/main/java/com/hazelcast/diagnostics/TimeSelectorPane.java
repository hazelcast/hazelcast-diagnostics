package com.hazelcast.diagnostics;

import com.jidesoft.swing.RangeSlider;

import javax.swing.*;
import javax.swing.event.ChangeListener;
import java.util.Collection;

public class TimeSelectorPane {

    private final JComponent component;
    private final RangeSlider rangeSlider;
    private long durationMs;
    private long endMs;
    private long startMs;

    public TimeSelectorPane() {
        this.rangeSlider = new RangeSlider();
        rangeSlider.setRangeDraggable(true);
        this.component = rangeSlider;
    }

    public void addChangeListener(ChangeListener changeListener) {
        rangeSlider.addChangeListener(changeListener);
    }

    public long getStartMs() {
        return rangeSlider.getLowValue() + startMs;
    }

    public long getEndMs() {
        return rangeSlider.getHighValue() + startMs;
    }

    public JComponent getComponent() {
        return this.component;
    }

    public void setInstanceDiagnostics(Collection<InstanceDiagnostics> instanceDiagnosticsList) {
        if (instanceDiagnosticsList.isEmpty()) {
            startMs = 0;
            endMs = 0;
            durationMs = 0;
            return;
        }

        this.startMs = Long.MAX_VALUE;
        this.endMs = Long.MIN_VALUE;
        for (InstanceDiagnostics instanceDiagnostics : instanceDiagnosticsList) {
            if (instanceDiagnostics.startMs() < startMs) {
                this.startMs = instanceDiagnostics.startMs();
            }
            if (instanceDiagnostics.endMs() > endMs) {
                this.endMs = instanceDiagnostics.endMs();
            }
        }
        this.durationMs = endMs - startMs;

        BoundedRangeModel model = rangeSlider.getModel();
        model.setMinimum(0);
        model.setMaximum((int) durationMs);
        model.setValue(0);
        model.setExtent((int) durationMs);
    }
}
