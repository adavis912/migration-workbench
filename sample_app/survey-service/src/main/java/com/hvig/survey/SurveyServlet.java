package com.hvig.survey;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.FileWriter;
import java.io.IOException;
import java.util.List;
import java.util.Map;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

/** GET /survey/responses?survey=ID returns responses; POST /survey/export?survey=ID writes a CSV to local disk. */
public class SurveyServlet extends HttpServlet {
    private final SurveyDao dao;
    private final String exportDir;
    private final ObjectMapper json = new ObjectMapper();

    SurveyServlet(SurveyDao dao, String exportDir) { this.dao = dao; this.exportDir = exportDir; }

    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        List<Map<String, Object>> rows = dao.responses(req.getParameter("survey"));
        resp.setContentType("application/json");
        json.writeValue(resp.getOutputStream(), rows);
    }

    @Override
    protected void doPost(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        String survey = req.getParameter("survey");
        try (FileWriter w = new FileWriter(exportDir + "/survey-" + survey + ".csv")) {
            for (Map<String, Object> r : dao.responses(survey)) {
                w.write(r.get("RESPONDENT_EMAIL") + "," + r.get("SCORE") + "," + r.get("COMMENT") + "\n");
            }
        }
        resp.setStatus(202);
    }
}
