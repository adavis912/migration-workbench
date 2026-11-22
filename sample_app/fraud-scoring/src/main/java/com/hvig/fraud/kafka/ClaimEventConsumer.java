package com.hvig.fraud.kafka;

import com.hvig.fraud.config.AppConfig;
import com.hvig.fraud.model.ClaimEvent;
import com.hvig.fraud.service.ScoringService;
import java.io.ByteArrayInputStream;
import java.io.ObjectInputStream;
import java.util.Collections;
import java.util.Properties;
import javax.servlet.ServletContextEvent;
import javax.servlet.ServletContextListener;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.ConsumerRecords;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.log4j.Logger;

/** Starts a consumer thread when the WAR deploys. Events arrive as Java-serialized ClaimEvent objects. */
public class ClaimEventConsumer implements ServletContextListener, Runnable {
    private static final Logger LOG = Logger.getLogger(ClaimEventConsumer.class);
    private volatile boolean running = true;

    public void contextInitialized(ServletContextEvent sce) {
        Thread t = new Thread(this, "claim-consumer");
        t.setDaemon(true);
        t.start();
    }

    public void contextDestroyed(ServletContextEvent sce) { running = false; }

    public void run() {
        Properties p = new Properties();
        p.put("bootstrap.servers", AppConfig.get("kafka.bootstrap", "clmfrau1-mq03.hvig.internal:9092"));
        p.put("group.id", "fraud-scoring");
        p.put("enable.auto.commit", "true");
        p.put("key.deserializer", "org.apache.kafka.common.serialization.StringDeserializer");
        p.put("value.deserializer", "org.apache.kafka.common.serialization.ByteArrayDeserializer");
        KafkaConsumer<String, byte[]> c = new KafkaConsumer<String, byte[]>(p);
        c.subscribe(Collections.singletonList(AppConfig.get("kafka.topic", "claims.intake")));
        while (running) {
            ConsumerRecords<String, byte[]> recs = c.poll(1000);
            for (ConsumerRecord<String, byte[]> r : recs) {
                try (ObjectInputStream in = new ObjectInputStream(new ByteArrayInputStream(r.value()))) {
                    ClaimEvent e = (ClaimEvent) in.readObject();
                    ScoringService.get().score(e);
                } catch (Exception ex) {
                    LOG.error("bad event at offset " + r.offset(), ex);
                }
            }
        }
        c.close();
    }
}
