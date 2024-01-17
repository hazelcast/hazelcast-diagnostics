# Diagnostics Analyzer

Analyzes the [collected diagnostics](https://docs.hazelcast.com/hazelcast/latest/maintain-cluster/monitoring#diagnostics) of Hazelcast

----

![Screenshot](./images/screenshot.png)

----

## Launching
Launch `com.hazelcast.diagnostics.Main.main(String[])`

## Troubleshooting

- `java.lang.IllegalAccessError: class com.jidesoft.plaf.LookAndFeelFactory [...] cannot access class com.sun.java.swing.plaf.windows.WindowsLookAndFeel [...] because module java.desktop does not export com.sun.java.swing.plaf.windows to unnamed module`
  - Add `--add-exports java.desktop/com.sun.java.swing.plaf.windows=ALL-UNNAMED` VM argument
