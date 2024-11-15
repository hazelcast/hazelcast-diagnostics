package com.hazelcast.diagnostics;

import com.jidesoft.swing.RangeSlider;

import javax.swing.BoundedRangeModel;
import javax.swing.JComponent;
import javax.swing.event.ChangeListener;
import java.util.Collection;

public class TimeSelectorPane {

    private final JComponent component;
    private final RangeSlider rangeSlider;
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
        long durationMs;
        long endMs;
        if (instanceDiagnosticsList.isEmpty()) {
            startMs = 0;
            return;
        }

        this.startMs = Long.MAX_VALUE;
        endMs = Long.MIN_VALUE;
        for (InstanceDiagnostics instanceDiagnostics : instanceDiagnosticsList) {
            if (instanceDiagnostics.startMs() < startMs) {
                this.startMs = instanceDiagnostics.startMs();
            }
            if (instanceDiagnostics.endMs() > endMs) {
                endMs = instanceDiagnostics.endMs();
            }
        }
        durationMs = endMs - startMs;

        BoundedRangeModel model = rangeSlider.getModel();
        model.setMinimum(0);
        model.setMaximum((int) durationMs);
        model.setValue(0);
        model.setExtent((int) durationMs);
    }
}
