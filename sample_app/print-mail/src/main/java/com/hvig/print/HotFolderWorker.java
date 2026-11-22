package com.hvig.print;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import redis.clients.jedis.Jedis;

/** Moves queued jobs into PrintFlow's hot folder as XML and records them in ATP. */
@Component
public class HotFolderWorker {
    private final JdbcTemplate jdbc;
    @Value("${print.redis.host}") private String redisHost;
    @Value("${print.redis.port}") private int redisPort;
    @Value("${print.hotfolder}") private String hotFolder;

    HotFolderWorker(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    @Scheduled(fixedDelay = 5000)
    public void drain() throws Exception {
        try (Jedis r = new Jedis(redisHost, redisPort)) {
            for (String item; (item = r.rpop("print:jobs")) != null; ) {
                String[] f = item.split("\\|", 4);
                Path out = Paths.get(hotFolder, f[0] + ".xml");
                Files.writeString(out, "<job template='" + f[1] + "'><to name='" + f[2] + "'>" + f[3] + "</to></job>");
                jdbc.update("INSERT INTO print_jobs (job_id, template, submitted_at) VALUES (?, ?, SYSTIMESTAMP)", f[0], f[1]);
            }
        }
    }
}
