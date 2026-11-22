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
