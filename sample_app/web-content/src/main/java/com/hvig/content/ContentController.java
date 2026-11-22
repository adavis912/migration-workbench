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
