"""Writes the small fictional Wave 1 sample apps (one per app) under sample_app/<slug>/.
Each matches the stack recorded for that app in the synthetic portfolio and carries a few deliberate
legacy issues for a code scan to find. All names, hosts and data are invented for the capstone."""
import os, textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
NOTE = "Fictional legacy code written for the capstone. Not a real system; contains deliberate issues for code-scan practice."


def jenkins(build, deploy):
    return textwrap.dedent(f"""\
    // Legacy CI: builds on a shared Jenkins agent, then a person copies the artifact to the VM by hand.
    pipeline {{
      agent {{ label 'linux-build' }}
      stages {{
        stage('Build') {{ steps {{ sh '{build}' }} }}
        stage('Archive') {{ steps {{ archiveArtifacts artifacts: '{deploy}', fingerprint: true }} }}
      }}
      // Deploy is manual: scp the artifact to the app VM and restart the service (see README).
    }}
    """)


def readme(app, name, stack, layout, issues):
    lines = [f"# {name} ({app})", "", NOTE, "", "## Current stack", ""] + [f"- {s}" for s in stack]
    lines += ["", "## Layout", ""] + [f"- `{p}`: {d}" for p, d in layout]
    lines += ["", "## Known legacy concerns (for the migration assessment)", ""] + [f"- {i}" for i in issues]
    return "\n".join(lines) + "\n"


def pom(group, artifact, packaging, java, deps, parent=None, plugins=""):
    dep_xml = "\n".join(
        f"    <dependency><groupId>{g}</groupId><artifactId>{a}</artifactId>{f'<version>{v}</version>' if v else ''}"
        f"{'<scope>' + s + '</scope>' if s else ''}</dependency>" for g, a, v, s in deps)
    par = (f"  <parent><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId>"
           f"<version>{parent}</version></parent>\n") if parent else ""
    return textwrap.dedent(f"""\
    <?xml version="1.0" encoding="UTF-8"?>
    <project xmlns="http://maven.apache.org/POM/4.0.0">
      <modelVersion>4.0.0</modelVersion>
    {par}  <groupId>{group}</groupId>
      <artifactId>{artifact}</artifactId>
      <version>1.0.0-SNAPSHOT</version>
      <packaging>{packaging}</packaging>
      <properties>
        <maven.compiler.source>{java}</maven.compiler.source>
        <maven.compiler.target>{java}</maven.compiler.target>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
      </properties>
      <dependencies>
    {dep_xml}
      </dependencies>
    {plugins}</project>
    """)


APPS = {}

# ---------------------------------------------------------------- CS-SURV2 Survey Service: Jetty, JDK 17, httpd, SQL Server
APPS["survey-service"] = {
    "README.md": readme("CS-SURV2", "Survey Service", [
        "Embedded Jetty 9.4 on OpenJDK 17, fronted by Apache httpd", "SQL Server 2019 on a VM (survey responses, ~6 TB)",
        "Jenkins build, manual deploy"], [
        ("src/main/java/com/hvig/survey", "Jetty server, servlet and DAO"), ("config/survey.properties.example", "runtime settings"),
        ("deploy/httpd/survey.conf", "reverse proxy")], [
        "Jetty 9.4 is end of community support", "SQL built by string concatenation in SurveyDao",
        "JDBC connection has encrypt=false", "Large SQL Server database: plan the data move separately (DMS or native backup)",
        "Response exports written to local disk under /data/exports"]),
    "Jenkinsfile": jenkins("mvn -B -q package", "target/*.jar"),
    "pom.xml": pom("com.hvig", "survey-service", "jar", "17", [
        ("org.eclipse.jetty", "jetty-server", "9.4.31.v20200723", ""), ("org.eclipse.jetty", "jetty-servlet", "9.4.31.v20200723", ""),
        ("com.microsoft.sqlserver", "mssql-jdbc", "8.4.1.jre11", ""), ("com.fasterxml.jackson.core", "jackson-databind", "2.10.0", ""),
        ("junit", "junit", "4.12", "test")]),
    "config/survey.properties.example": textwrap.dedent("""\
        http.port=8085
        db.url=jdbc:sqlserver://cssurv2-db.hvig.internal:1433;databaseName=surveys;encrypt=false
        db.user=survey_app
        db.password=CHANGE_ME
        export.dir=/data/exports
        """),
    "deploy/httpd/survey.conf": textwrap.dedent("""\
        <VirtualHost *:80>
          ServerName surveys.hvig.internal
          ProxyPass        /survey http://127.0.0.1:8085/survey
          ProxyPassReverse /survey http://127.0.0.1:8085/survey
        </VirtualHost>
        """),
    "src/main/java/com/hvig/survey/SurveyServer.java": textwrap.dedent("""\
        package com.hvig.survey;

        import java.io.FileInputStream;
        import java.util.Properties;
        import org.eclipse.jetty.server.Server;
        import org.eclipse.jetty.servlet.ServletContextHandler;
        import org.eclipse.jetty.servlet.ServletHolder;

        public class SurveyServer {
            public static void main(String[] args) throws Exception {
                Properties p = new Properties();
                p.load(new FileInputStream("/opt/survey/survey.properties"));
                Server server = new Server(Integer.parseInt(p.getProperty("http.port", "8085")));
                ServletContextHandler ctx = new ServletContextHandler();
                ctx.setContextPath("/survey");
                ctx.addServlet(new ServletHolder(new SurveyServlet(new SurveyDao(p), p.getProperty("export.dir"))), "/*");
                server.setHandler(ctx);
                server.start();
                server.join();
            }
        }
        """),
    "src/main/java/com/hvig/survey/SurveyServlet.java": textwrap.dedent("""\
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
                        w.write(r.get("RESPONDENT_EMAIL") + "," + r.get("SCORE") + "," + r.get("COMMENT") + "\\n");
                    }
                }
                resp.setStatus(202);
            }
        }
        """),
    "src/main/java/com/hvig/survey/SurveyDao.java": textwrap.dedent("""\
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
        """),
}

# ---------------------------------------------------------------- FIN-RPT-LEGACY Finance Reporting: Tomcat 7, JDK 8, no DB, retiring
APPS["finance-reporting"] = {
    "README.md": readme("FIN-RPT-LEGACY", "Finance Reporting (Legacy, retiring)", [
        "WAR on Tomcat 7, Oracle JDK 1.8, one VM", "No database: reads nightly CSV extracts from an NFS share",
        "Nightly cron job builds Excel reports", "Jenkins build, manual deploy", "Scheduled for retirement once the new reporting app is live"], [
        ("src/main/java/com/hvig/finrpt", "report servlet and extract reader"), ("cron/nightly-report.sh", "cron job on the VM"),
        ("src/main/webapp/WEB-INF/web.xml", "servlet mapping")], [
        "Tomcat 7 and Oracle JDK 8 are end of life", "Apache POI 3.9 has known vulnerabilities",
        "Report download takes a file name from the request (path traversal)", "Depends on an NFS mount (/mnt/finshare)",
        "Retirement candidate: confirm with the business before migrating"]),
    "Jenkinsfile": jenkins("mvn -B -q package", "target/*.war"),
    "pom.xml": pom("com.hvig", "finance-reporting", "war", "1.8", [
        ("javax.servlet", "javax.servlet-api", "3.0.1", "provided"), ("org.apache.poi", "poi", "3.9", ""),
        ("org.apache.commons", "commons-csv", "1.4", ""), ("log4j", "log4j", "1.2.17", "")]),
    "cron/nightly-report.sh": textwrap.dedent("""\
        #!/bin/sh
        # crontab on finrpt-app01:  15 2 * * * /opt/finrpt/nightly-report.sh
        curl -s -X POST http://localhost:8080/finrpt/build?period=$(date +%Y-%m) >> /var/log/finrpt/nightly.log 2>&1
        """),
    "src/main/webapp/WEB-INF/web.xml": textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <web-app xmlns="http://java.sun.com/xml/ns/javaee" version="3.0">
          <servlet><servlet-name>report</servlet-name><servlet-class>com.hvig.finrpt.ReportServlet</servlet-class></servlet>
          <servlet-mapping><servlet-name>report</servlet-name><url-pattern>/*</url-pattern></servlet-mapping>
        </web-app>
        """),
    "src/main/java/com/hvig/finrpt/ExtractReader.java": textwrap.dedent("""\
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
        """),
    "src/main/java/com/hvig/finrpt/ReportServlet.java": textwrap.dedent("""\
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
        """),
}

# ---------------------------------------------------------------- HR-RECR1 Recruiting Tracker: Tomcat 9, JDK 8, Oracle 19c (DB System)
APPS["recruiting-tracker"] = {
    "README.md": readme("HR-RECR1", "Recruiting Tracker", [
        "WAR on Tomcat 9, Oracle JDK 1.8", "Oracle 19c on an OCI DB System (VM), ~150 GB", "JNDI datasource in Tomcat context.xml",
        "Jenkins build, manual deploy"], [
        ("src/main/java/com/hvig/recruit", "servlet and DAO"), ("db/recruit_pkg.sql", "PL/SQL package the app calls"),
        ("src/main/webapp/META-INF/context.xml", "datasource")], [
        "Business logic in a PL/SQL package (Oracle-specific; affects a move to PostgreSQL)",
        "Candidate national ID stored in clear text", "Oracle JDK 8 licensing and end of support",
        "Datasource password kept in context.xml"]),
    "Jenkinsfile": jenkins("mvn -B -q package", "target/*.war"),
    "pom.xml": pom("com.hvig", "recruiting-tracker", "war", "1.8", [
        ("javax.servlet", "javax.servlet-api", "3.1.0", "provided"), ("com.oracle.database.jdbc", "ojdbc8", "19.3.0.0", ""),
        ("org.springframework", "spring-jdbc", "4.3.25.RELEASE", ""), ("com.google.code.gson", "gson", "2.8.0", "")]),
    "src/main/webapp/META-INF/context.xml": textwrap.dedent("""\
        <Context>
          <Resource name="jdbc/recruit" auth="Container" type="javax.sql.DataSource"
                    driverClassName="oracle.jdbc.OracleDriver"
                    url="jdbc:oracle:thin:@hrrecr1-db.hvig.internal:1521/RECRUIT"
                    username="recruit_app" password="CHANGE_ME" maxTotal="20"/>
        </Context>
        """),
    "db/recruit_pkg.sql": textwrap.dedent("""\
        CREATE OR REPLACE PACKAGE recruit_pkg AS
          PROCEDURE advance_stage(p_candidate_id IN NUMBER, p_stage OUT VARCHAR2);
        END recruit_pkg;
        /
        CREATE OR REPLACE PACKAGE BODY recruit_pkg AS
          PROCEDURE advance_stage(p_candidate_id IN NUMBER, p_stage OUT VARCHAR2) IS
          BEGIN
            UPDATE candidates
               SET stage = DECODE(stage, 'APPLIED', 'SCREEN', 'SCREEN', 'INTERVIEW', 'INTERVIEW', 'OFFER', stage),
                   updated_at = SYSDATE
             WHERE candidate_id = p_candidate_id
            RETURNING stage INTO p_stage;
            COMMIT;
          END advance_stage;
        END recruit_pkg;
        /
        """),
    "src/main/java/com/hvig/recruit/CandidateDao.java": textwrap.dedent("""\
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
        """),
    "src/main/java/com/hvig/recruit/CandidateServlet.java": textwrap.dedent("""\
        package com.hvig.recruit;

        import com.google.gson.Gson;
        import java.io.IOException;
        import javax.servlet.ServletException;
        import javax.servlet.annotation.WebServlet;
        import javax.servlet.http.*;

        /** GET /candidates?req=ID lists candidates; POST /candidates?id=N advances one stage. No role check. */
        @WebServlet("/candidates")
        public class CandidateServlet extends HttpServlet {
            private CandidateDao dao;

            @Override
            public void init() throws ServletException {
                try { dao = new CandidateDao(); } catch (Exception e) { throw new ServletException(e); }
            }

            @Override
            protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
                resp.setContentType("application/json");
                resp.getWriter().write(new Gson().toJson(dao.openCandidates(req.getParameter("req"))));
            }

            @Override
            protected void doPost(HttpServletRequest req, HttpServletResponse resp) throws IOException {
                resp.getWriter().write(dao.advance(Long.parseLong(req.getParameter("id"))));
            }
        }
        """),
}

# ---------------------------------------------------------------- MKT-WEB3 Web Content Service: Spring Boot, JDK 17, Oracle on Exadata
APPS["web-content"] = {
    "README.md": readme("MKT-WEB3", "Web Content Service", [
        "Spring Boot 2.3 on OpenJDK 17", "Oracle 19c on Exadata (RAC), ~2.5 TB", "Uploaded images stored on the VM's local disk",
        "Jenkins build, manual deploy"], [
        ("src/main/java/com/hvig/content", "Boot app, REST controller, repository"), ("src/main/resources/application.yml", "settings")], [
        "Spring Boot 2.3 is end of support", "Oracle-specific SQL (CONNECT BY hierarchy, optimizer hints)",
        "Exadata-sized database: right-size before choosing an RDS class", "Uploads on local disk (/var/content): move to object storage",
        "All actuator endpoints exposed without auth"]),
    "Jenkinsfile": jenkins("./mvnw -B -q package", "target/*.jar"),
    "pom.xml": pom("com.hvig", "web-content", "jar", "17", [
        ("org.springframework.boot", "spring-boot-starter-web", "", ""), ("org.springframework.boot", "spring-boot-starter-jdbc", "", ""),
        ("org.springframework.boot", "spring-boot-starter-actuator", "", ""), ("com.oracle.database.jdbc", "ojdbc10", "19.8.0.0", "")],
        parent="2.3.12.RELEASE"),
    "src/main/resources/application.yml": textwrap.dedent("""\
        server:
          port: 8080
        spring:
          datasource:
            url: jdbc:oracle:thin:@mktweb3-exa.oraclecloud.internal:1521/CONTENT
            username: content_app
            password: CHANGE_ME
        content:
          upload-dir: /var/content
        management:
          endpoints:
            web:
              exposure:
                include: "*"
        """),
    "src/main/java/com/hvig/content/ContentApplication.java": textwrap.dedent("""\
        package com.hvig.content;

        import org.springframework.boot.SpringApplication;
        import org.springframework.boot.autoconfigure.SpringBootApplication;

        @SpringBootApplication
        public class ContentApplication {
            public static void main(String[] args) { SpringApplication.run(ContentApplication.class, args); }
        }
        """),
    "src/main/java/com/hvig/content/ContentRepository.java": textwrap.dedent("""\
        package com.hvig.content;

        import java.util.List;
        import java.util.Map;
        import org.springframework.jdbc.core.JdbcTemplate;
        import org.springframework.stereotype.Repository;

        @Repository
        public class ContentRepository {
            private final JdbcTemplate jdbc;
            ContentRepository(JdbcTemplate jdbc) { this.jdbc = jdbc; }

            /** Page tree for a site section, using Oracle hierarchical SQL. */
            List<Map<String, Object>> tree(long rootId) {
                return jdbc.queryForList(
                    "SELECT /*+ INDEX(p PAGES_PARENT_IX) */ page_id, parent_id, slug, title, LEVEL AS depth "
                  + "FROM pages p START WITH page_id = ? CONNECT BY PRIOR page_id = parent_id ORDER SIBLINGS BY slug", rootId);
            }

            void saveAsset(long pageId, String path) {
                jdbc.update("INSERT INTO assets (asset_id, page_id, path, created_at) VALUES (assets_seq.NEXTVAL, ?, ?, SYSTIMESTAMP)", pageId, path);
            }
        }
        """),
    "src/main/java/com/hvig/content/ContentController.java": textwrap.dedent("""\
        package com.hvig.content;

        import java.io.File;
        import java.io.IOException;
        import java.util.List;
        import java.util.Map;
        import org.springframework.beans.factory.annotation.Value;
        import org.springframework.web.bind.annotation.*;
        import org.springframework.web.multipart.MultipartFile;

        @RestController
        @RequestMapping("/api/content")
        public class ContentController {
            private final ContentRepository repo;
            @Value("${content.upload-dir}") private String uploadDir;

            ContentController(ContentRepository repo) { this.repo = repo; }

            @GetMapping("/tree/{rootId}")
            public List<Map<String, Object>> tree(@PathVariable long rootId) { return repo.tree(rootId); }

            @PostMapping("/{pageId}/assets")
            public String upload(@PathVariable long pageId, @RequestParam MultipartFile file) throws IOException {
                File dest = new File(uploadDir, file.getOriginalFilename());
                file.transferTo(dest);
                repo.saveAsset(pageId, dest.getPath());
                return dest.getName();
            }
        }
        """),
}

# ---------------------------------------------------------------- OPS-ASSE4 Asset Inventory: COTS (TalentArc) + Node.js 18 sync service
APPS["asset-inventory"] = {
    "README.md": readme("OPS-ASSE4", "Asset Inventory (customizations)", [
        "Vendor product TalentArc (COTS) holds the asset records; this repo is our sync service and config only",
        "Node.js 18 service on a VM, run by systemd", "Pulls assets from the vendor API every hour and drops a CSV on an SFTP server for Finance",
        "Jenkins build, manual deploy"], [
        ("src/talentarc-client.js", "vendor API client"), ("src/sync.js", "hourly sync job"), ("config/default.json", "settings")], [
        "Node.js 18 reached end of life in April 2025", "axios 0.21 has known vulnerabilities",
        "TLS certificate checks turned off for the vendor API", "Schedule runs inside the process (node-cron): consider a managed scheduler",
        "COTS product: confirm the vendor's supported AWS deployment before planning a rehost"]),
    "Jenkinsfile": jenkins("npm ci && npm test", "**/*.js,package*.json"),
    "package.json": textwrap.dedent("""\
        {
          "name": "asset-inventory-sync",
          "version": "1.4.2",
          "private": true,
          "engines": { "node": "18.x" },
          "scripts": { "start": "node src/sync.js", "test": "node -e \\"require('./src/talentarc-client')\\"" },
          "dependencies": {
            "axios": "0.21.1",
            "node-cron": "3.0.0",
            "ssh2-sftp-client": "7.2.1"
          }
        }
        """),
    "config/default.json": textwrap.dedent("""\
        {
          "talentarc": { "baseUrl": "https://talentarc.hvig.internal/api/v2", "apiUser": "sync", "apiKey": "CHANGE_ME" },
          "sftp": { "host": "sftp.hvig.internal", "user": "assets", "password": "CHANGE_ME", "dir": "/inbound/assets" },
          "schedule": "0 * * * *"
        }
        """),
    "src/talentarc-client.js": textwrap.dedent("""\
        const axios = require('axios');
        const https = require('https');
        const cfg = require('../config/default.json').talentarc;

        // Legacy: vendor appliance uses a self-signed cert, so verification was switched off.
        const http = axios.create({
          baseURL: cfg.baseUrl,
          httpsAgent: new https.Agent({ rejectUnauthorized: false }),
          auth: { username: cfg.apiUser, password: cfg.apiKey },
        });

        async function allAssets() {
          const out = [];
          for (let page = 1; ; page++) {
            const { data } = await http.get('/assets', { params: { page, size: 500 } });
            out.push(...data.items);
            if (!data.next) return out;
          }
        }

        module.exports = { allAssets };
        """),
    "src/sync.js": textwrap.dedent("""\
        const cron = require('node-cron');
        const SftpClient = require('ssh2-sftp-client');
        const cfg = require('../config/default.json');
        const { allAssets } = require('./talentarc-client');

        async function run() {
          const assets = await allAssets();
          const csv = ['asset_tag,serial,location,cost_center,owner_email']
            .concat(assets.map(a => [a.tag, a.serial, a.location, a.costCenter, a.ownerEmail].join(',')))
            .join('\\n');
          const sftp = new SftpClient();
          await sftp.connect({ host: cfg.sftp.host, username: cfg.sftp.user, password: cfg.sftp.password });
          await sftp.put(Buffer.from(csv), `${cfg.sftp.dir}/assets-${Date.now()}.csv`);
          await sftp.end();
          console.log(`synced ${assets.length} assets`);
        }

        cron.schedule(cfg.schedule, () => run().catch(err => console.error('sync failed', err)));
        console.log('asset sync scheduled:', cfg.schedule);
        """),
}

# ---------------------------------------------------------------- OPS-PRIN1 Print and Mail: COTS (PrintFlow) + Spring Boot adapter, Redis, Oracle ATP, httpd
APPS["print-mail"] = {
    "README.md": readme("OPS-PRIN1", "Print and Mail (adapter)", [
        "Vendor product PrintFlow (COTS) does composition and printing; this repo is our Spring Boot adapter",
        "Spring Boot 2.7 on OpenJDK 17, fronted by Apache httpd", "Redis list as the job queue", "Oracle Autonomous (ATP) for job history",
        "Jenkins build, manual deploy"], [
        ("src/main/java/com/hvig/print", "REST endpoint, queue worker, PrintFlow hot-folder writer"),
        ("src/main/resources/application.properties", "settings"), ("deploy/httpd/print.conf", "reverse proxy")], [
        "Spring Boot 2.7 open-source support has ended", "Hands jobs to PrintFlow through a local hot folder (/printflow/hotfolder)",
        "Redis without TLS or auth", "Job payloads include customer names and mailing addresses",
        "COTS product: confirm vendor support for the target platform"]),
    "Jenkinsfile": jenkins("./mvnw -B -q package", "target/*.jar"),
    "pom.xml": pom("com.hvig", "print-mail-adapter", "jar", "17", [
        ("org.springframework.boot", "spring-boot-starter-web", "", ""), ("org.springframework.boot", "spring-boot-starter-jdbc", "", ""),
        ("redis.clients", "jedis", "3.6.0", ""), ("com.oracle.database.jdbc", "ojdbc11", "21.1.0.0", "")], parent="2.7.18"),
    "src/main/resources/application.properties": textwrap.dedent("""\
        server.port=8090
        spring.datasource.url=jdbc:oracle:thin:@opsprin1_tp?TNS_ADMIN=/opt/print/wallet
        spring.datasource.username=print_app
        spring.datasource.password=CHANGE_ME
        print.redis.host=opsprin1-redis.hvig.internal
        print.redis.port=6379
        print.hotfolder=/printflow/hotfolder
        """),
    "deploy/httpd/print.conf": textwrap.dedent("""\
        <VirtualHost *:80>
          ServerName print.hvig.internal
          ProxyPass        /print http://127.0.0.1:8090/print
          ProxyPassReverse /print http://127.0.0.1:8090/print
        </VirtualHost>
        """),
    "src/main/java/com/hvig/print/PrintApplication.java": textwrap.dedent("""\
        package com.hvig.print;

        import org.springframework.boot.SpringApplication;
        import org.springframework.boot.autoconfigure.SpringBootApplication;
        import org.springframework.scheduling.annotation.EnableScheduling;

        @SpringBootApplication
        @EnableScheduling
        public class PrintApplication {
            public static void main(String[] args) { SpringApplication.run(PrintApplication.class, args); }
        }
        """),
    "src/main/java/com/hvig/print/PrintJobController.java": textwrap.dedent("""\
        package com.hvig.print;

        import java.util.Map;
        import java.util.UUID;
        import org.springframework.beans.factory.annotation.Value;
        import org.springframework.web.bind.annotation.*;
        import redis.clients.jedis.Jedis;

        /** POST /print/jobs queues a letter for PrintFlow. */
        @RestController
        @RequestMapping("/print")
        public class PrintJobController {
            @Value("${print.redis.host}") private String redisHost;
            @Value("${print.redis.port}") private int redisPort;

            @PostMapping("/jobs")
            public Map<String, String> submit(@RequestBody Map<String, String> job) {
                String id = UUID.randomUUID().toString();
                try (Jedis r = new Jedis(redisHost, redisPort)) {
                    r.lpush("print:jobs", id + "|" + job.get("template") + "|" + job.get("name") + "|" + job.get("address"));
                }
                return Map.of("id", id, "state", "queued");
            }
        }
        """),
    "src/main/java/com/hvig/print/HotFolderWorker.java": textwrap.dedent("""\
        package com.hvig.print;

        import java.nio.file.Files;
        import java.nio.file.Path;
        import java.nio.file.Paths;
        import org.springframework.beans.factory.annotation.Value;
        import org.springframework.jdbc.core.JdbcTemplate;
        import org.springframework.scheduling.annotation.Scheduled;
        import org.springframework.stereotype.Component;
        import redis.clients.jedis.Jedis;

        /** Moves queued jobs into PrintFlow's hot folder as XML and records them in ATP. */
        @Component
        public class HotFolderWorker {
            private final JdbcTemplate jdbc;
            @Value("${print.redis.host}") private String redisHost;
            @Value("${print.redis.port}") private int redisPort;
            @Value("${print.hotfolder}") private String hotFolder;

            HotFolderWorker(JdbcTemplate jdbc) { this.jdbc = jdbc; }

            @Scheduled(fixedDelay = 5000)
            public void drain() throws Exception {
                try (Jedis r = new Jedis(redisHost, redisPort)) {
                    for (String item; (item = r.rpop("print:jobs")) != null; ) {
                        String[] f = item.split("\\\\|", 4);
                        Path out = Paths.get(hotFolder, f[0] + ".xml");
                        Files.writeString(out, "<job template='" + f[1] + "'><to name='" + f[2] + "'>" + f[3] + "</to></job>");
                        jdbc.update("INSERT INTO print_jobs (job_id, template, submitted_at) VALUES (?, ?, SYSTIMESTAMP)", f[0], f[1]);
                    }
                }
            }
        }
        """),
}

# ---------------------------------------------------------------- OPS-VEND2 Vendor Management: COTS (MarkPoint) plugin, Tomcat 9, JDK 8, MongoDB 6, httpd
APPS["vendor-management"] = {
    "README.md": readme("OPS-VEND2", "Vendor Management (plugin)", [
        "Vendor product MarkPoint (COTS) on Tomcat 9, Oracle JDK 1.8, fronted by Apache httpd",
        "This repo is our export plugin WAR deployed next to MarkPoint", "MongoDB 6.0 on a VM, ~400 GB",
        "Data classification: Confidential (vendor tax IDs and bank details)", "Jenkins build, manual deploy"], [
        ("src/main/java/com/hvig/vendor", "export servlet and Mongo repository"), ("config/markpoint-plugin.xml", "plugin registration"),
        ("deploy/httpd/vendor.conf", "reverse proxy")], [
        "Server-side JavaScript ($where) built from a request parameter", "Export returns bank details with no role check",
        "MongoDB connection without TLS", "Mongo driver 3.12 is old: check compatibility with Amazon DocumentDB or MongoDB Atlas",
        "COTS product: confirm vendor support for the target platform"]),
    "Jenkinsfile": jenkins("mvn -B -q package", "target/*.war"),
    "pom.xml": pom("com.hvig", "vendor-export-plugin", "war", "1.8", [
        ("javax.servlet", "javax.servlet-api", "3.1.0", "provided"), ("org.mongodb", "mongo-java-driver", "3.12.7", ""),
        ("com.fasterxml.jackson.core", "jackson-databind", "2.9.10", "")]),
    "config/markpoint-plugin.xml": textwrap.dedent("""\
        <plugin id="hvig-vendor-export" version="2.1">
          <context>/vendor-export</context>
          <setting name="mongo.uri">mongodb://vendor_app:CHANGE_ME@opsvend2-db.hvig.internal:27017/vendors?ssl=false</setting>
        </plugin>
        """),
    "deploy/httpd/vendor.conf": textwrap.dedent("""\
        <VirtualHost *:80>
          ServerName vendors.hvig.internal
          ProxyPass        /vendor-export ajp://127.0.0.1:8009/vendor-export
          ProxyPassReverse /vendor-export ajp://127.0.0.1:8009/vendor-export
        </VirtualHost>
        """),
    "src/main/java/com/hvig/vendor/VendorRepository.java": textwrap.dedent("""\
        package com.hvig.vendor;

        import com.mongodb.MongoClient;
        import com.mongodb.MongoClientURI;
        import com.mongodb.client.MongoCollection;
        import java.util.ArrayList;
        import java.util.List;
        import org.bson.Document;

        public class VendorRepository {
            private final MongoCollection<Document> vendors;

            VendorRepository(String uri) {
                vendors = new MongoClient(new MongoClientURI(uri)).getDatabase("vendors").getCollection("vendors");
            }

            List<Document> byCategory(String category) {
                // Legacy: server-side JavaScript filter built from input.
                Document q = new Document("$where", "this.category == '" + category + "' && this.active");
                return vendors.find(q).into(new ArrayList<Document>());
            }
        }
        """),
    "src/main/java/com/hvig/vendor/VendorExportServlet.java": textwrap.dedent("""\
        package com.hvig.vendor;

        import com.fasterxml.jackson.databind.ObjectMapper;
        import java.io.IOException;
        import javax.servlet.ServletException;
        import javax.servlet.annotation.WebServlet;
        import javax.servlet.http.*;

        /** GET /vendor-export/vendors?category=X returns vendors, including tax ID and bank account. */
        @WebServlet("/vendors")
        public class VendorExportServlet extends HttpServlet {
            private VendorRepository repo;

            @Override
            public void init() throws ServletException {
                repo = new VendorRepository(System.getProperty("mongo.uri",
                        "mongodb://opsvend2-db.hvig.internal:27017/vendors"));
            }

            @Override
            protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
                resp.setContentType("application/json");
                new ObjectMapper().writeValue(resp.getOutputStream(), repo.byCategory(req.getParameter("category")));
            }
        }
        """),
}


if __name__ == "__main__":
    for slug, files in APPS.items():
        for rel, body in files.items():
            p = os.path.join(HERE, slug, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(body)
        print(slug, len(files), "files")
