package com.hazelcast.diagnostics;

import javax.swing.UIDefaults;
import javax.swing.UIManager;
import javax.swing.plaf.FontUIResource;
import java.awt.Font;
import java.util.Enumeration;

public class Main {

    public static void main(String[] args) {
        enableAntiAliasing();
        incFontSize(1.5f);

        MainWindow mainWindow = new MainWindow();
        mainWindow.getJFrame().setVisible(true);
    }

    private static void enableAntiAliasing() {
        System.setProperty("awt.useSystemAAFontSettings", "on");
        System.setProperty("swing.aatext", "true");
    }

    public static void incFontSize(float multiplier) {
        UIDefaults defaults = UIManager.getDefaults();
        for (Enumeration<Object> e = defaults.keys(); e.hasMoreElements(); ) {
            Object key = e.nextElement();
            Object value = defaults.get(key);
            if (value instanceof Font) {
                Font font = (Font) value;
                int newSize = Math.round(font.getSize() * multiplier);
                if (value instanceof FontUIResource) {
                    defaults.put(key, new FontUIResource(font.getName(), font.getStyle(), newSize));
                } else {
                    defaults.put(key, new Font(font.getName(), font.getStyle(), newSize));
                }
            }
        }
    }
}
