"""VB6 project-model resolver (plan 260917-1200, phase 04).

Engine-independent: works over ANTLR payloads (which carry ``module_name``,
``call_type``, ``resolution_status``) and regex payloads (which carry only
names). Resolution order per red-team digest R5 / phase-04 4.2:

1. exact qualified (``module.proc`` or receiver = known module/class)
2. module-local (Private + Public of the caller's module)
3. project-public (Public procedures of standard modules, unique match)
4. arity filter when several candidates remain

Nothing is dropped: ambiguous/late-bound/external/unresolved calls keep a
``resolution_status`` so the publication layer can route them to
POSSIBLE_CALLS instead of discarding them.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tools.vb.vb_common import CallEdge, FunctionDef

#: VB6 intrinsic functions (no project target can exist)
_BUILTIN_FUNCTIONS = frozenset({
    "msgbox", "inputbox", "print", "cint", "clng", "cstr", "cdbl", "cbool",
    "cbyte", "cdate", "cvar", "cverr", "val", "len", "left", "right", "mid",
    "trim", "rtrim", "ltrim", "ucase", "lcase", "instr", "instrrev", "replace",
    "split", "join", "format", "isnull", "isempty", "isobject", "isnumeric",
    "isarray", "isdate", "ismissing", "createobject", "getobject", "array",
    "abs", "int", "fix", "sgn", "sqr", "atn", "cos", "sin", "tan", "exp",
    "log", "rnd", "randomize", "now", "date", "time", "timer", "doevents",
    "beep", "environ", "shell", "sendkeys", "load", "unload", "me", "strconv",
    "hex", "oct", "chr", "asc", "dir", "curdir", "chdir", "mkdir", "rmdir",
    "kill", "filecopy", "name", "freefile", "lopen", "close", "eof", "loc",
    "lof", "lineinput", "appactivate",
})

#: intrinsic objects whose members can never be project procedures
_INTRINSIC_OBJECTS = frozenset({
    "debug", "err", "app", "screen", "printer", "clipboard", "forms",
    "screen.twipsperpixelx",
})

#: COM type-library namespaces (plan 260924 3.1): receivers declared with one
#: of these prefixes get an annotated USES_TYPE to a com-kind Type node
#: instead of a blind late_bound/unresolved classification
COM_TYPE_PREFIXES = ("adodb.", "dao.", "rdo.", "scripting.")

#: designer pseudo-controls (mirror of vb_analyzer_base.VB6_PSEUDO_CONTROL_
#: TYPES; duplicated to avoid the analyzer→resolver import cycle)
_PSEUDO_CONTROL_TYPES = frozenset({"form", "mdiform", "usercontrol"})


def is_com_type_name(type_name: str) -> bool:
    key = _norm(type_name)
    return any(key.startswith(prefix) for prefix in COM_TYPE_PREFIXES)


def external_com_type_id(type_name: str) -> str:
    """Stable :Type id for an external/COM class (mirrors the analyzer builder)."""

    return f"external::vb6/{(type_name or '').strip().lower()}"

_CALLER_ID_RE = re.compile(r"^(?P<module>[^./]+)\.(?P<proc>[^/]+)/(?P<arity>\d+)@")


def _norm(name: str) -> str:
    return (name or "").strip().lstrip(".").lower()


@dataclass
class VariableInfo:
    name: str
    type_name: str
    module_name: str
    procedure_name: Optional[str]
    symbol_id: str = ""

    @property
    def type_key(self) -> str:
        return _norm(self.type_name)

    @property
    def late_bound(self) -> bool:
        return self.type_key in {"object", "variant", ""}


@dataclass
class ModuleInfo:
    module_name: str
    file_path: str
    kind: str  # bas | cls | frm | ctl | pag
    # name -> ALL overloads (a VB6 property legitimately exists as both
    # Property Get and Property Let with the same name)
    functions: Dict[str, List[FunctionDef]] = field(default_factory=dict)
    implements: List[str] = field(default_factory=list)
    # plan 260917-1628 3.3: Attribute VB_PredeclaredId = True registers a
    # default instance (forms AND class modules)
    predeclared: bool = False

    def functions_named(self, member: str) -> List[FunctionDef]:
        return self.functions.get(_norm(member), [])

    def public_functions(self) -> List[FunctionDef]:
        return [fn for fns in self.functions.values() for fn in fns if not fn.is_private]


class VB6ModuleRegistry:
    """Project model: modules by (Attribute VB_Name) name + lookup indexes."""

    def __init__(self) -> None:
        self.modules: Dict[str, ModuleInfo] = {}
        self.module_by_file: Dict[str, ModuleInfo] = {}
        self.variables: List[VariableInfo] = []
        self.public_by_name: Dict[str, List[Tuple[str, FunctionDef]]] = {}
        self.any_by_name: Dict[str, List[Tuple[str, FunctionDef]]] = {}
        # interface name -> implementing module keys (plan 260917-1628 3.1;
        # reverse-derived from the payloads' implements lists, red-team F4-ctx)
        self.implementers_of: Dict[str, List[str]] = {}
        # declared Windows API names (declares plane) -> unqualified calls to
        # them classify external, not unresolved (review fix F1)
        self.api_names: set = set()
        # plan 260924 anchor graph: designer controls / module constants /
        # parameter types / module-level state ids for the ui_access and
        # instantiation resolution passes
        self.control_by_module: Dict[str, Dict[str, str]] = {}
        self.constant_ids: Dict[str, List[Tuple[str, str]]] = {}
        self.state_variable_ids: Dict[str, List[Tuple[str, str]]] = {}
        self.param_types_by_proc: Dict[Tuple[str, str], Dict[str, str]] = {}

    # -- construction -------------------------------------------------

    @classmethod
    def from_payloads(cls, payloads: Sequence[Dict[str, Any]]) -> "VB6ModuleRegistry":
        registry = cls()

        def _meta_field(payload: Dict[str, Any], key: str, default: Any = None) -> Any:
            meta = payload.get("parse_meta") or {}
            if isinstance(meta, dict):
                return meta.get(key, default)
            return getattr(meta, key, default)

        for payload in payloads:
            file_def = payload.get("file_def") or {}
            file_path = str(
                file_def.get("file_path") if isinstance(file_def, dict)
                else getattr(file_def, "file_path", "") or ""
            )
            if not file_path:
                continue
            module_name = ""
            for fn in payload.get("functions", []):
                if getattr(fn, "module_name", ""):
                    module_name = fn.module_name
                    break
            if not module_name:
                base = os.path.basename(file_path)
                module_name = os.path.splitext(base)[0]
            ext = os.path.splitext(file_path)[1].lower().lstrip(".")
            kind = ext if ext in {"bas", "cls", "frm", "ctl", "pag"} else "bas"
            attrs = _meta_field(payload, "module_attributes") or {}
            predeclared = str(
                attrs.get("vb_predeclaredid", "") if isinstance(attrs, dict) else ""
            ).strip().lower() == "true"
            module = ModuleInfo(
                module_name=module_name,
                file_path=file_path,
                kind=kind,
                predeclared=predeclared,
            )
            for fn in payload.get("functions", []):
                module.functions.setdefault(_norm(fn.name), []).append(fn)
            module.implements = list(_meta_field(payload, "implements") or [])
            registry.modules.setdefault(_norm(module_name), module)
            registry.module_by_file.setdefault(file_path, module)
            for iface in module.implements:
                key = _norm(iface)
                if key:
                    registry.implementers_of.setdefault(key, []).append(_norm(module_name))

            for declare in payload.get("declares") or []:
                declare_name = (
                    declare.get("name") if isinstance(declare, dict)
                    else getattr(declare, "name", "")
                )
                if declare_name:
                    registry.api_names.add(_norm(declare_name))

            for var in payload.get("variables", []):
                def _var_field(key: str) -> Any:
                    if isinstance(var, dict):
                        return var.get(key, "")
                    return getattr(var, key, "")

                var_info = VariableInfo(
                    name=str(_var_field("name") or ""),
                    type_name=str(_var_field("type_name") or ""),
                    module_name=str(_var_field("module_name") or module_name),
                    procedure_name=(str(_var_field("procedure_name") or "") or None),
                    symbol_id=str(_var_field("symbol_id") or ""),
                )
                registry.variables.append(var_info)
                if not var_info.procedure_name and var_info.symbol_id:
                    registry.state_variable_ids.setdefault(_norm(var_info.name), []).append(
                        (var_info.symbol_id, _norm(var_info.module_name))
                    )

            for control in payload.get("controls") or []:
                if not isinstance(control, dict) or not control.get("name"):
                    continue
                cname = str(control["name"]).strip()
                ctype = str(control.get("type") or "")
                if ctype.rsplit(".", 1)[-1].lower() in _PSEUDO_CONTROL_TYPES:
                    continue
                registry.control_by_module.setdefault(_norm(module_name), {})[
                    _norm(cname)
                ] = f"{module_name}.{cname}@control"

            for const in payload.get("constants") or []:
                def _const_field(key: str) -> Any:
                    if isinstance(const, dict):
                        return const.get(key, "")
                    return getattr(const, key, "")

                const_name = str(_const_field("name") or "").strip()
                const_id = str(_const_field("symbol_id") or "")
                if const_name and const_id:
                    registry.constant_ids.setdefault(_norm(const_name), []).append(
                        (const_id, _norm(module_name))
                    )

            for fn in payload.get("functions", []):
                param_names = [str(n or "") for n in (getattr(fn, "param_names", None) or [])]
                param_types = [str(t or "") for t in (getattr(fn, "param_types", None) or [])]
                mapping = {
                    name.lower(): type_name
                    for name, type_name in zip(param_names, param_types)
                    if name and type_name
                }
                if mapping:
                    registry.param_types_by_proc[(_norm(module_name), _norm(fn.name))] = mapping

        for key, module in registry.modules.items():
            for fns in module.functions.values():
                for fn in fns:
                    registry.any_by_name.setdefault(_norm(fn.name), []).append((key, fn))
                    if not fn.is_private and module.kind == "bas":
                        registry.public_by_name.setdefault(_norm(fn.name), []).append((key, fn))
        return registry

    # -- lookups ------------------------------------------------------

    def module(self, name: str) -> Optional[ModuleInfo]:
        return self.modules.get(_norm(name))

    def qualified_lookup(self, module_name: str, member: str) -> Optional[FunctionDef]:
        candidates = self.qualified_candidates(module_name, member)
        return candidates[0] if len(candidates) == 1 else (candidates[0] if candidates else None)

    def qualified_candidates(self, module_name: str, member: str) -> List[FunctionDef]:
        module = self.modules.get(_norm(module_name))
        if module is None:
            return []
        return module.functions_named(member)

    def find_late_bound_variable(
        self, receiver: str, caller_module: str, caller_proc: str
    ) -> Optional[VariableInfo]:
        receiver_key = _norm(receiver)
        best: Optional[VariableInfo] = None
        for var in self.variables:
            if _norm(var.name) != receiver_key:
                continue
            if _norm(var.module_name) != _norm(caller_module):
                continue
            if var.procedure_name:
                if _norm(var.procedure_name or "") == _norm(caller_proc):
                    return var  # procedure-local wins
                continue
            best = best or var
        return best

    # -- anchor-graph lookups (plan 260924) ----------------------------

    def control_id(self, module_key: str, name: str) -> Optional[str]:
        return self.control_by_module.get(_norm(module_key), {}).get(_norm(name))

    def find_typed_variable(
        self, receiver: str, caller_module: str, caller_proc: str
    ) -> Optional[VariableInfo]:
        """Declaratively typed variable for a receiver (local beats module)."""

        receiver_key = _norm(receiver)
        module_best: Optional[VariableInfo] = None
        for var in self.variables:
            if _norm(var.name) != receiver_key:
                continue
            if _norm(var.module_name) != _norm(caller_module):
                continue
            if var.procedure_name:
                if _norm(var.procedure_name or "") == _norm(caller_proc):
                    return var
                continue
            module_best = module_best or var
        return module_best

    def is_local_or_param(self, name: str, caller_module: str, caller_proc: str) -> bool:
        """Shadowing check (plan 260924 3.4): a local/param beats module state."""

        key = _norm(name)
        for var in self.variables:
            if _norm(var.name) != key:
                continue
            if _norm(var.module_name) != _norm(caller_module):
                continue
            if var.procedure_name and _norm(var.procedure_name or "") == _norm(caller_proc):
                return True
        if _norm(caller_proc) in {
            proc_key for module_key, proc_key in self.param_types_by_proc
            if module_key == _norm(caller_module)
        }:
            if key in self.param_types_by_proc.get(
                (_norm(caller_module), _norm(caller_proc)), {}
            ):
                return True
        return False

    def state_variable_id(
        self, name: str, prefer_module: str
    ) -> Optional[str]:
        hits = self.state_variable_ids.get(_norm(name)) or []
        if not hits:
            return None
        prefer_key = _norm(prefer_module)
        for symbol_id, module_key in hits:
            if module_key == prefer_key:
                return symbol_id
        return hits[0][0]

    def constant_id(self, name: str, prefer_module: str) -> Optional[str]:
        hits = self.constant_ids.get(_norm(name)) or []
        if not hits:
            return None
        prefer_key = _norm(prefer_module)
        for symbol_id, module_key in hits:
            if module_key == prefer_key:
                return symbol_id
        return hits[0][0]

    def param_type(self, caller_module: str, caller_proc: str, name: str) -> Optional[str]:
        return self.param_types_by_proc.get(
            (_norm(caller_module), _norm(caller_proc)), {}
        ).get(_norm(name))

    def type_node_id(self, type_or_module_name: str) -> Optional[str]:
        """Types-lane node id (``<Name>@<rel>``) when the name is a project module.

        Only class-module kinds EMIT a :Type node (worker classes[] plane is
        gated on ClazzModule); a .bas match would produce a target no node
        exists for, and the typed-rel write aborts the whole batch.
        """

        module = self.modules.get(_norm(type_or_module_name))
        if module is None:
            return None
        if module.kind not in {"cls", "frm", "ctl", "pag"}:
            return None
        return f"{module.module_name}@{module.file_path}"

    def interface_member_candidates(
        self, interface_name: str, member: str
    ) -> List[Tuple[str, FunctionDef]]:
        """(module_key, fn) candidates for an interface-typed receiver."""

        implementers = self.implementers_of.get(_norm(interface_name)) or []
        hits: List[Tuple[str, FunctionDef]] = []
        seen: set = set()
        for module_key in implementers:
            module = self.modules.get(module_key)
            if module is None:
                continue
            for fn in module.functions_named(member):
                if fn.symbol_id in seen:
                    continue
                seen.add(fn.symbol_id)
                hits.append((module_key, fn))
        return hits


#: intrinsic form instance members: a predeclared form receiver calling these
#: stays external even though a matching project member does not exist
_FORM_INTRINSIC_MEMBERS = frozenset({
    "show", "hide", "refresh", "cls", "print", "move", "scale", "setfocus",
    "line", "circle", "pset", "point", "textwidth", "textheight",
    "popupmenu", "validatecontrols", "showwhatsthismode", "zorder",
    "linkexecute", "linkpoke", "linkrequest", "linksend",
})


def _arity_accepts(fn: FunctionDef, arg_count: Optional[int]) -> bool:
    """Signature-compatibility check (plan 260917-1628 3.2).

    ``arg_count`` is the arity the worker observed on the bound callee.
    ANTLR rows carry min_arity/optional/paramarray; regex rows fall back to
    the exact-arity behavior (defaults 0/False — no regress, red-team F4).
    """

    if arg_count is None:
        return True
    if getattr(fn, "has_paramarray", False):
        if arg_count >= int(getattr(fn, "min_arity", 0) or 0):
            return True
    if getattr(fn, "has_optional_args", False):
        if int(getattr(fn, "min_arity", 0) or 0) <= arg_count <= fn.arity:
            return True
    return fn.arity == arg_count


def _split_callee(call: CallEdge) -> Tuple[str, str]:
    """Return (receiver_or_empty, member) for a callee name.

    Handles both ``rs.Field`` member calls and dictionary (default-member)
    ``rs!Field`` rows (plan 260917-1628 3.4).
    """

    member = str(call.callee_member or "").strip()
    name = str(call.callee_name or "").strip()
    if member:
        for separator in (".", "!"):
            if name.lower().endswith(separator + member.lower()):
                receiver = name[: len(name) - len(member) - 1]
                return receiver, member
        if name.lower().endswith(member.lower()):
            receiver = name[: len(name) - len(member)]
            receiver = receiver.rstrip(".!")
            return receiver, member
        return "", member
    if not name:
        return "", ""
    if "!" in name:
        receiver, _, member = name.rpartition("!")
        return receiver, member
    if "." in name:
        receiver, _, member = name.rpartition(".")
        return receiver, member
    return "", name.lstrip(".")


def _parse_caller(call: CallEdge) -> Tuple[str, str]:
    match = _CALLER_ID_RE.match(call.caller_id or "")
    if match:
        return match.group("module"), match.group("proc")
    scope = str(call.caller_scope or "")
    return scope, ""


def _payload_module(payload: Dict[str, Any]) -> str:
    meta = payload.get("parse_meta") or {}
    module_name = ""
    if isinstance(meta, dict):
        module_name = str(meta.get("module_name") or "")
    if module_name:
        return module_name
    file_def = payload.get("file_def") or {}
    file_path = str(
        file_def.get("file_path") if isinstance(file_def, dict)
        else getattr(file_def, "file_path", "")
    )
    return os.path.splitext(os.path.basename(file_path))[0]


def _classify_receiver_type(
    registry: VB6ModuleRegistry, type_name: str
) -> Tuple[str, str]:
    """(target_kind, target_id) for a declared type name (plan 260924 3.1).

    Only COM-prefixed and project types carry a target — everything else is
    annotated unresolved/late-bound, never fake-resolved to an edge.
    """

    if is_com_type_name(type_name):
        return "com_type", external_com_type_id(type_name)
    node_id = registry.type_node_id(type_name)
    if node_id is not None:
        return "type", node_id
    if _norm(type_name) not in {"object", "variant", ""}:
        return "unresolved_type", ""
    return "late_bound", ""


def resolve_with_targets(
    payloads: Sequence[Dict[str, Any]], registry: VB6ModuleRegistry
) -> Dict[str, int]:
    """Annotate with_targets + attach via_with member rows (plan 260924 3.3).

    Classification order: control → variable/param type → form/class module
    → unknown. Member rows inside the block attach to the INNERMOST covering
    target (greatest line wins among covering blocks) and inherit its
    classification via ``with_target_kind``/``with_target_id``.
    """

    counts: Dict[str, int] = {"control": 0, "type": 0, "com_type": 0, "unknown": 0}
    for payload in payloads or []:
        module_key = _norm(_payload_module(payload))
        targets = payload.get("with_targets") or []
        for target in targets:
            module_key_t = _norm(str(target.get("module") or "") or module_key)
            expr = str(target.get("expr_raw") or "").strip()
            if target.get("is_new"):
                target["target_kind"] = "new"
                continue
            control = registry.control_id(module_key_t, expr)
            if control is not None:
                target["target_kind"] = "control"
                target["target_id"] = control
            else:
                proc = str(target.get("proc") or "")
                kind, target_id = _classify_receiver_type(registry, expr)
                if kind in {"late_bound", "unresolved_type"}:
                    # plan 3.3 order: control → typed variable/param →
                    # form/class module → unknown (a bare typed-variable
                    # name classifies unresolved_type BEFORE this lookup)
                    var = registry.find_typed_variable(expr, module_key_t, proc)
                    if var is not None and not var.late_bound:
                        kind, target_id = _classify_receiver_type(registry, var.type_name)
                    else:
                        var_type = registry.param_type(module_key_t, proc, expr)
                        if var_type:
                            kind, target_id = _classify_receiver_type(registry, var_type)
                        elif var is not None:
                            kind = "late_bound"
                if kind == "unresolved_type":
                    # a With target is a bare name: without a project module,
                    # control, or typed declaration it stays unknown (no edge)
                    kind = "unknown"
                    target_id = ""
                if kind in counts:
                    counts[kind] += 1
                target["target_kind"] = kind
                target["target_id"] = target_id
        for row in payload.get("ui_access") or []:
            if not (row.get("via_with") and row.get("member")):
                continue
            covering = [
                t for t in targets
                if str(t.get("proc") or "") == str(row.get("proc") or "")
                and int(t.get("line") or 0) <= int(row.get("line") or 0) <= int(t.get("block_end_line") or 0)
            ]
            if not covering:
                continue
            innermost = max(
                covering, key=lambda t: (int(t.get("line") or 0), int(t.get("block_end_line") or 0))
            )
            row["with_target_kind"] = str(innermost.get("target_kind") or "unknown")
            row["with_target_id"] = str(innermost.get("target_id") or "")
    return counts


def resolve_ui_access(
    payloads: Sequence[Dict[str, Any]], registry: VB6ModuleRegistry
) -> Dict[str, int]:
    """Annotate ui_access rows in place (plan 260924 3.1/3.4).

    Member rows: control → typed variable/param (COM → com_type; project
    class/interface → type). State rows (member==""): module-level variable →
    module constant, with local/param shadowing checked first. Unknown stays
    unknown — no edge is ever fabricated.
    """

    counts: Dict[str, int] = {}
    for payload in payloads or []:
        module_key = _norm(_payload_module(payload))
        for row in payload.get("ui_access") or []:
            proc = str(row.get("proc") or "")
            receiver = str(row.get("receiver_raw") or "").strip()
            member = str(row.get("member") or "").strip()
            if member and row.get("via_with"):
                # attached by resolve_with_targets; keep its classification
                kind = str(row.get("with_target_kind") or "unknown")
                counts[kind] = counts.get(kind, 0) + 1
                continue
            if member:
                control = registry.control_id(module_key, receiver)
                if control is not None:
                    row["target_kind"] = "control"
                    row["target_id"] = control
                else:
                    kind = "unknown"
                    target_id = ""
                    var = registry.find_typed_variable(receiver, module_key, proc)
                    var_type = ""
                    if var is not None and not var.late_bound:
                        var_type = var.type_name
                    else:
                        var_type = registry.param_type(module_key, proc, receiver) or ""
                    if var_type:
                        kind, target_id = _classify_receiver_type(registry, var_type)
                    elif var is not None:
                        kind = "late_bound"
                    row["target_kind"] = kind
                    row["target_id"] = target_id
                    if is_com_type_name(var_type):
                        row["com_type"] = var_type
            else:
                if registry.is_local_or_param(receiver, module_key, proc):
                    row["target_kind"] = "local_shadow"
                else:
                    state_id = registry.state_variable_id(receiver, module_key)
                    if state_id is not None:
                        row["target_kind"] = "variable"
                        row["target_id"] = state_id
                    else:
                        const_id = registry.constant_id(receiver, module_key)
                        if const_id is not None:
                            row["target_kind"] = "constant"
                            row["target_id"] = const_id
                        else:
                            row["target_kind"] = "unknown"
                counts[row["target_kind"]] = counts.get(row["target_kind"], 0) + 1
    return counts


def resolve_instantiations(
    payloads: Sequence[Dict[str, Any]], registry: VB6ModuleRegistry
) -> Dict[str, int]:
    """Annotate `New <CLS>` rows (plan 260924 3.2).

    Project class (case-insensitive module-name match, mirroring the calls
    walk's moduleNames guard) → types-lane node; anything else → com-kind
    external Type node + USES_TYPE (annotation only — never fake-resolved).
    """

    counts: Dict[str, int] = {"project": 0, "external": 0}
    for payload in payloads or []:
        for row in payload.get("instantiations") or []:
            cls_name = str(row.get("name") or "").strip()
            node_id = registry.type_node_id(cls_name)
            if node_id is not None:
                row["target_kind"] = "project"
                row["target_id"] = node_id
                counts["project"] += 1
            else:
                row["target_kind"] = "external"
                row["target_id"] = external_com_type_id(cls_name)
                counts["external"] += 1
    return counts


def resolve_anchor_planes(
    payloads: Sequence[Dict[str, Any]], registry: VB6ModuleRegistry, verbose: bool = False
) -> Dict[str, Dict[str, int]]:
    """Run every anchor-plane resolution pass (with-targets first so member
    rows can attach). Returns per-pass counters for the verbose log."""

    results = {
        "with_targets": resolve_with_targets(payloads, registry),
        "ui_access": resolve_ui_access(payloads, registry),
        "instantiations": resolve_instantiations(payloads, registry),
    }
    if verbose:
        print(f"[vb6][resolver][anchors] {results}", flush=True)
    return results


def resolve_vb6_calls(
    functions: Sequence[FunctionDef],
    calls: Sequence[CallEdge],
    *,
    payloads: Optional[Sequence[Dict[str, Any]]] = None,
    verbose: bool = False,
) -> Dict[str, int]:
    """Resolve VB6 call edges in place; returns status counters."""

    registry = VB6ModuleRegistry.from_payloads(payloads or [])
    # payload-less callers fall back to module names recorded on functions
    if not registry.modules and functions:
        for fn in functions:
            key = _norm(getattr(fn, "module_name", "") or "")
            if not key:
                continue
            module = registry.modules.get(key)
            if module is None:
                module = ModuleInfo(
                    module_name=fn.module_name,
                    file_path=fn.file_path,
                    kind="bas",
                )
                registry.modules[key] = module
                registry.module_by_file.setdefault(fn.file_path, module)
            module.functions.setdefault(_norm(fn.name), fn)
        for key, module in registry.modules.items():
            for fns in module.functions.values():
                for fn in fns:
                    registry.any_by_name.setdefault(_norm(fn.name), []).append((key, fn))
                    if not fn.is_private and module.kind == "bas":
                        registry.public_by_name.setdefault(_norm(fn.name), []).append((key, fn))

    counts: Dict[str, int] = {}

    def record(status: str) -> None:
        counts[status] = counts.get(status, 0) + 1

    for call in calls:
        receiver, member = _split_callee(call)
        caller_module, caller_proc = _parse_caller(call)
        callee_key = _norm(member)
        worker_resolved = bool(call.callee_id)

        if not member:
            call.resolution_status = call.resolution_status or "unresolved"
            record(call.resolution_status)
            continue

        # --- dictionary (default-member) calls: `rs!Field` (plan 3.4) -----
        # never dropped, never CALLS-tier: receiver late-bound → late_bound,
        # otherwise external (collection/recordset accessors)
        if call.call_type == "dictionary_call" or "!" in str(call.callee_name or ""):
            call.callee_id = None
            if receiver:
                variable = registry.find_late_bound_variable(receiver, caller_module, caller_proc)
                if variable is not None and variable.late_bound:
                    call.resolution_status = "late_bound"
                else:
                    call.resolution_status = "external"
            else:
                call.resolution_status = "external"
            record(call.resolution_status)
            continue

        # --- With-block members (".ProcessOrder"): trust the ASG binding ---
        if str(call.callee_name or "").startswith(".") and worker_resolved:
            call.resolution_status = "asg_resolved"
            record(call.resolution_status)
            continue

        # --- receiver-based calls ---
        if receiver:
            receiver_key = _norm(receiver)
            if receiver_key == "me":
                me_candidates = registry.qualified_candidates(caller_module, member)
                target = me_candidates[0] if me_candidates else None
                if target is not None:
                    call.callee_id = call.callee_id or target.symbol_id
                    call.resolution_status = "asg_resolved"
                else:
                    call.resolution_status = "external"  # intrinsic form method
                record(call.resolution_status)
                continue

            module = registry.modules.get(receiver_key)
            if module is not None:
                candidates = module.functions_named(member)
                target = candidates[0] if candidates else None
                if target is not None:
                    same_module = receiver_key == _norm(caller_module)
                    if target.is_private and not same_module:
                        call.resolution_status = "unresolved"
                    else:
                        call.callee_id = call.callee_id or target.symbol_id
                        call.resolution_status = "asg_resolved"
                elif (module.kind in {"frm", "ctl", "pag"} or module.predeclared) and (
                    callee_key in _FORM_INTRINSIC_MEMBERS
                ):
                    # form instance intrinsics (Show/Hide/Refresh/...) have no
                    # project member and never resolve (review fix F4: a
                    # missing NON-intrinsic member stays unresolved)
                    call.resolution_status = "external"
                else:
                    call.resolution_status = "unresolved"
                record(call.resolution_status)
                continue

            if receiver_key in _INTRINSIC_OBJECTS:
                call.resolution_status = "external"
                record(call.resolution_status)
                continue

            # typed receiver: interface dispatch / class-typed member call
            # (plan 260917-1628 3.1) — runs BEFORE the worker-binding keep so
            # an arbitrary ASG pick against the interface module cannot mask
            # the implementer set
            variable = registry.find_late_bound_variable(receiver, caller_module, caller_proc)
            if variable is not None and not variable.late_bound:
                implementer_hits = registry.interface_member_candidates(
                    variable.type_key, member
                )
                if implementer_hits:
                    unique_ids = {fn.symbol_id for _key, fn in implementer_hits}
                    if len(unique_ids) == 1:
                        target = implementer_hits[0][1]
                        if target.is_private:
                            call.resolution_status = "unresolved"
                        else:
                            call.callee_id = next(iter(unique_ids))
                            call.resolution_status = "name_resolved"
                    else:
                        # genuinely ambiguous dispatch: POSSIBLE_CALLS keeps
                        # every implementer candidate
                        call.callee_id = None
                        call.candidate_ids = sorted(unique_ids)
                        call.resolution_status = "ambiguous"
                    record(call.resolution_status)
                    continue
                type_module = registry.modules.get(variable.type_key)
                if type_module is not None:
                    candidates = type_module.functions_named(member)
                    unique = {fn.symbol_id for fn in candidates}
                    if len(unique) == 1:
                        target = candidates[0]
                        if target.is_private:
                            call.resolution_status = "unresolved"
                        else:
                            call.callee_id = call.callee_id or next(iter(unique))
                            call.resolution_status = "asg_resolved"
                        record(call.resolution_status)
                        continue
                    if len(unique) > 1:
                        # property Get+Let pair: the ASG binding knows which
                        # kind the call site used — keep it when it points at
                        # one of them, else mark ambiguous
                        if worker_resolved and call.callee_id in unique:
                            call.resolution_status = "asg_resolved"
                        else:
                            call.callee_id = None
                            call.candidate_ids = sorted(unique)
                            call.resolution_status = "ambiguous"
                        record(call.resolution_status)
                        continue

            # bare late-bound/untyped receiver member call
            if worker_resolved:
                call.resolution_status = "asg_resolved"
                record(call.resolution_status)
                continue

            if variable is not None and variable.late_bound:
                call.resolution_status = "late_bound"
            else:
                call.resolution_status = "unresolved"
            record(call.resolution_status)
            continue

        # --- unqualified calls: ALWAYS re-derive so an arbitrary ASG pick
        #     (ProLeap resolves ambiguous names to one target) cannot publish
        #     a wrong CALLS edge (fixture case TestSameName) ---
        caller = registry.modules.get(_norm(caller_module))
        locals_ = caller.functions_named(member) if caller else []
        if locals_:
            local_ids = {fn.symbol_id for fn in locals_}
            if worker_resolved and call.callee_id in local_ids:
                # the ASG already bound a module-local target — and it knows
                # the property kind (Get vs Let) that a name-only lookup
                # cannot distinguish; keep it
                call.resolution_status = "asg_resolved"
                record(call.resolution_status)
                continue
            if len(locals_) == 1:
                call.callee_id = locals_[0].symbol_id
                call.resolution_status = "name_resolved"
                record(call.resolution_status)
                continue
            # several same-name locals (Property Get + Let): prefer the
            # worker binding when it points at one of them, else arity filter
            if worker_resolved:
                call.resolution_status = "asg_resolved"
                record(call.resolution_status)
                continue
            if call.callee_arity is not None:
                # exact arity first, range fallback (review fix F2: a range
                # match must never beat an exact signature when both exist)
                by_arity = [fn for fn in locals_ if fn.arity == call.callee_arity]
                if not by_arity:
                    by_arity = [fn for fn in locals_ if _arity_accepts(fn, call.callee_arity)]
                if len(by_arity) == 1:
                    call.callee_id = by_arity[0].symbol_id
                    call.resolution_status = "name_resolved"
                    record(call.resolution_status)
                    continue
            call.callee_id = None
            call.candidate_ids = list(local_ids)
            call.resolution_status = "ambiguous"
            record(call.resolution_status)
            continue

        public_candidates = registry.public_by_name.get(callee_key) or []
        any_candidates = registry.any_by_name.get(callee_key) or []
        if call.callee_arity is not None:
            # exact signature preferred, range fallback (review fix F2);
            # narrowing runs BEFORE concluding ambiguous (plan 3.2)
            exact_hits = [
                (key, fn) for key, fn in public_candidates
                if fn.arity == call.callee_arity
            ]
            if exact_hits:
                public_candidates = exact_hits
            else:
                range_hits = [
                    (key, fn) for key, fn in public_candidates
                    if _arity_accepts(fn, call.callee_arity)
                ]
                if range_hits:
                    public_candidates = range_hits

        if len(public_candidates) == 1:
            key, fn = public_candidates[0]
            call.callee_id = fn.symbol_id
            call.resolution_status = "name_resolved"
            record(call.resolution_status)
            continue
        if len(any_candidates) > 1 or len(public_candidates) > 1:
            # ambiguous: the worker may have bound ONE target arbitrarily
            # (ProLeap picks the first match) — drop it so publication cannot
            # turn an ambiguous name into a wrong CALLS edge
            call.callee_id = None
            call.candidate_ids = [fn.symbol_id for _key, fn in any_candidates]
            call.resolution_status = "ambiguous"
            record(call.resolution_status)
            continue
        if callee_key in registry.api_names:
            # declared Windows API (declares plane, ANTLR engine): external,
            # never unresolved (review fix F1)
            call.resolution_status = "external"
            record(call.resolution_status)
            continue
        if callee_key in _BUILTIN_FUNCTIONS:
            call.resolution_status = "external"
            record(call.resolution_status)
            continue
        call.resolution_status = "unresolved"
        record(call.resolution_status)

    if verbose:
        print(f"[vb6][resolver] {dict(sorted(counts.items()))}", flush=True)
    if payloads:
        resolve_anchor_planes(payloads, registry, verbose=verbose)
    return counts
