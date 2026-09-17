# Phase 03 — Full Member Inventory

## Goal
Mở rộng data model và graph output để cover tất cả C# member types: properties, fields, events, delegates, generics, attributes, XML docs.

## Scope

### Enhanced Type Information

Upgrade `TypeDef` → `EnhancedTypeDef` with:
- `base_types`: resolved base class chain
- `implemented_interfaces`: resolved interface list
- `type_parameters`: generic type parameter names
- `type_constraints`: constraints per type parameter (class, struct, new(), specific types)
- `is_abstract`, `is_sealed`, `is_static`, `is_partial`, `is_record`
- `accessibility`: public, private, protected, internal, protected_internal
- `attributes`: resolved attribute names
- `xml_doc`: parsed XML documentation

### Enhanced Function Information

Upgrade `FunctionDef` → `EnhancedFunctionDef` with:
- `return_type`: resolved return type
- `type_parameters`: generic type parameters
- `is_async`, `is_static`, `is_virtual`, `is_override`, `is_abstract`
- `is_extension_method`: detected extension methods
- `accessibility`
- `attributes`: resolved attribute names
- `parameter_details`: full `ParameterDef` list
- `xml_doc`: parsed XML documentation
- `is_lambda`: lambda expression bodies
- `is_local_function`: local function declarations

### New Member Types

#### Properties (`PropertyDef`)
```python
@dataclass
class PropertyDef:
    symbol_id: str
    qualified_name: str
    name: str
    type_name: str           # resolved type name
    accessibility: str
    has_getter: bool
    has_setter: bool
    is_static: bool
    is_virtual: bool
    is_override: bool
    is_abstract: bool
    is_auto_property: bool
    is_indexer: bool         # this[] indexer
    attributes: List[str]
    xml_doc: str
    file_path: str
    start_line: int
    end_line: int
    code: str
```

#### Fields (`FieldDef`)
```python
@dataclass
class FieldDef:
    symbol_id: str
    qualified_name: str
    name: str
    type_name: str
    accessibility: str
    is_static: bool
    is_const: bool
    is_readonly: bool
    is_volatile: bool
    constant_value: Optional[str]  # for const fields
    attributes: List[str]
    xml_doc: str
    file_path: str
    start_line: int
    end_line: int
    code: str
```

#### Events (`EventDef`)
```python
@dataclass
class EventDef:
    symbol_id: str
    qualified_name: str
    name: str
    delegate_type: str       # event handler type
    accessibility: str
    is_static: bool
    is_abstract: bool
    attributes: List[str]
    xml_doc: str
    file_path: str
    start_line: int
    end_line: int
    code: str
```

#### Delegates (`DelegateDef`)
```python
@dataclass
class DelegateDef:
    symbol_id: str
    qualified_name: str
    name: str
    return_type: str
    type_parameters: List[str]
    parameter_types: List[ParameterDef]
    accessibility: str
    attributes: List[str]
    xml_doc: str
    file_path: str
    start_line: int
    end_line: int
    code: str
```

### Graph Output

#### New Node Labels
```
Property, Field, Event, Delegate, Parameter, GenericParameter
```

#### New Relationships
```
Type -[:HAS_PROPERTY]-> Property
Type -[:HAS_FIELD]-> Field
Type -[:HAS_EVENT]-> Event
Type -[:HAS_DELEGATE]-> Delegate
Property -[:HAS_PARAMETER]-> Parameter  (for indexers)
Delegate -[:HAS_PARAMETER]-> Parameter
Type -[:HAS_GENERIC_PARAMETER]-> GenericParameter
GenericParameter -[:CONSTRAINED_BY]-> GenericParameter  (self-ref for constraints)
```

#### Enhanced Existing Relationships
```
Type -[:EXTENDS_CLASS {resolved: true, type_args: [...]}]-> Type
Type -[:IMPLEMENTS_INTERFACE {resolved: true, type_args: [...]}]-> Type
```

### Roslyn Worker Extraction

`MemberExtractor.cs` must handle:
1. **Property declarations**: auto-props, full props, expression-bodied, indexers
2. **Field declarations**: regular, const, readonly, volatile
3. **Event declarations**: field-like events, custom events
4. **Delegate declarations**: with generic parameters
5. **Generic types**: class/struct/interface/delegate with constraints
6. **Partial types**: merge across files in workspace mode
7. **Record types**: positional and nominal records
8. **Nested types**: types within types

### Backward Compatibility

- Existing `FunctionDef`, `TypeDef` fields remain unchanged
- New fields are additive with defaults
- Existing graph queries continue to work
- New labels/relationships are additive

### Implementation Steps

1. **T01**: Update `models.py` with new data classes
2. **T02**: Update Roslyn worker `MemberExtractor.cs` to emit full member inventory
3. **T03**: Update `csharp_analyzer.py` graph ingestion to write new nodes/relationships
4. **T04**: Update `LanguageCodeWriter` or create C#-specific writer extensions
5. **T05**: Update parse cache version and normalization
6. **T06**: Golden tests for each member type
7. **T07**: Graph tests for new labels and relationships

## Acceptance Criteria

- Properties, fields, events, delegates are extracted and written to graph
- Generic type parameters and constraints are captured
- Attributes are extracted with resolved names
- XML documentation is parsed and stored
- Partial classes are merged in workspace mode
- Record types are detected
- Accessibility modifiers are correct
- Existing tests remain green
- Graph queries can traverse Type → Property/Field/Event/Delegate
