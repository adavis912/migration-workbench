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
