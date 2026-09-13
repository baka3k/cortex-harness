//! Port `tools/spring/annotation_catalog.py`.

use std::collections::{BTreeMap, BTreeSet};

pub fn short_annotation_name(name: &str) -> String {
    let mut raw = name.trim().to_string();
    if let Some(index) = raw.find(':') {
        raw = raw[index + 1..].to_string();
    }
    match raw.rfind('.') {
        Some(index) => raw[index + 1..].to_string(),
        None => raw,
    }
}

pub fn application_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["SpringBootApplication", "EnableAutoConfiguration"])
}
pub fn configuration_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["Configuration"])
}
pub fn bean_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["Bean"])
}
pub fn component_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["Component", "Service", "Repository", "Controller", "RestController"])
}
pub fn controller_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["Controller", "RestController"])
}
pub fn service_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["Service"])
}
pub fn repository_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["Repository"])
}
pub fn injection_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["Autowired", "Inject", "Resource"])
}
pub fn value_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["Value", "ConfigurationProperties"])
}
pub fn http_mapping_methods() -> BTreeMap<&'static str, &'static str> {
    BTreeMap::from([
        ("GetMapping", "GET"),
        ("PostMapping", "POST"),
        ("PutMapping", "PUT"),
        ("PatchMapping", "PATCH"),
        ("DeleteMapping", "DELETE"),
    ])
}
pub fn http_mapping_annotations() -> BTreeSet<&'static str> {
    let mut set: BTreeSet<&'static str> = BTreeSet::from(["RequestMapping"]);
    set.extend(http_mapping_methods().keys().copied());
    set
}
pub fn entity_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["Entity", "MappedSuperclass", "Embeddable"])
}
pub fn repository_supertypes() -> BTreeSet<&'static str> {
    BTreeSet::from([
        "Repository",
        "CrudRepository",
        "JpaRepository",
        "PagingAndSortingRepository",
        "ReactiveCrudRepository",
    ])
}
pub fn transaction_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["Transactional"])
}
pub fn message_listener_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["KafkaListener", "RabbitListener", "RabbitHandler"])
}
pub fn scheduled_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["Scheduled"])
}
pub fn async_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["Async"])
}
pub fn event_listener_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["EventListener", "TransactionalEventListener"])
}
pub fn security_method_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["PreAuthorize", "PostAuthorize", "PreFilter", "PostFilter", "Secured", "RolesAllowed"])
}
pub fn aop_class_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["Aspect"])
}
pub fn aop_method_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["Pointcut", "Before", "After", "AfterReturning", "AfterThrowing", "Around"])
}
pub fn validation_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from([
        "Valid",
        "Validated",
        "NotNull",
        "NotBlank",
        "NotEmpty",
        "Size",
        "Min",
        "Max",
        "Pattern",
        "Email",
        "Positive",
        "PositiveOrZero",
        "Negative",
        "NegativeOrZero",
    ])
}
pub fn cache_annotations() -> BTreeSet<&'static str> {
    BTreeSet::from(["Cacheable", "CachePut", "CacheEvict", "Caching"])
}
