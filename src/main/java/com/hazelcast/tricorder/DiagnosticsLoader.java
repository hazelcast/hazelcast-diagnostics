package com.hazelcast.tricorder;

import java.io.File;
import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ForkJoinPool;

import static java.util.stream.Collectors.toList;

public final class DiagnosticsLoader {

    private DiagnosticsLoader() {
    }

    static List<InstanceDiagnostics> load(List<File> directories) {
        ForkJoinPool threadPool = new ForkJoinPool();
        try {
            return threadPool.submit(
                    () -> directories.stream().parallel()
                                     .map(directory -> new InstanceDiagnostics(directory).analyze())
                                     .collect(toList())
            ).get();
        } catch (InterruptedException | ExecutionException e) {
            throw new RuntimeException(e);
        }
    }
}
