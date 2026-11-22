package com.hvig.fraud.rules;

import com.hvig.fraud.config.AppConfig;
import com.hvig.fraud.model.ClaimEvent;
import java.util.LinkedHashMap;
import java.util.Map;
import javax.script.ScriptEngine;
import javax.script.ScriptEngineManager;

/**
 * Fraud rules. Each rule is a JavaScript boolean expression from app.properties, run with the Nashorn engine
 * that ships with JDK 8 (removed in JDK 15). Example: rule.lateReport=(reported - loss) / 86400000 > 30
 */
public class RuleEngine {
    private final ScriptEngine js = new ScriptEngineManager().getEngineByName("nashorn");
    private final Map<String, Integer> weights = new LinkedHashMap<String, Integer>();
    private final Map<String, String> rules = new LinkedHashMap<String, String>();

    public RuleEngine() {
        rules.put("highAmount", AppConfig.get("rule.highAmount", "amount > 25000"));
        rules.put("lateReport", AppConfig.get("rule.lateReport", "(reported - loss) / 86400000 > 30"));
        rules.put("repeatClaimant", AppConfig.get("rule.repeatClaimant", "prior >= 3"));
        rules.put("hotZip", AppConfig.get("rule.hotZip", "['90210','10001','33101'].indexOf(zip) >= 0"));
        weights.put("highAmount", 30);
        weights.put("lateReport", 25);
        weights.put("repeatClaimant", 35);
        weights.put("hotZip", 10);
    }

    public Map<String, Integer> evaluate(ClaimEvent e) {
        Map<String, Integer> hits = new LinkedHashMap<String, Integer>();
        js.put("amount", e.amount == null ? 0 : e.amount.doubleValue());
        js.put("loss", e.lossDate == null ? 0 : e.lossDate.getTime());
        js.put("reported", e.reportedDate == null ? 0 : e.reportedDate.getTime());
        js.put("prior", e.priorClaims);
        js.put("zip", e.zipCode);
        for (Map.Entry<String, String> r : rules.entrySet()) {
            try {
                Object v = js.eval(r.getValue());
                if (Boolean.TRUE.equals(v)) hits.put(r.getKey(), weights.get(r.getKey()));
            } catch (Exception ex) {
                // rule errors are ignored so scoring never stops
            }
        }
        return hits;
    }
}
