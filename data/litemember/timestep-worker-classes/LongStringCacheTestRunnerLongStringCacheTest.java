import com.hazelcast.simulator.test.*;
import com.hazelcast.simulator.test.annotations.*;
import com.hazelcast.simulator.worker.testcontainer.*;
import com.hazelcast.simulator.worker.*;
import com.hazelcast.simulator.worker.metronome.*;
import com.hazelcast.simulator.probes.*;
import com.hazelcast.simulator.utils.*;

import org.apache.log4j.*;

import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

public class LongStringCacheTestRunnerLongStringCacheTest extends TimeStepRunner {

    public LongStringCacheTestRunnerLongStringCacheTest(com.hazelcast.simulator.hz.cache.LongStringCacheTest testInstance, TimeStepModel model, String executionGroup) {
        super(testInstance, model, executionGroup);
    }

    @Override
    public void timeStepLoop() throws Exception {
        final AtomicLong iterations = this.iterations;
        final TestContextImpl testContext = (TestContextImpl)this.testContext;
        final com.hazelcast.simulator.hz.cache.LongStringCacheTest testInstance = (com.hazelcast.simulator.hz.cache.LongStringCacheTest)this.testInstance;
        final com.hazelcast.simulator.probes.impl.HdrProbe getProbe = (com.hazelcast.simulator.probes.impl.HdrProbe)probeMap.get("get");
        final com.hazelcast.simulator.hz.cache.LongStringCacheTest.ThreadState threadState = (com.hazelcast.simulator.hz.cache.LongStringCacheTest.ThreadState)this.threadState;



        long iteration = 0;
        while (!testContext.isStopped()) {
            final long startNanos = System.nanoTime();

            testInstance.get( threadState );
            getProbe.recordValue(System.nanoTime() - startNanos);
            iteration++;
            iterations.lazySet(iteration);
        }
    }

}