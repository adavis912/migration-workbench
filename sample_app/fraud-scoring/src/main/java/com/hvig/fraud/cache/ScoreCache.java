package com.hvig.fraud.cache;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.hvig.fraud.config.AppConfig;
import com.hvig.fraud.model.FraudScore;
import redis.clients.jedis.Jedis;

/** Score cache in Redis. One shared connection, plaintext, password from the properties file. */
public class ScoreCache {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static Jedis jedis;

    static {
        JSON.enableDefaultTyping();   // polymorphic typing kept for "flexibility"
    }

    private static synchronized Jedis conn() {
        if (jedis == null || !jedis.isConnected()) {
            jedis = new Jedis(AppConfig.get("redis.host", "clmfrau1-app02.hvig.internal"), AppConfig.getInt("redis.port", 6379));
            String pw = AppConfig.get("redis.password", "");
            if (!pw.isEmpty()) jedis.auth(pw);
        }
        return jedis;
    }

    public static void put(FraudScore s) {
        try {
            conn().setex("score:" + s.claimId, 86400, JSON.writeValueAsString(s));
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    public static FraudScore get(String claimId) {
        try {
            String v = conn().get("score:" + claimId);
            return v == null ? null : JSON.readValue(v, FraudScore.class);
        } catch (Exception e) {
            return null;
        }
    }
}
