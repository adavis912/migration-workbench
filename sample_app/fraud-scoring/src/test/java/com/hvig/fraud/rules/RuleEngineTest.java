package com.hvig.fraud.rules;

import static org.junit.Assert.assertTrue;

import com.hvig.fraud.model.ClaimEvent;
import java.math.BigDecimal;
import org.junit.Test;

public class RuleEngineTest {
    @Test
    public void highAmountHits() {
        ClaimEvent e = new ClaimEvent();
        e.claimId = "C-1";
        e.amount = new BigDecimal("30000");
        assertTrue(new RuleEngine().evaluate(e).containsKey("highAmount"));
    }
}
