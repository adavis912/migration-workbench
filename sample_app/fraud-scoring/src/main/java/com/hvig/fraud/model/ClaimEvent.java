package com.hvig.fraud.model;

import java.io.Serializable;
import java.math.BigDecimal;
import java.util.Date;

/** Claim event published by Claims Intake. Sent over Kafka with Java serialization. */
public class ClaimEvent implements Serializable {
    private static final long serialVersionUID = 1L;
    public String claimId;
    public String policyNumber;
    public String claimantName;
    public String lossType;      // AUTO, PROPERTY, LIABILITY
    public BigDecimal amount;
    public Date lossDate;
    public Date reportedDate;
    public int priorClaims;
    public String zipCode;
}
