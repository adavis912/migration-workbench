package com.hvig.survey;

import java.sql.*;
import java.util.*;

public class SurveyDao {
    private final Properties p;
    SurveyDao(Properties p) { this.p = p; }

    List<Map<String, Object>> responses(String surveyId) {
        List<Map<String, Object>> out = new ArrayList<>();
        // Legacy: query built by string concatenation.
        String sql = "SELECT TOP 5000 RESPONDENT_EMAIL, SCORE, COMMENT FROM dbo.RESPONSES WITH (NOLOCK) WHERE SURVEY_ID = '" + surveyId + "'";
        try (Connection c = DriverManager.getConnection(p.getProperty("db.url"), p.getProperty("db.user"), p.getProperty("db.password"));
             Statement s = c.createStatement(); ResultSet rs = s.executeQuery(sql)) {
            ResultSetMetaData md = rs.getMetaData();
            while (rs.next()) {
                Map<String, Object> row = new LinkedHashMap<>();
                for (int i = 1; i <= md.getColumnCount(); i++) row.put(md.getColumnName(i), rs.getObject(i));
                out.add(row);
            }
        } catch (SQLException e) {
            e.printStackTrace();
        }
        return out;
    }
}
