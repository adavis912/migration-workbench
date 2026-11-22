package com.hvig.finrpt;

import java.io.*;
import java.math.BigDecimal;
import java.util.Map;
import javax.servlet.http.*;
import org.apache.log4j.Logger;
import org.apache.poi.hssf.usermodel.*;

/** POST /build?period=YYYY-MM writes an .xls report; GET /download?file=NAME streams it back. */
public class ReportServlet extends HttpServlet {
    private static final Logger LOG = Logger.getLogger(ReportServlet.class);
    static final String OUT = "/var/finrpt/reports";

    @Override
    protected void doPost(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        String period = req.getParameter("period");
        Map<String, BigDecimal> totals = new ExtractReader().totalsByCostCenter(period);
        HSSFWorkbook wb = new HSSFWorkbook();
        HSSFSheet sheet = wb.createSheet("Cost centers");
        int i = 0;
        for (Map.Entry<String, BigDecimal> e : totals.entrySet()) {
            HSSFRow row = sheet.createRow(i++);
            row.createCell(0).setCellValue(e.getKey());
            row.createCell(1).setCellValue(e.getValue().doubleValue());
        }
        FileOutputStream out = new FileOutputStream(OUT + "/cost-centers-" + period + ".xls");
        wb.write(out);
        out.close();
        LOG.info("built report for " + period);
    }

    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        File f = new File(OUT, req.getParameter("file"));   // legacy: name is not validated
        resp.setContentType("application/vnd.ms-excel");
        InputStream in = new FileInputStream(f);
        byte[] buf = new byte[8192];
        for (int n; (n = in.read(buf)) > 0; ) resp.getOutputStream().write(buf, 0, n);
        in.close();
    }
}
