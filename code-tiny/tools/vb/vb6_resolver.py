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

_CALLER_ID_RE = re.compile(r"^(?P<module>[^./]+)\.(?P<proc>[^/]+)/(?P<arity>\d+)@")


def _norm(name: str) -> str:
    return (name or "").strip().lstrip(".").lower()


@dataclass
class VariableInfo:
    name: str
    type_name: str
    module_name: str
    procedure_name: Optional[str]

    @property
    def late_bound(self) -> bool:
        return _norm(self.type_name) in {"object", "variant", ""}


@dataclass
class ModuleInfo:
    module_name: str
    file_path: str
    kind: str  # bas | cls | frm | ctl | pag
    # name -> ALL overloads (a VB6 property legitimately exists as both
    # Property Get and Property Let with the same name)
    functions: Dict[str, List[FunctionDef]] = field(default_factory=dict)
    implements: List[str] = field(default_factory=list)

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
            module = ModuleInfo(
                module_name=module_name,
                file_path=file_path,
                kind=kind,
            )
            for fn in payload.get("functions", []):
                module.functions.setdefault(_norm(fn.name), []).append(fn)
            module.implements = list(_meta_field(payload, "implements") or [])
            registry.modules.setdefault(_norm(module_name), module)
            registry.module_by_file.setdefault(file_path, module)

            for var in payload.get("variables", []):
                def _var_field(key: str) -> Any:
                    if isinstance(var, dict):
                        return var.get(key, "")
                    return getattr(var, key, "")

                registry.variables.append(
                    VariableInfo(
                        name=str(_var_field("name") or ""),
                        type_name=str(_var_field("type_name") or ""),
                        module_name=str(_var_field("module_name") or module_name),
                        procedure_name=(str(_var_field("procedure_name") or "") or None),
                    )
                )

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


def _split_callee(call: CallEdge) -> Tuple[str, str]:
    """Return (receiver_or_empty, member) for a callee name."""

    member = str(call.callee_member or "").strip()
    name = str(call.callee_name or "").strip()
    if member:
        if name.lower().endswith("." + member.lower()) or name.lower().endswith(member.lower()):
            receiver = name[: len(name) - len(member)]
            receiver = receiver.rstrip(".")
            return receiver, member
        return "", member
    if not name:
        return "", ""
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
                elif module.kind in {"frm", "ctl", "pag"}:
                    call.resolution_status = "external"  # form intrinsic (Show/Refresh/...)
                else:
                    call.resolution_status = "unresolved"
                record(call.resolution_status)
                continue

            if receiver_key in _INTRINSIC_OBJECTS:
                call.resolution_status = "external"
                record(call.resolution_status)
                continue

            # typed-receiver member call already bound by the ASG
            # (ord.Total, ship.Ship_Order): keep the worker binding
            if worker_resolved:
                call.resolution_status = "asg_resolved"
                record(call.resolution_status)
                continue

            variable = registry.find_late_bound_variable(receiver, caller_module, caller_proc)
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
                by_arity = [fn for fn in locals_ if fn.arity == call.callee_arity]
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
            arity_hits = [
                (key, fn) for key, fn in public_candidates
                if fn.arity == call.callee_arity
            ]
            if len(arity_hits) == 1:
                public_candidates = arity_hits
            elif len(arity_hits) > 1:
                public_candidates = arity_hits

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
        if callee_key in _BUILTIN_FUNCTIONS:
            call.resolution_status = "external"
            record(call.resolution_status)
            continue
        call.resolution_status = "unresolved"
        record(call.resolution_status)

    if verbose:
        print(f"[vb6][resolver] {dict(sorted(counts.items()))}", flush=True)
    return counts
