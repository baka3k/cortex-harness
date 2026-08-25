package com.example.catalog;

import java.util.List;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface CatalogMapper {
    @Select("select id, name, status from catalog_item where status = #{status}")
    List<CatalogItem> findVisible(@Param("status") String status);
}
