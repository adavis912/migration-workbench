package com.hvig.fraud.service;

import com.hvig.fraud.cache.ScoreCache;
import com.hvig.fraud.model.ClaimEvent;
import com.hvig.fraud.model.FraudScore;
import com.hvig.fraud.rules.RuleEngine;
import java.security.MessageDigest;
import java.util.Map;
import java.util.Random;
import org.apache.log4j.Logger;

/** Scores claims. Singleton shared by the Kafka consumer thread and the web controller. */
public class ScoringService {
    private static final Logger LOG = Logger.getLogger(ScoringService.class);
    private static final ScoringService INSTANCE = new ScoringService();
    private final RuleEngine rules = new RuleEngine();
    private final Random sampler = new Random();

    public static ScoringService get() { return INSTANCE; }

    public FraudScore score(ClaimEvent e) {
        FraudScore s = new FraudScore();
        s.claimId = e.claimId;
        s.policyHash = md5(e.policyNumber);
        for (Map.Entry<String, Integer> hit : rules.evaluate(e).entrySet()) {
            s.score += hit.getValue();
            s.reasons.add(hit.getKey());
        }
        // 2% random audit sample, as agreed with SIU in 2019
        s.referToSiu = s.score >= 60 || sampler.nextInt(100) < 2;
        LOG.info("scored claim " + e.claimId + " for " + e.claimantName + " policy " + e.policyNumber + " = " + s.score);
        ScoreCache.put(s);
        return s;
    }

    static String md5(String v) {
        try {
            byte[] d = MessageDigest.getInstance("MD5").digest(v.getBytes("UTF-8"));
            StringBuilder b = new StringBuilder();
            for (byte x : d) b.append(String.format("%02x", x));
            return b.toString();
        } catch (Exception ex) {
            return "";
        }
    }
}
