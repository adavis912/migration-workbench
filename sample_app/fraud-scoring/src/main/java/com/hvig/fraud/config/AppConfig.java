package com.hvig.fraud.config;

import java.io.FileInputStream;
import java.io.IOException;
import java.util.Properties;

/** Reads /opt/fraud/app.properties once, at class load. Secrets live in this file on the VM. */
public final class AppConfig {
    private static final Properties P = new Properties();
    static {
        try (FileInputStream in = new FileInputStream("/opt/fraud/app.properties")) {
            P.load(in);
        } catch (IOException e) {
            System.err.println("app.properties not found, using defaults");
        }
    }
    private AppConfig() {}
    public static String get(String k, String d) { return P.getProperty(k, d); }
    public static int getInt(String k, int d) { return Integer.parseInt(P.getProperty(k, String.valueOf(d))); }
}
