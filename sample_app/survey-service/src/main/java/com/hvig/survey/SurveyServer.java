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
