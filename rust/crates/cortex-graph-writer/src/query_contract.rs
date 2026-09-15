//! Port của `tools/graph/writer/query_contract.py` — safe Cypher construction
//! cho label-qualified relationship mutations. Các compiled query giữ nguyên
//! từng chữ so với Python (dual-write parity yêu cầu wire-identical).

use std::collections::BTreeMap;

use serde_json::Value;

use cortex_graph_core::schema_manifest::{code_graph_schema, validate_cypher_identifier};

use crate::json_row::Row;
use crate::project_scope::project_id_lookup_key;

/// `RelationshipGroup` — ordering theo (source_label, target_label,
/// relationship_type) như `@dataclass(frozen=True, order=True)`.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct RelationshipGroup {
    pub source_label: String,
    pub target_label: String,
    pub relationship_type: String,
}

impl RelationshipGroup {
    /// `__post_init__` — validate identifier + identity index trong manifest.
    pub fn new(
        source_label: &str,
        target_label: &str,
        relationship_type: &str,
    ) -> Result<Self, String> {
        validate_cypher_identifier(source_label, "source label")?;
        validate_cypher_identifier(target_label, "target label")?;
        validate_cypher_identifier(relationship_type, "relationship type")?;
        let schema = code_graph_schema();
        for (role, label) in [("source", source_label), ("target", target_label)] {
            if !schema.has_identity_index(label, "id") {
                return Err(format!(
                    "{role} label {label:?} has no required id index in schema {}@{}",
                    schema.name,
                    schema.fingerprint()
                ));
            }
        }
        Ok(Self {
            source_label: source_label.to_string(),
            target_label: target_label.to_string(),
            relationship_type: relationship_type.to_string(),
        })
    }

    /// Xây trực tiếp khi caller đã validate (create_* helpers của operations
    /// dùng RelationshipGroup để hợp lệ hoá rồi format query).
    pub fn new_unchecked(
        source_label: &str,
        target_label: &str,
        relationship_type: &str,
    ) -> Self {
        Self {
            source_label: source_label.to_string(),
            target_label: target_label.to_string(),
            relationship_type: relationship_type.to_string(),
        }
    }

    pub fn state_key(&self) -> String {
        format!(
            "relations:{}:{}:{}",
            self.source_label, self.relationship_type, self.target_label
        )
    }
}

fn row_str<'a>(row: &'a Row, key: &str) -> Option<&'a str> {
    row.get(key).and_then(Value::as_str).filter(|s| !s.is_empty())
}

/// `group_typed_relations` — group rows by endpoint labels + relationship
/// type; mỗi row cần source_label/target_label/rel_type/source_id/target_id
/// và project_id (từ row, properties, hoặc default).
pub fn group_typed_relations(
    relations: &[Row],
    default_project_id: Option<&str>,
) -> Result<BTreeMap<RelationshipGroup, Vec<Row>>, String> {
    let mut groups: BTreeMap<RelationshipGroup, Vec<Row>> = BTreeMap::new();
    for (position, relation) in relations.iter().enumerate() {
        let source_label = row_str(relation, "source_label")
            .filter(|s| !s.is_empty())
            .ok_or_else(|| {
                format!(
                    "typed relationship row requires source_label, target_label, and \
                     rel_type (row {position})"
                )
            })?;
        let target_label = row_str(relation, "target_label")
            .filter(|s| !s.is_empty())
            .ok_or_else(|| {
                format!(
                    "typed relationship row requires source_label, target_label, and \
                     rel_type (row {position})"
                )
            })?;
        let relationship_type = row_str(relation, "rel_type")
            .filter(|s| !s.is_empty())
            .ok_or_else(|| {
                format!(
                    "typed relationship row requires source_label, target_label, and \
                     rel_type (row {position})"
                )
            })?;
        if row_str(relation, "source_id").filter(|s| !s.is_empty()).is_none()
            || row_str(relation, "target_id").filter(|s| !s.is_empty()).is_none()
        {
            return Err(format!(
                "typed relationship row requires source_id and target_id (row {position})"
            ));
        }
        let mut row = relation.clone();
        let property_scope = row
            .get("properties")
            .and_then(Value::as_object)
            .and_then(|props| props.get("project_id"))
            .and_then(Value::as_str);
        let project_id = row_str(relation, "project_id")
            .map(str::to_string)
            .or_else(|| property_scope.map(str::to_string))
            .or_else(|| default_project_id.map(str::to_string));
        let normalized_scope = project_id_lookup_key(project_id.as_deref())
            .ok_or_else(|| {
                format!("typed relationship row requires project_id (row {position})")
            })?;
        let project_id = project_id.unwrap_or_default().trim().to_string();
        row.insert("project_id".to_string(), Value::String(project_id));
        row.insert(
            "project_id_normalized".to_string(),
            Value::String(normalized_scope),
        );
        // Cypher groups equal maps trong `WITH row, count(...)`. Giữ ordinal
        // input để endpoint audit đo node cardinality cho từng relation
        // submitted (không đếm nhầm duplicate relation rows thành duplicate
        // nodes) — khớp comment gốc.
        row.insert("_contract_row_position".to_string(), Value::from(position as i64));
        let group = RelationshipGroup::new_unchecked(
            source_label,
            target_label,
            relationship_type,
        );
        groups.entry(group).or_default().push(row);
    }
    Ok(groups)
}

/// `compile_relationship_upsert` — compiled query giữ nguyên chữ Python.
pub fn compile_relationship_upsert(group: &RelationshipGroup) -> String {
    format!(
        "UNWIND $rows AS row \
         MATCH (a:{source} {{id: row.source_id, project_id_normalized: row.project_id_normalized}}) \
         WITH row, a \
         MATCH (b:{target} {{id: row.target_id, project_id_normalized: row.project_id_normalized}}) \
         MERGE (a)-[r:{rel}]->(b) \
         SET r += coalesce(row.properties, {{}}), \
         r.project_id = row.project_id, \
         r.project_id_normalized = row.project_id_normalized \
         RETURN count(r) AS count",
        source = group.source_label,
        target = group.target_label,
        rel = group.relationship_type,
    )
}

/// Ladybug không hỗ trợ `SET r += …` trên relationship và không có literal
/// map rỗng — variant này bỏ dynamic properties (caller phải fail-closed
/// khi row mang properties khác rỗng).
pub fn compile_relationship_upsert_ladybug(group: &RelationshipGroup) -> String {
    format!(
        "UNWIND $rows AS row \
         MATCH (a:{source} {{id: row.source_id, project_id_normalized: row.project_id_normalized}}) \
         WITH row, a \
         MATCH (b:{target} {{id: row.target_id, project_id_normalized: row.project_id_normalized}}) \
         MERGE (a)-[r:{rel}]->(b) \
         SET r.project_id = row.project_id, \
         r.project_id_normalized = row.project_id_normalized \
         RETURN count(r) AS count",
        source = group.source_label,
        target = group.target_label,
        rel = group.relationship_type,
    )
}

/// `compile_relationship_endpoint_audit` — read-only query nhận diện
/// non-unique endpoints (mutation fail-closed khi count lệch submitted).
pub fn compile_relationship_endpoint_audit(group: &RelationshipGroup) -> String {
    format!(
        "UNWIND $rows AS row \
         OPTIONAL MATCH (a:{source} {{id: row.source_id, project_id_normalized: row.project_id_normalized}}) \
         WITH row, count(a) AS source_matches \
         OPTIONAL MATCH (b:{target} {{id: row.target_id, project_id_normalized: row.project_id_normalized}}) \
         WITH row, source_matches, count(b) AS target_matches \
         WHERE source_matches <> 1 OR target_matches <> 1 \
         RETURN row.source_id AS source_id, row.target_id AS target_id, \
         row.project_id_normalized AS project_id_normalized, \
         source_matches, target_matches LIMIT 20",
        source = group.source_label,
        target = group.target_label,
    )
}

/// `EvidenceEdgeGroup` — staging-plane evidence edge shape. Empty
/// `edge_property` = pattern merge theo endpoint identity.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct EvidenceEdgeGroup {
    pub source_label: String,
    pub source_property: String,
    pub target_label: String,
    pub target_property: String,
    pub relationship_type: String,
    pub edge_property: String,
}

const EVIDENCE_EDGE_ROW_FIELDS: [&str; 5] = [
    "source_label",
    "source_property",
    "target_label",
    "target_property",
    "rel_type",
];

impl EvidenceEdgeGroup {
    pub fn new(
        source_label: &str,
        source_property: &str,
        target_label: &str,
        target_property: &str,
        relationship_type: &str,
        edge_property: &str,
    ) -> Result<Self, String> {
        for (kind, identifier) in [
            ("source label", source_label),
            ("target label", target_label),
            ("relationship type", relationship_type),
            ("source property", source_property),
            ("target property", target_property),
        ] {
            validate_cypher_identifier(identifier, kind)?;
        }
        if !edge_property.is_empty() {
            validate_cypher_identifier(edge_property, "edge property")?;
        }
        let schema = code_graph_schema();
        for (role, label, prop) in [
            ("source", source_label, source_property),
            ("target", target_label, target_property),
        ] {
            if !schema.has_identity_index(label, prop) {
                return Err(format!(
                    "{role} label {label:?} has no required {prop:?} index in schema {}@{}",
                    schema.name,
                    schema.fingerprint()
                ));
            }
        }
        Ok(Self {
            source_label: source_label.to_string(),
            source_property: source_property.to_string(),
            target_label: target_label.to_string(),
            target_property: target_property.to_string(),
            relationship_type: relationship_type.to_string(),
            edge_property: edge_property.to_string(),
        })
    }

    pub fn state_key(&self) -> String {
        let edge_suffix = if self.edge_property.is_empty() {
            String::new()
        } else {
            format!(":{}", self.edge_property)
        };
        format!(
            "call_evidence:edges:{}:{}:{}:{}:{}{}",
            self.source_label,
            self.source_property,
            self.relationship_type,
            self.target_label,
            self.target_property,
            edge_suffix
        )
    }
}

/// `group_evidence_edges` — group self-describing edge rows theo edge shape.
pub fn group_evidence_edges(
    edges: &[Row],
) -> Result<BTreeMap<EvidenceEdgeGroup, Vec<Row>>, String> {
    let mut groups: BTreeMap<EvidenceEdgeGroup, Vec<Row>> = BTreeMap::new();
    for (position, edge) in edges.iter().enumerate() {
        let missing: Vec<&str> = EVIDENCE_EDGE_ROW_FIELDS
            .iter()
            .copied()
            .filter(|field| {
                edge.get(*field)
                    .and_then(Value::as_str)
                    .map(str::trim)
                    .unwrap_or("")
                    .is_empty()
            })
            .collect();
        let source_id = edge
            .get("source_id")
            .and_then(Value::as_str)
            .map(str::trim)
            .unwrap_or("");
        let target_id = edge
            .get("target_id")
            .and_then(Value::as_str)
            .map(str::trim)
            .unwrap_or("");
        if !missing.is_empty() || source_id.is_empty() || target_id.is_empty() {
            return Err(format!(
                "evidence edge row requires source/target label, property, id, \
                 and rel_type (row {position})"
            ));
        }
        let mut row = edge.clone();
        let property_scope = row
            .get("props")
            .and_then(Value::as_object)
            .and_then(|props| props.get("project_id"))
            .and_then(Value::as_str);
        let project_id = row_str(&row, "project_id")
            .map(str::to_string)
            .or_else(|| property_scope.map(str::to_string));
        let normalized_scope = project_id_lookup_key(project_id.as_deref())
            .ok_or_else(|| {
                format!("evidence edge row requires project_id (row {position})")
            })?;
        let project_id = project_id.unwrap_or_default().trim().to_string();
        row.insert("project_id".to_string(), Value::String(project_id));
        row.insert(
            "project_id_normalized".to_string(),
            Value::String(normalized_scope),
        );
        let edge_property = row
            .get("edge_property")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let edge_id = row
            .get("edge_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if !edge_property.is_empty() && edge_id.is_empty() {
            // Keyed merge thiếu key value sẽ collapse distinct edges onto
            // endpoint identity và mất evidence im lặng.
            return Err(format!(
                "evidence edge row with an edge_property requires a non-empty \
                 edge_id (row {position})"
            ));
        }
        let edge_id = if edge_id.is_empty() {
            source_id.to_string()
        } else {
            edge_id
        };
        row.insert("edge_id".to_string(), Value::String(edge_id));
        let group = EvidenceEdgeGroup::new(
            row.get("source_label").and_then(Value::as_str).unwrap_or(""),
            row.get("source_property").and_then(Value::as_str).unwrap_or(""),
            row.get("target_label").and_then(Value::as_str).unwrap_or(""),
            row.get("target_property").and_then(Value::as_str).unwrap_or(""),
            row.get("rel_type").and_then(Value::as_str).unwrap_or(""),
            &edge_property,
        )?;
        groups.entry(group).or_default().push(row);
    }
    Ok(groups)
}

/// `compile_evidence_edge_upsert`.
pub fn compile_evidence_edge_upsert(group: &EvidenceEdgeGroup) -> String {
    let edge_pattern = if !group.edge_property.is_empty() {
        format!(
            "MERGE (a)-[r:{rel} {{{prop}: row.edge_id}}]->(b) ",
            rel = group.relationship_type,
            prop = group.edge_property,
        )
    } else {
        format!("MERGE (a)-[r:{}]->(b) ", group.relationship_type)
    };
    format!(
        "UNWIND $rows AS row \
         MATCH (a:{source_label} {{{source_property}: row.source_id, project_id_normalized: row.project_id_normalized}}) \
         WITH row, a \
         MATCH (b:{target_label} {{{target_property}: row.target_id, project_id_normalized: row.project_id_normalized}}) \
         {edge_pattern}\
         SET r += coalesce(row.props, {{}}), \
         r.project_id = row.project_id, \
         r.updated_at = datetime() \
         RETURN count(r) AS count",
        source_label = group.source_label,
        source_property = group.source_property,
        target_label = group.target_label,
        target_property = group.target_property,
    )
}

/// Ladybug: `SET r += …` trên relationship + map rỗng đều không được hỗ
/// trợ — variant bỏ dynamic props (caller fail-closed khi props khác rỗng).
pub fn compile_evidence_edge_upsert_ladybug(group: &EvidenceEdgeGroup) -> String {
    let edge_pattern = if !group.edge_property.is_empty() {
        format!(
            "MERGE (a)-[r:{rel} {{{prop}: row.edge_id}}]->(b) ",
            rel = group.relationship_type,
            prop = group.edge_property,
        )
    } else {
        format!("MERGE (a)-[r:{}]->(b) ", group.relationship_type)
    };
    format!(
        "UNWIND $rows AS row \
         MATCH (a:{source_label} {{{source_property}: row.source_id, project_id_normalized: row.project_id_normalized}}) \
         WITH row, a \
         MATCH (b:{target_label} {{{target_property}: row.target_id, project_id_normalized: row.project_id_normalized}}) \
         {edge_pattern}\
         SET r.project_id = row.project_id, \
         r.updated_at = datetime() \
         RETURN count(r) AS count",
        source_label = group.source_label,
        source_property = group.source_property,
        target_label = group.target_label,
        target_property = group.target_property,
    )
}

/// Ladybug: `SET r += …` trên relationship + map rỗng đều không được hỗ
/// trợ — variant bỏ dynamic props (caller fail-closed khi props khác rỗng).
/// `compile_evidence_edge_readback`.
pub fn compile_evidence_edge_readback(group: &EvidenceEdgeGroup) -> String {
    let edge_match = if !group.edge_property.is_empty() {
        format!(
            "-[r:{rel} {{{prop}: row.edge_id}}]->",
            rel = group.relationship_type,
            prop = group.edge_property,
        )
    } else {
        format!("-[r:{}]->", group.relationship_type)
    };
    format!(
        "UNWIND $rows AS row \
         OPTIONAL MATCH (a:{source_label} {{{source_property}: row.source_id, project_id_normalized: row.project_id_normalized}}) \
         {edge_match}\
         (b:{target_label} {{{target_property}: row.target_id, project_id_normalized: row.project_id_normalized}}) \
         RETURN count(r) AS count",
        source_label = group.source_label,
        source_property = group.source_property,
        target_label = group.target_label,
        target_property = group.target_property,
    )
}

/// `compile_evidence_endpoint_audit`.
pub fn compile_evidence_endpoint_audit(group: &EvidenceEdgeGroup) -> String {
    format!(
        "UNWIND $rows AS row \
         OPTIONAL MATCH (a:{source_label} {{{source_property}: row.source_id, project_id_normalized: row.project_id_normalized}}) \
         WITH row, count(a) AS source_matches \
         OPTIONAL MATCH (b:{target_label} {{{target_property}: row.target_id, project_id_normalized: row.project_id_normalized}}) \
         WITH row, source_matches, count(b) AS target_matches \
         WHERE source_matches <> 1 OR target_matches <> 1 \
         RETURN row.source_id AS source_id, row.target_id AS target_id, \
         row.project_id_normalized AS project_id_normalized, \
         source_matches, target_matches LIMIT 20",
        source_label = group.source_label,
        source_property = group.source_property,
        target_label = group.target_label,
        target_property = group.target_property,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn row(map: serde_json::Value) -> Row {
        map.as_object().unwrap().clone()
    }

    #[test]
    fn compile_relationship_upsert_matches_python_byte_for_byte() {
        let group = RelationshipGroup::new_unchecked("Class", "Class", "EXTENDS");
        let expected = concat!(
            "UNWIND $rows AS row ",
            "MATCH (a:Class {id: row.source_id, project_id_normalized: row.project_id_normalized}) ",
            "WITH row, a ",
            "MATCH (b:Class {id: row.target_id, project_id_normalized: row.project_id_normalized}) ",
            "MERGE (a)-[r:EXTENDS]->(b) ",
            "SET r += coalesce(row.properties, {}), ",
            "r.project_id = row.project_id, ",
            "r.project_id_normalized = row.project_id_normalized ",
            "RETURN count(r) AS count"
        );
        assert_eq!(compile_relationship_upsert(&group), expected);
    }

    #[test]
    fn compile_relationship_endpoint_audit_matches_python() {
        let group = RelationshipGroup::new_unchecked("Function", "Function", "CALLS");
        let expected = concat!(
            "UNWIND $rows AS row ",
            "OPTIONAL MATCH (a:Function {id: row.source_id, project_id_normalized: row.project_id_normalized}) ",
            "WITH row, count(a) AS source_matches ",
            "OPTIONAL MATCH (b:Function {id: row.target_id, project_id_normalized: row.project_id_normalized}) ",
            "WITH row, source_matches, count(b) AS target_matches ",
            "WHERE source_matches <> 1 OR target_matches <> 1 ",
            "RETURN row.source_id AS source_id, row.target_id AS target_id, ",
            "row.project_id_normalized AS project_id_normalized, ",
            "source_matches, target_matches LIMIT 20"
        );
        assert_eq!(compile_relationship_endpoint_audit(&group), expected);
    }

    #[test]
    fn compile_evidence_edge_upsert_with_edge_property() {
        let group = EvidenceEdgeGroup::new(
            "CallSite",
            "site_id",
            "Function",
            "id",
            "RESOLVES_TO",
            "site_id",
        )
        .unwrap();
        let expected = concat!(
            "UNWIND $rows AS row ",
            "MATCH (a:CallSite {site_id: row.source_id, project_id_normalized: row.project_id_normalized}) ",
            "WITH row, a ",
            "MATCH (b:Function {id: row.target_id, project_id_normalized: row.project_id_normalized}) ",
            "MERGE (a)-[r:RESOLVES_TO {site_id: row.edge_id}]->(b) ",
            "SET r += coalesce(row.props, {}), ",
            "r.project_id = row.project_id, ",
            "r.updated_at = datetime() ",
            "RETURN count(r) AS count"
        );
        assert_eq!(compile_evidence_edge_upsert(&group), expected);
    }

    #[test]
    fn group_typed_relations_requires_scope_and_ids() {
        let err = group_typed_relations(&[row(json!({"rel_type": "CALLS"}))], None).unwrap_err();
        assert!(err.contains("source_label"), "{err}");
        let err = group_typed_relations(
            &[row(json!({
                "source_label": "Function",
                "target_label": "Function",
                "rel_type": "CALLS",
            }))],
            None,
        )
        .unwrap_err();
        assert!(err.contains("source_id"), "{err}");
        let err = group_typed_relations(
            &[row(json!({
                "source_label": "Function",
                "target_label": "Function",
                "rel_type": "CALLS",
                "source_id": "f1",
                "target_id": "f2",
            }))],
            None,
        )
        .unwrap_err();
        assert!(err.contains("project_id"), "{err}");
    }

    #[test]
    fn group_typed_relations_defaults_scope_and_keeps_position() {
        let groups = group_typed_relations(
            &[row(json!({
                "source_label": "Function",
                "target_label": "Function",
                "rel_type": "CALLS",
                "source_id": "f1",
                "target_id": "f2",
            }))],
            Some("Bank"),
        )
        .unwrap();
        assert_eq!(groups.len(), 1);
        let rows = groups.values().next().unwrap();
        assert_eq!(rows[0]["project_id_normalized"], json!("bank"));
        assert_eq!(rows[0]["_contract_row_position"], json!(0));
    }

    #[test]
    fn evidence_edge_keyed_merge_requires_edge_id() {
        let err = group_evidence_edges(&[row(json!({
            "source_label": "CallSite",
            "source_property": "site_id",
            "source_id": "s1",
            "target_label": "Function",
            "target_property": "id",
            "target_id": "f1",
            "rel_type": "RESOLVES_TO",
            "edge_property": "site_id",
            "project_id": "p",
        }))])
        .unwrap_err();
        assert!(err.contains("edge_id"), "{err}");
    }

    #[test]
    fn evidence_edge_state_key_shape() {
        let group = EvidenceEdgeGroup::new(
            "CallSite",
            "site_id",
            "Function",
            "id",
            "OBSERVED_AS",
            "evidence_id",
        )
        .unwrap();
        assert_eq!(
            group.state_key(),
            "call_evidence:edges:CallSite:site_id:OBSERVED_AS:Function:id:evidence_id"
        );
        let plain = EvidenceEdgeGroup::new(
            "Function",
            "id",
            "CallSite",
            "site_id",
            "HAS_CALLSITE",
            "",
        )
        .unwrap();
        assert_eq!(
            plain.state_key(),
            "call_evidence:edges:Function:id:HAS_CALLSITE:CallSite:site_id"
        );
    }
}
