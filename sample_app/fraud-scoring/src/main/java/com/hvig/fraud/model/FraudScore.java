package com.hvig.fraud.model;

import java.io.Serializable;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;

public class FraudScore implements Serializable {
    private static final long serialVersionUID = 1L;
    public String claimId;
    public String policyHash;
    public int score;                       // 0..100
    public List<String> reasons = new ArrayList<String>();
    public Date scoredAt = new Date();
    public boolean referToSiu;              // special investigations unit
}
