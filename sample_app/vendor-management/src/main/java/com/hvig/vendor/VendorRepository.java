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
