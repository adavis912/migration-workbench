package com.hvig.print;

import java.util.Map;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.*;
import redis.clients.jedis.Jedis;

/** POST /print/jobs queues a letter for PrintFlow. */
@RestController
@RequestMapping("/print")
public class PrintJobController {
    @Value("${print.redis.host}") private String redisHost;
    @Value("${print.redis.port}") private int redisPort;

    @PostMapping("/jobs")
    public Map<String, String> submit(@RequestBody Map<String, String> job) {
        String id = UUID.randomUUID().toString();
        try (Jedis r = new Jedis(redisHost, redisPort)) {
            r.lpush("print:jobs", id + "|" + job.get("template") + "|" + job.get("name") + "|" + job.get("address"));
        }
        return Map.of("id", id, "state", "queued");
    }
}
