//! analyzer-sql-family — Rust port của SQL-family analyzers (phase 08):
//!
//! * `mybatis` — framework overlay `tools/mybatis/` (prerequisite java/kotlin):
//!   detector → mapper-interface (tree-sitter-java) → annotation-mapper →
//!   mapper-XML (tree-sitter-xml) → SQL semantic (tree-sitter-sql) → resolver →
//!   `MyBatisFactWriter` (node/rel facts qua GraphStore).
//! * `database_schema` — schema overlay `tools/database_schema/` (dialect
//!   sql|plsql): regex DDL scan → `DatabaseSchemaWriter`.
//! * `sqlfile` — `tools/sql/sql_analyzer.py`: regex routine scan →
//!   `LanguageCodeWriter::write_all` (files with_imports).
//! * `plsql` — `tools/plsql/plsql_analyzer.py`: packages/triggers/jobs →
//!   `LanguageCodeWriter::write_all`.
//!
//! Qdrant/embedding/message-scan là plane Python — cờ nhận và bỏ qua
//! (key decision #3 của plan). Call rows mang `project_id` tường minh
//! (writer contract — mirror analyzer-java).

pub mod database_schema;
pub mod mybatis;
pub mod plsql;
pub mod pyutil;
pub mod sqlcommon;
pub mod sqlfile;
