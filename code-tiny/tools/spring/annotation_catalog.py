from __future__ import annotations

from typing import Dict, FrozenSet, Iterable, Optional


def short_annotation_name(name: str) -> str:
    raw = (name or "").strip()
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    return raw.rsplit(".", 1)[-1]


APPLICATION_ANNOTATIONS: FrozenSet[str] = frozenset({"SpringBootApplication", "EnableAutoConfiguration"})
CONFIGURATION_ANNOTATIONS: FrozenSet[str] = frozenset({"Configuration"})
BEAN_ANNOTATIONS: FrozenSet[str] = frozenset({"Bean"})
COMPONENT_ANNOTATIONS: FrozenSet[str] = frozenset({"Component", "Service", "Repository", "Controller", "RestController"})
CONTROLLER_ANNOTATIONS: FrozenSet[str] = frozenset({"Controller", "RestController"})
SERVICE_ANNOTATIONS: FrozenSet[str] = frozenset({"Service"})
REPOSITORY_ANNOTATIONS: FrozenSet[str] = frozenset({"Repository"})
INJECTION_ANNOTATIONS: FrozenSet[str] = frozenset({"Autowired", "Inject", "Resource"})
VALUE_ANNOTATIONS: FrozenSet[str] = frozenset({"Value", "ConfigurationProperties"})
HTTP_MAPPING_METHODS: Dict[str, str] = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "PatchMapping": "PATCH",
    "DeleteMapping": "DELETE",
}
HTTP_MAPPING_ANNOTATIONS: FrozenSet[str] = frozenset({"RequestMapping", *HTTP_MAPPING_METHODS.keys()})
ENTITY_ANNOTATIONS: FrozenSet[str] = frozenset({"Entity", "MappedSuperclass", "Embeddable"})
REPOSITORY_SUPERTYPES: FrozenSet[str] = frozenset({
    "Repository",
    "CrudRepository",
    "JpaRepository",
    "PagingAndSortingRepository",
    "ReactiveCrudRepository",
})
TRANSACTION_ANNOTATIONS: FrozenSet[str] = frozenset({"Transactional"})
MESSAGE_LISTENER_ANNOTATIONS: FrozenSet[str] = frozenset({"KafkaListener", "RabbitListener", "RabbitHandler"})
SCHEDULED_ANNOTATIONS: FrozenSet[str] = frozenset({"Scheduled"})
ASYNC_ANNOTATIONS: FrozenSet[str] = frozenset({"Async"})
EVENT_LISTENER_ANNOTATIONS: FrozenSet[str] = frozenset({"EventListener", "TransactionalEventListener"})
SECURITY_METHOD_ANNOTATIONS: FrozenSet[str] = frozenset({
    "PreAuthorize",
    "PostAuthorize",
    "PreFilter",
    "PostFilter",
    "Secured",
    "RolesAllowed",
})
AOP_CLASS_ANNOTATIONS: FrozenSet[str] = frozenset({"Aspect"})
AOP_METHOD_ANNOTATIONS: FrozenSet[str] = frozenset({
    "Pointcut",
    "Before",
    "After",
    "AfterReturning",
    "AfterThrowing",
    "Around",
})
VALIDATION_ANNOTATIONS: FrozenSet[str] = frozenset({
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
})
CACHE_ANNOTATIONS: FrozenSet[str] = frozenset({"Cacheable", "CachePut", "CacheEvict", "Caching"})


def has_any_annotation(names: Iterable[str], catalog: Iterable[str]) -> bool:
    known = set(catalog)
    return any(short_annotation_name(item) in known for item in names)


def first_annotation(names: Iterable[str], catalog: Iterable[str]) -> Optional[str]:
    known = set(catalog)
    for item in names:
        short = short_annotation_name(item)
        if short in known:
            return short
    return None
