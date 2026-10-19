import org.apache.zookeeper.CreateMode;
import org.apache.zookeeper.KeeperException;
import org.apache.zookeeper.Watcher;
import org.apache.zookeeper.ZKUtil;
import org.apache.zookeeper.ZooDefs.Ids;
import org.apache.zookeeper.ZooKeeper;

import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Date;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Random;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/**
 * Benchmark.java - Java port of benchmark.py: mixed read/write load generator
 * for a ZooKeeper ensemble. Same CLI, same znode-pool workload, same CSV schema
 * (so usl_fit.py / plot_chaos.py work unchanged).
 *
 * One "client" = one ZooKeeper session driven by one thread of a fixed pool
 * (closed loop, synchronous calls, like the kazoo version). Unlike the Python
 * version there is no GIL, so N clients really run on N cores.
 *
 * Usage:
 *   java -jar zk-benchmark.jar --hosts 127.0.0.1:2181,127.0.0.1:2182,127.0.0.1:2183 \
 *        --ensemble-size 3 --clients 20 --write-ratio 0.3 --duration 30 --warmup 5 \
 *        --pool-size 200 --output results_java.csv
 */
public class Benchmark {

    // ------------------------------------------------------------------ config
    static final class Config {
        String hosts;
        int ensembleSize = -1;
        int clients = -1;
        double writeRatio = 0.3;
        double duration = 30.0;
        double warmup = 5.0;
        int poolSize = 200;
        int payloadBytes = 128;
        double timeout = 10.0;
        String pathPrefix = "/bench";
        String runId = null;
        boolean keepPool = false;
        String output = "results.csv";
        String rawOutput = null;
        String label = "";
        int timeoutMs() { return (int) Math.round(timeout * 1000.0); }
    }

    static Config parseArgs(String[] a) {
        Config c = new Config();
        for (int i = 0; i < a.length; i++) {
            String k = a[i];
            switch (k) {
                case "--keep-pool": c.keepPool = true; break;
                case "-h": case "--help": usage(); System.exit(0); break;
                default:
                    if (i + 1 >= a.length) die("missing value for " + k);
                    String v = a[++i];
                    switch (k) {
                        case "--hosts": c.hosts = v; break;
                        case "--ensemble-size": c.ensembleSize = Integer.parseInt(v); break;
                        case "--clients": c.clients = Integer.parseInt(v); break;
                        case "--write-ratio": c.writeRatio = Double.parseDouble(v); break;
                        case "--duration": c.duration = Double.parseDouble(v); break;
                        case "--warmup": c.warmup = Double.parseDouble(v); break;
                        case "--pool-size": c.poolSize = Integer.parseInt(v); break;
                        case "--payload-bytes": c.payloadBytes = Integer.parseInt(v); break;
                        case "--timeout": c.timeout = Double.parseDouble(v); break;
                        case "--path-prefix": c.pathPrefix = v; break;
                        case "--run-id": c.runId = v; break;
                        case "--output": c.output = v; break;
                        case "--raw-output": c.rawOutput = v; break;
                        case "--label": c.label = v; break;
                        default: die("unknown argument " + k);
                    }
            }
        }
        if (c.hosts == null) die("--hosts is required");
        if (c.ensembleSize < 0) die("--ensemble-size is required");
        if (c.clients <= 0) die("--clients is required (>0)");
        if (c.runId == null) c.runId = UUID.randomUUID().toString().replace("-", "").substring(0, 12);
        return c;
    }

    static void usage() {
        System.out.println("java -jar zk-benchmark.jar --hosts h:p,h:p --ensemble-size N --clients C\n"
                + "  [--write-ratio 0.3] [--duration 30] [--warmup 5] [--pool-size 200]\n"
                + "  [--payload-bytes 128] [--timeout 10] [--path-prefix /bench] [--run-id ID]\n"
                + "  [--keep-pool] [--output results.csv] [--raw-output ops.jsonl] [--label TEXT]");
    }

    static void die(String msg) {
        System.err.println("error: " + msg);
        usage();
        System.exit(2);
    }

    // --------------------------------------------------------------- utilities
    static final class LongList {
        long[] a = new long[1 << 12];
        int n = 0;
        void add(long v) {
            if (n == a.length) a = Arrays.copyOf(a, n * 2);
            a[n++] = v;
        }
    }

    static long[] merge(List<LongList> lists) {
        int total = 0;
        for (LongList l : lists) total += l.n;
        long[] out = new long[total];
        int p = 0;
        for (LongList l : lists) {
            System.arraycopy(l.a, 0, out, p, l.n);
            p += l.n;
        }
        Arrays.sort(out);
        return out;
    }

    /** Same linear-interpolation percentile as benchmark.py; input sorted nanos, output ms. */
    static double percentileMs(long[] sorted, double p) {
        if (sorted.length == 0) return Double.NaN;
        double k = (sorted.length - 1) * p;
        int f = (int) k;
        int c = Math.min(f + 1, sorted.length - 1);
        double v = (f == c) ? sorted[f] : sorted[f] + (sorted[c] - sorted[f]) * (k - f);
        return v / 1e6;
    }

    static String num(double v) {
        if (Double.isNaN(v) || Double.isInfinite(v)) return "nan";
        double r = Math.round(v * 1000.0) / 1000.0;
        return new BigDecimal(Double.toString(r)).toPlainString();
    }

    static String csvEscape(String s) {
        if (s.indexOf(',') >= 0 || s.indexOf('"') >= 0 || s.indexOf('\n') >= 0) {
            return "\"" + s.replace("\"", "\"\"") + "\"";
        }
        return s;
    }

    static String jsonEscape(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    static String errorJson(Map<String, Integer> m) {
        StringBuilder sb = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, Integer> e : m.entrySet()) {
            if (!first) sb.append(", ");
            first = false;
            sb.append('"').append(jsonEscape(e.getKey())).append("\": ").append(e.getValue());
        }
        return sb.append('}').toString();
    }

    // ------------------------------------------------------------- ZooKeeper
    static ZooKeeper connect(String hosts, int timeoutMs) throws Exception {
        CountDownLatch up = new CountDownLatch(1);
        ZooKeeper zk = new ZooKeeper(hosts, timeoutMs, ev -> {
            if (ev.getState() == Watcher.Event.KeeperState.SyncConnected) up.countDown();
        });
        if (!up.await(timeoutMs, TimeUnit.MILLISECONDS)) {
            try { zk.close(); } catch (Exception ignore) { }
            throw new IOException("connect timeout after " + timeoutMs + " ms");
        }
        return zk;
    }

    static void ensurePath(ZooKeeper zk, String path) throws Exception {
        StringBuilder cur = new StringBuilder();
        for (String part : path.split("/")) {
            if (part.isEmpty()) continue;
            cur.append('/').append(part);
            try {
                zk.create(cur.toString(), new byte[0], Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
            } catch (KeeperException.NodeExistsException ignore) { }
        }
    }

    static String[] buildPool(Config c) {
        String[] p = new String[c.poolSize];
        for (int i = 0; i < c.poolSize; i++) {
            p[i] = String.format("%s/%s/node-%06d", c.pathPrefix, c.runId, i);
        }
        return p;
    }

    static void createPool(Config c, String[] pool) throws Exception {
        ZooKeeper zk = connect(c.hosts, c.timeoutMs());
        try {
            ensurePath(zk, c.pathPrefix + "/" + c.runId);
            byte[] payload = new byte[c.payloadBytes];
            new Random().nextBytes(payload);
            for (String path : pool) {
                zk.create(path, payload, Ids.OPEN_ACL_UNSAFE, CreateMode.PERSISTENT);
            }
        } finally {
            zk.close();
        }
    }

    static void cleanupPool(Config c) throws Exception {
        ZooKeeper zk = connect(c.hosts, c.timeoutMs());
        try {
            ZKUtil.deleteRecursive(zk, c.pathPrefix + "/" + c.runId);
        } catch (KeeperException.NoNodeException ignore) {
        } finally {
            zk.close();
        }
    }

    // ---------------------------------------------------------------- workers
    /** Timing window shared by all workers; set once, by the barrier action, after every session is up. */
    static volatile long warmupUntilNs;
    static volatile long stopAtNs;

    static final class Worker implements Callable<Void> {
        final int id;
        final Config cfg;
        final String[] pool;
        final CyclicBarrier barrier;
        final LongList readNs = new LongList();
        final LongList writeNs = new LongList();
        final Map<String, Integer> errors = new LinkedHashMap<>();
        final List<String> raw;   // null unless --raw-output

        Worker(int id, Config cfg, String[] pool, CyclicBarrier barrier) {
            this.id = id;
            this.cfg = cfg;
            this.pool = pool;
            this.barrier = barrier;
            this.raw = cfg.rawOutput != null ? new ArrayList<>() : null;
        }

        void err(String name) { errors.merge(name, 1, Integer::sum); }

        @Override
        public Void call() throws Exception {
            ZooKeeper zk = null;
            try {
                zk = connect(cfg.hosts, cfg.timeoutMs());
            } catch (Exception e) {
                err("connect");   // like benchmark.py: connection failures are counted as errors
            }
            // Always arrive at the barrier, even after a failed connect, so nobody hangs.
            try {
                barrier.await(cfg.timeoutMs() * 3L + 30_000L, TimeUnit.MILLISECONDS);
            } catch (Exception e) {
                err("barrier");
                closeQuietly(zk);
                return null;
            }
            if (zk == null) return null;

            Random rng = new Random(id * 7919L + 1);
            byte[] payload = new byte[cfg.payloadBytes];
            rng.nextBytes(payload);
            final long warmupUntil = warmupUntilNs;
            final long stopAt = stopAtNs;

            try {
                while (true) {
                    long now = System.nanoTime();
                    if (now >= stopAt) break;

                    String path = pool[rng.nextInt(pool.length)];
                    boolean isWrite = rng.nextDouble() < cfg.writeRatio;
                    String errName = null;
                    long t0 = System.nanoTime();
                    try {
                        if (isWrite) zk.setData(path, payload, -1);
                        else zk.getData(path, false, null);
                    } catch (KeeperException e) {
                        errName = e.getClass().getSimpleName();
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        break;
                    } catch (Exception e) {
                        errName = "unexpected:" + e.getClass().getSimpleName();
                    }
                    long t1 = System.nanoTime();

                    if (now >= warmupUntil) {
                        long lat = t1 - t0;
                        if (errName == null) {
                            (isWrite ? writeNs : readNs).add(lat);
                        } else {
                            err(errName);
                        }
                        if (raw != null) {
                            raw.add(String.format(Locale.ROOT,
                                    "{\"run_id\": \"%s\", \"ensemble_size\": %d, \"clients\": %d, \"op\": \"%s\", "
                                            + "\"latency_ms\": %s, \"ok\": %s, \"error\": \"%s\"}",
                                    cfg.runId, cfg.ensembleSize, cfg.clients, isWrite ? "write" : "read",
                                    Double.toString(lat / 1e6), errName == null,
                                    errName == null ? "" : jsonEscape(errName)));
                        }
                    }
                }
            } finally {
                closeQuietly(zk);
            }
            return null;
        }

        static void closeQuietly(ZooKeeper zk) {
            if (zk == null) return;
            try { zk.close(); } catch (Exception ignore) { }
        }
    }

    // ------------------------------------------------------------------- main
    static final String[] HEADER = {
        "timestamp", "run_id", "label", "ensemble_size", "clients", "write_ratio", "duration_s",
        "pool_size", "payload_bytes", "total_ops_ok", "total_ops_err", "throughput_ops_sec",
        "overall_p50_ms", "overall_p95_ms", "overall_p99_ms",
        "read_count", "read_p50_ms", "read_p95_ms", "read_p99_ms",
        "write_count", "write_p50_ms", "write_p95_ms", "write_p99_ms", "error_breakdown"
    };

    public static void main(String[] args) throws Exception {
        Config c = parseArgs(args);
        String[] pool = buildPool(c);

        System.out.printf("[setup] creating pool of %d znodes under %s/%s%n", c.poolSize, c.pathPrefix, c.runId);
        createPool(c, pool);

        System.out.printf("[run] launching %d clients, write_ratio=%s, warmup=%ss, duration=%ss%n",
                c.clients, c.writeRatio, num(c.warmup), num(c.duration));

        // The barrier action runs once, in the last thread to arrive, i.e. after every session is up.
        // Setting the window here means connection time never eats into warmup/measurement.
        CyclicBarrier barrier = new CyclicBarrier(c.clients, () -> {
            long t = System.nanoTime();
            warmupUntilNs = t + (long) (c.warmup * 1e9);
            stopAtNs = warmupUntilNs + (long) (c.duration * 1e9);
        });

        ExecutorService exec = Executors.newFixedThreadPool(c.clients);
        List<Worker> workers = new ArrayList<>();
        List<Future<Void>> futures = new ArrayList<>();
        for (int i = 0; i < c.clients; i++) {
            Worker w = new Worker(i, c, pool, barrier);
            workers.add(w);
            futures.add(exec.submit(w));
        }
        exec.shutdown();
        long waitMs = (long) ((c.warmup + c.duration) * 1000) + c.timeoutMs() * 4L + 30_000L;
        if (!exec.awaitTermination(waitMs, TimeUnit.MILLISECONDS)) {
            System.err.println("[warn] workers did not finish in time; forcing shutdown");
            exec.shutdownNow();
        }
        for (Future<Void> f : futures) {
            try { f.get(1, TimeUnit.SECONDS); } catch (Exception e) { System.err.println("[warn] worker failed: " + e); }
        }

        // ---- aggregate
        List<LongList> rl = new ArrayList<>(), wl = new ArrayList<>();
        Map<String, Integer> errors = new LinkedHashMap<>();
        for (Worker w : workers) {
            rl.add(w.readNs);
            wl.add(w.writeNs);
            for (Map.Entry<String, Integer> e : w.errors.entrySet()) errors.merge(e.getKey(), e.getValue(), Integer::sum);
        }
        long[] reads = merge(rl);
        long[] writes = merge(wl);
        long[] all = new long[reads.length + writes.length];
        System.arraycopy(reads, 0, all, 0, reads.length);
        System.arraycopy(writes, 0, all, reads.length, writes.length);
        Arrays.sort(all);

        long totalOk = all.length;
        long totalErr = 0;
        for (int v : errors.values()) totalErr += v;
        double throughput = c.duration > 0 ? totalOk / c.duration : 0.0;

        // ---- CSV row (same columns/order as benchmark.py)
        Map<String, String> row = new HashMap<>();
        row.put("timestamp", new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss").format(new Date()));
        row.put("run_id", c.runId);
        row.put("label", c.label);
        row.put("ensemble_size", Integer.toString(c.ensembleSize));
        row.put("clients", Integer.toString(c.clients));
        row.put("write_ratio", Double.toString(c.writeRatio));
        row.put("duration_s", Double.toString(c.duration));
        row.put("pool_size", Integer.toString(c.poolSize));
        row.put("payload_bytes", Integer.toString(c.payloadBytes));
        row.put("total_ops_ok", Long.toString(totalOk));
        row.put("total_ops_err", Long.toString(totalErr));
        row.put("throughput_ops_sec", num(throughput));
        row.put("overall_p50_ms", num(percentileMs(all, 0.50)));
        row.put("overall_p95_ms", num(percentileMs(all, 0.95)));
        row.put("overall_p99_ms", num(percentileMs(all, 0.99)));
        row.put("read_count", Integer.toString(reads.length));
        row.put("read_p50_ms", num(percentileMs(reads, 0.50)));
        row.put("read_p95_ms", num(percentileMs(reads, 0.95)));
        row.put("read_p99_ms", num(percentileMs(reads, 0.99)));
        row.put("write_count", Integer.toString(writes.length));
        row.put("write_p50_ms", num(percentileMs(writes, 0.50)));
        row.put("write_p95_ms", num(percentileMs(writes, 0.95)));
        row.put("write_p99_ms", num(percentileMs(writes, 0.99)));
        row.put("error_breakdown", errorJson(errors));

        Path out = Paths.get(c.output);
        boolean exists = Files.isRegularFile(out);
        StringBuilder sb = new StringBuilder();
        if (!exists) sb.append(String.join(",", HEADER)).append("\n");
        for (int i = 0; i < HEADER.length; i++) {
            if (i > 0) sb.append(',');
            sb.append(csvEscape(row.get(HEADER[i])));
        }
        sb.append("\n");
        Files.write(out, sb.toString().getBytes(StandardCharsets.UTF_8),
                StandardOpenOption.CREATE, StandardOpenOption.APPEND);

        if (c.rawOutput != null) {
            StringBuilder rs = new StringBuilder();
            for (Worker w : workers) for (String line : w.raw) rs.append(line).append('\n');
            Files.write(Paths.get(c.rawOutput), rs.toString().getBytes(StandardCharsets.UTF_8),
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        }

        System.out.printf("[done] throughput=%s ops/s  p50=%sms p95=%sms p99=%sms  errors=%d%n",
                row.get("throughput_ops_sec"), row.get("overall_p50_ms"), row.get("overall_p95_ms"),
                row.get("overall_p99_ms"), totalErr);

        if (!c.keepPool) {
            System.out.println("[cleanup] deleting znode pool");
            try { cleanupPool(c); } catch (Exception e) { System.err.println("[warn] cleanup failed: " + e); }
        }
        System.exit(0);   // ZooKeeper client threads are non-daemon in some failure paths
    }
}
