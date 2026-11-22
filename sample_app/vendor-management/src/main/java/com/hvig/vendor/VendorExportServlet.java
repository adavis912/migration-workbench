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
