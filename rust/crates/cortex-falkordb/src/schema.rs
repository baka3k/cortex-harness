//! Bảng tên schema (labels / relationship types / property keys) của một graph.
//!
//! Trên dây, node/edge mang *id* số cho label, rel-type và property key; client
//! tự giữ bảng tên. falkordb-py làm đúng vậy qua `DB.LABELS`,
//! `DB.RELATIONSHIPTYPES`, `DB.PROPERTYKEYS` (thứ tự trả về chính là id).
//! Khi gặp id ngoài bảng, caller refresh bảng rồi parse lại.

#[derive(Debug, Clone)]
pub enum SchemaError {
    OutOfRange { kind: &'static str, id: i64 },
}

impl std::fmt::Display for SchemaError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SchemaError::OutOfRange { kind, id } => {
                write!(f, "{kind} id {id} ngoài bảng tên schema — cần refresh")
            }
        }
    }
}

impl std::error::Error for SchemaError {}

#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
pub struct GraphSchema {
    pub labels: Vec<String>,
    pub relationships: Vec<String>,
    pub properties: Vec<String>,
}

impl GraphSchema {
    pub fn from_tables(
        labels: Vec<String>,
        relationships: Vec<String>,
        properties: Vec<String>,
    ) -> Self {
        Self {
            labels,
            relationships,
            properties,
        }
    }

    pub fn label_name(&self, id: i64) -> Result<String, SchemaError> {
        self.labels
            .get(id as usize)
            .cloned()
            .ok_or(SchemaError::OutOfRange { kind: "label", id })
    }

    pub fn relationship_name(&self, id: i64) -> Result<String, SchemaError> {
        self.relationships.get(id as usize).cloned().ok_or(
            SchemaError::OutOfRange {
                kind: "relationship type",
                id,
            },
        )
    }

    pub fn property_name(&self, id: i64) -> Result<String, SchemaError> {
        self.properties.get(id as usize).cloned().ok_or(
            SchemaError::OutOfRange { kind: "property", id },
        )
    }
}
