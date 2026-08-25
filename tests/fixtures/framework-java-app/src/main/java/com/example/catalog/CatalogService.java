package com.example.catalog;

import java.util.List;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class CatalogService {
    private final CatalogMapper mapper;

    public CatalogService(CatalogMapper mapper) {
        this.mapper = mapper;
    }

    @Transactional(readOnly = true)
    public List<CatalogItem> findVisible() {
        return mapper.findVisible("ACTIVE");
    }
}
