"""TypeScript / Node.js Project Type Detector

Public API
──────────
    from tools.ts.ts_project_detector import detect_project_type, ProjectTypeResult

    result = detect_project_type("/path/to/project")
    # result.project_type  →  "frontend" | "backend" | "fullstack" | "unknown"
    # result.framework     →  "react" | "next" | "nestjs" | "express" | … | ""
    # result.backend_score, result.frontend_score  → numeric confidence
    # result.signals       →  list of human-readable detection reasons

Intended usage from hyper-agent scan pipeline:
    Call ``detect_project_type(root)`` BEFORE choosing which analyzer to invoke:
    - "backend" / "fullstack" (backend-dominant) → ts_backend_analyzer.py
    - "frontend"                                  → ts_analyzer.py
    - "unknown"                                   → try both (ts_analyzer.py first)
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


# ─────────────────────────────────────────────────────────────────────────────
# Score tables
# ─────────────────────────────────────────────────────────────────────────────

# (package_name, weight, framework_hint, is_backend)
_PACKAGE_SIGNALS: List[tuple] = [
    # Backend — frameworks
    ("express",                    4, "express",  True),
    ("fastify",                    4, "fastify",  True),
    ("koa",                        4, "koa",      True),
    ("hapi",                       4, "hapi",     True),
    ("@hapi/hapi",                 4, "hapi",     True),
    ("@nestjs/core",               5, "nestjs",   True),
    ("@nestjs/common",             5, "nestjs",   True),
    ("@nestjs/platform-express",   4, "nestjs",   True),
    ("restify",                    3, "restify",  True),
    ("polka",                      3, "polka",    True),
    # Backend — data access
    ("prisma",                     3, "",         True),
    ("@prisma/client",             3, "",         True),
    ("typeorm",                    3, "",         True),
    ("sequelize",                  3, "",         True),
    ("mongoose",                   3, "",         True),
    ("drizzle-orm",                3, "",         True),
    ("knex",                       2, "",         True),
    ("kysely",                     2, "",         True),
    ("pg",                         2, "",         True),
    ("mysql2",                     2, "",         True),
    ("mongodb",                    2, "",         True),
    ("ioredis",                    2, "",         True),
    ("redis",                      2, "",         True),
    ("neo4j-driver",               2, "",         True),
    # Backend — API layers
    ("graphql",                    2, "graphql",  True),
    ("apollo-server",              3, "apollo",   True),
    ("@apollo/server",             3, "apollo",   True),
    ("@trpc/server",               4, "trpc",     True),
    # Frontend — frameworks
    ("react",                      4, "react",    False),
    ("react-dom",                  3, "react",    False),
    ("vue",                        4, "vue",      False),
    ("@vue/core",                  4, "vue",      False),
    ("svelte",                     4, "svelte",   False),
    ("@sveltejs/kit",              4, "sveltekit", False),
    ("solid-js",                   4, "solid",    False),
    ("@angular/core",              5, "angular",  False),
    ("next",                       4, "next",     False),
    ("nuxt",                       4, "nuxt",     False),
    ("@remix-run/react",           4, "remix",    False),
    ("gatsby",                     4, "gatsby",   False),
    # Frontend — tooling (weaker signals)
    ("vite",                       2, "",         False),
    ("@vitejs/plugin-react",       2, "",         False),
    ("react-native",               5, "react-native", False),
    ("expo",                       4, "expo",     False),
    ("@react-navigation/native",   3, "react-native", False),
]

# Directory name segments that skew backend (weight, is_backend)
_DIR_SIGNALS: List[tuple] = [
    ("controllers",   2, True),
    ("controller",    2, True),
    ("services",      1, True),
    ("service",       1, True),
    ("repositories",  2, True),
    ("repository",    2, True),
    ("middleware",    1, True),
    ("middlewares",   1, True),
    ("guards",        2, True),
    ("guard",         1, True),
    ("interceptors",  2, True),
    ("interceptor",   1, True),
    ("routes",        1, True),
    ("routers",       1, True),
    ("dto",           2, True),
    ("dtos",          2, True),
    ("entities",      2, True),
    ("entity",        2, True),
    ("migrations",    2, True),
    ("modules",       1, True),
    ("components",    1, False),
    ("pages",         1, False),
    ("screens",       2, False),
    ("views",         1, False),
    ("hooks",         1, False),
    ("assets",        1, False),
    ("styles",        1, False),
    ("layouts",       1, False),
]

_SKIP_DIRS: Set[str] = {
    ".git", ".hg", ".svn", "node_modules",
    "dist", "build", "out", ".next", ".nuxt",
    ".cache", "__pycache__", "coverage",
}


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProjectTypeResult:
    project_type: str               # "frontend"|"backend"|"fullstack"|"unknown"
    framework: str                  # primary detected framework
    backend_score: int
    frontend_score: int
    signals: List[str] = field(default_factory=list)
    db_packages: List[str] = field(default_factory=list)
    recommended_analyzer: str = ""  # "ts_analyzer"|"ts_backend_analyzer"|"both"

    def is_backend(self) -> bool:
        return self.project_type in {"backend", "fullstack"}

    def is_frontend(self) -> bool:
        return self.project_type in {"frontend", "fullstack"}


# ─────────────────────────────────────────────────────────────────────────────
# Detection logic
# ─────────────────────────────────────────────────────────────────────────────

def detect_project_type(root: str) -> ProjectTypeResult:
    """Analyse a Node.js/TypeScript project root and return a ``ProjectTypeResult``.

    The detector runs three passes in descending confidence order:

    1. ``package.json`` dependency scoring (highest confidence).
    2. Source directory structure scoring.
    3. Entry-point file content signals.

    Returns a ``ProjectTypeResult`` with all collected evidence included.
    """
    backend_score = 0
    frontend_score = 0
    signals: List[str] = []
    framework_votes: Dict[str, int] = {}
    db_packages: List[str] = []

    # ── Pass 1: package.json ─────────────────────────────────────────────────
    pkg_path = os.path.join(root, "package.json")
    if os.path.isfile(pkg_path):
        try:
            with open(pkg_path, "r", encoding="utf-8") as fh:
                pkg = json.load(fh)
            all_deps: Set[str] = set()
            all_deps.update((pkg.get("dependencies") or {}).keys())
            all_deps.update((pkg.get("devDependencies") or {}).keys())

            for pkg_name, weight, fw_hint, is_be in _PACKAGE_SIGNALS:
                if pkg_name in all_deps:
                    if is_be:
                        backend_score += weight
                        signals.append(f"dep:{pkg_name} (+{weight} backend)")
                        if fw_hint:
                            framework_votes[fw_hint] = framework_votes.get(fw_hint, 0) + weight
                    else:
                        frontend_score += weight
                        signals.append(f"dep:{pkg_name} (+{weight} frontend)")
                        if fw_hint:
                            framework_votes[fw_hint] = framework_votes.get(fw_hint, 0) + weight

            # DB detection
            _DB_PACKAGES = {
                "prisma": "Prisma", "@prisma/client": "Prisma",
                "typeorm": "TypeORM", "sequelize": "Sequelize",
                "mongoose": "MongoDB/Mongoose", "mongodb": "MongoDB",
                "pg": "PostgreSQL", "mysql2": "MySQL",
                "redis": "Redis", "ioredis": "Redis",
                "drizzle-orm": "Drizzle", "knex": "Knex",
                "neo4j-driver": "Neo4j",
            }
            for pkg_name, label in _DB_PACKAGES.items():
                if pkg_name in all_deps and label not in db_packages:
                    db_packages.append(label)

            # Script analysis
            scripts: Dict[str, str] = pkg.get("scripts") or {}
            for _k, script_val in scripts.items():
                if re.search(r'\b(?:nest|express|fastify)\b', script_val, re.IGNORECASE):
                    backend_score += 2
                    signals.append(f"script:{script_val[:40]} (+2 backend)")
                if re.search(r'\b(?:vite|react-scripts|next\s+dev)\b', script_val, re.IGNORECASE):
                    frontend_score += 2
                    signals.append(f"script:{script_val[:40]} (+2 frontend)")
        except Exception:
            pass

    # ── Pass 2: directory structure ──────────────────────────────────────────
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for d in dirnames:
            dl = d.lower()
            for seg_name, weight, is_be in _DIR_SIGNALS:
                if dl == seg_name:
                    if is_be:
                        backend_score += weight
                        signals.append(f"dir:{d} (+{weight} backend)")
                    else:
                        frontend_score += weight
                        signals.append(f"dir:{d} (+{weight} frontend)")

    # ── Pass 3: entry-point file content ─────────────────────────────────────
    _entry_candidates = [
        "main.ts", "index.ts", "server.ts", "app.ts",
        "src/main.ts", "src/index.ts", "src/server.ts", "src/app.ts",
    ]
    for candidate in _entry_candidates:
        ep = os.path.join(root, candidate)
        if not os.path.isfile(ep):
            continue
        try:
            with open(ep, "r", encoding="utf-8") as fh:
                content = fh.read(8192)
            if re.search(r'NestFactory\.create|createNestApplication', content):
                backend_score += 5
                framework_votes["nestjs"] = framework_votes.get("nestjs", 0) + 5
                signals.append(f"entry:{candidate} NestFactory (+5 backend)")
            if re.search(r'express\s*\(\)|new\s+Fastify', content):
                backend_score += 4
                signals.append(f"entry:{candidate} express()/Fastify (+4 backend)")
            if re.search(r'ReactDOM\.render|createRoot\s*\(', content):
                frontend_score += 5
                framework_votes["react"] = framework_votes.get("react", 0) + 5
                signals.append(f"entry:{candidate} ReactDOM (+5 frontend)")
            if re.search(r'createApp\s*\(|createSSRApp\s*\(', content):
                frontend_score += 5
                framework_votes["vue"] = framework_votes.get("vue", 0) + 5
                signals.append(f"entry:{candidate} Vue createApp (+5 frontend)")
        except Exception:
            pass

    # ── Decision ─────────────────────────────────────────────────────────────
    if backend_score == 0 and frontend_score == 0:
        project_type = "unknown"
        recommended = "ts_analyzer"
    elif backend_score > 0 and frontend_score > 0:
        if backend_score >= frontend_score * 1.5:
            project_type = "backend"
            recommended = "ts_backend_analyzer"
        elif frontend_score >= backend_score * 1.5:
            project_type = "frontend"
            recommended = "ts_analyzer"
        else:
            project_type = "fullstack"
            recommended = "both"
    elif backend_score > 0:
        project_type = "backend"
        recommended = "ts_backend_analyzer"
    else:
        project_type = "frontend"
        recommended = "ts_analyzer"

    # Pick primary framework (highest vote)
    primary_framework = ""
    if framework_votes:
        primary_framework = max(framework_votes, key=lambda k: framework_votes[k])

    return ProjectTypeResult(
        project_type=project_type,
        framework=primary_framework,
        backend_score=backend_score,
        frontend_score=frontend_score,
        signals=signals,
        db_packages=db_packages,
        recommended_analyzer=recommended,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI (useful for quick inspection)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    root_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    result = detect_project_type(root_dir)
    print(f"Project type  : {result.project_type}")
    print(f"Framework     : {result.framework or '(none detected)'}")
    print(f"Backend score : {result.backend_score}")
    print(f"Frontend score: {result.frontend_score}")
    print(f"DB packages   : {', '.join(result.db_packages) or '(none)'}")
    print(f"Recommended   : {result.recommended_analyzer}")
    print("\nSignals:")
    for s in result.signals:
        print(f"  {s}")
