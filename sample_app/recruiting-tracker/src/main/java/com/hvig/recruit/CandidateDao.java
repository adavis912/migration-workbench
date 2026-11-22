package com.hvig.recruit;

import java.sql.Types;
import java.util.List;
import java.util.Map;
import javax.naming.InitialContext;
import javax.sql.DataSource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.SqlOutParameter;
import org.springframework.jdbc.core.SqlParameter;
import org.springframework.jdbc.core.simple.SimpleJdbcCall;

public class CandidateDao {
    private final JdbcTemplate jdbc;
    private final SimpleJdbcCall advance;

    CandidateDao() throws Exception {
        DataSource ds = (DataSource) new InitialContext().lookup("java:comp/env/jdbc/recruit");
        jdbc = new JdbcTemplate(ds);
        advance = new SimpleJdbcCall(ds).withCatalogName("RECRUIT_PKG").withProcedureName("ADVANCE_STAGE")
                .declareParameters(new SqlParameter("P_CANDIDATE_ID", Types.NUMERIC), new SqlOutParameter("P_STAGE", Types.VARCHAR));
    }

    List<Map<String, Object>> openCandidates(String requisition) {
        return jdbc.queryForList("SELECT candidate_id, full_name, email, national_id, stage FROM candidates "
                + "WHERE requisition_id = ? AND ROWNUM <= 500 ORDER BY updated_at DESC NULLS LAST", requisition);
    }

    String advance(long candidateId) {
        return (String) advance.execute(java.util.Collections.singletonMap("P_CANDIDATE_ID", candidateId)).get("P_STAGE");
    }
}
