package com.hvig.finrpt;

import java.io.FileReader;
import java.io.IOException;
import java.math.BigDecimal;
import java.util.Map;
import java.util.TreeMap;
import org.apache.commons.csv.CSVFormat;
import org.apache.commons.csv.CSVRecord;

/** Reads the nightly GL extract dropped on the NFS share by the finance batch. */
public class ExtractReader {
    static final String SHARE = "/mnt/finshare/extracts";

    Map<String, BigDecimal> totalsByCostCenter(String period) throws IOException {
        Map<String, BigDecimal> totals = new TreeMap<String, BigDecimal>();
        FileReader in = new FileReader(SHARE + "/gl_" + period + ".csv");
        for (CSVRecord r : CSVFormat.DEFAULT.withFirstRecordAsHeader().parse(in)) {
            BigDecimal amt = new BigDecimal(r.get("amount"));
            String cc = r.get("cost_center");
            totals.put(cc, totals.containsKey(cc) ? totals.get(cc).add(amt) : amt);
        }
        in.close();
        return totals;
    }
}
