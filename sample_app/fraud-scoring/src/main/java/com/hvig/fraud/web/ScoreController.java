package com.hvig.fraud.web;

import com.hvig.fraud.cache.ScoreCache;
import com.hvig.fraud.model.ClaimEvent;
import com.hvig.fraud.model.FraudScore;
import com.hvig.fraud.service.ScoringService;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

/** Internal API used by adjusters' desktop tool. No authentication: "it is on the internal network". */
@RestController
@RequestMapping("/api/scores")
public class ScoreController {

    @RequestMapping(value = "/{claimId}", method = RequestMethod.GET)
    public FraudScore get(@PathVariable("claimId") String claimId) {
        return ScoreCache.get(claimId);
    }

    @RequestMapping(value = "/rescore", method = RequestMethod.POST)
    public FraudScore rescore(@RequestBody ClaimEvent e) {
        return ScoringService.get().score(e);
    }
}
