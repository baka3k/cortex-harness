"""Descriptor, primary-analyzer, and framework coverage registries."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Callable, Iterable, Mapping, Optional, Tuple

from .models import DescriptorParseOutput, DescriptorRole, ParseDepth
from .parsers import (
    parse_ant,
    parse_android_manifest,
    parse_android_resource,
    parse_cmake,
    parse_gradle_build,
    parse_gradle_settings,
    parse_identity_manifest,
    parse_make,
    parse_maven,
    parse_protobuf,
)


ParserCallable = Callable[..., DescriptorParseOutput]


@dataclass(frozen=True)
class DescriptorSpec:
    name: str
    patterns: Tuple[str, ...]
    parser: ParserCallable
    role: DescriptorRole
    parse_depth: ParseDepth
    max_bytes: int = 2 * 1024 * 1024
    secret_bearing: bool = False
    generated: bool = False

    def matches(self, path: str) -> bool:
        normalized = path.replace("\\", "/")
        basename = PurePosixPath(normalized).name
        return any(
            fnmatch.fnmatch(normalized, pattern)
            or fnmatch.fnmatch(basename, pattern)
            for pattern in self.patterns
        )

    def parse(self, *, project_id: str, path: str, text: str) -> DescriptorParseOutput:
        kwargs = {"project_id": project_id, "path": path, "text": text}
        if self.parser is parse_identity_manifest:
            kwargs.update(
                {
                    "parser": self.name,
                    "role": self.role,
                    "parse_depth": self.parse_depth,
                    "secret_bearing": self.secret_bearing,
                    "generated": self.generated,
                }
            )
        return self.parser(**kwargs)


DESCRIPTOR_SPECS: Tuple[DescriptorSpec, ...] = (
    DescriptorSpec(
        "gradle_settings",
        ("settings.gradle", "settings.gradle.kts"),
        parse_gradle_settings,
        DescriptorRole.TOPOLOGY,
        ParseDepth.TOPOLOGY,
    ),
    DescriptorSpec(
        "gradle_build",
        ("build.gradle", "build.gradle.kts"),
        parse_gradle_build,
        DescriptorRole.DEPENDENCY,
        ParseDepth.DEPENDENCY,
    ),
    DescriptorSpec(
        "maven",
        ("pom.xml",),
        parse_maven,
        DescriptorRole.DEPENDENCY,
        ParseDepth.DEPENDENCY,
    ),
    DescriptorSpec(
        "ant",
        ("build.xml",),
        parse_ant,
        DescriptorRole.TOPOLOGY,
        ParseDepth.TOPOLOGY,
    ),
    DescriptorSpec(
        "cmake",
        ("CMakeLists.txt",),
        parse_cmake,
        DescriptorRole.TOPOLOGY,
        ParseDepth.DEPENDENCY,
    ),
    DescriptorSpec(
        "make",
        ("Makefile", "makefile", "GNUmakefile"),
        parse_make,
        DescriptorRole.TOPOLOGY,
        ParseDepth.DEPENDENCY,
    ),
    DescriptorSpec(
        "protobuf",
        ("*.proto",),
        parse_protobuf,
        DescriptorRole.INTERFACE,
        ParseDepth.SEMANTIC,
    ),
    DescriptorSpec(
        "android_manifest",
        ("AndroidManifest.xml",),
        parse_android_manifest,
        DescriptorRole.FRAMEWORK,
        ParseDepth.SEMANTIC,
    ),
    DescriptorSpec(
        "android_resource",
        ("*/res/*.xml", "*/res/**/*.xml"),
        parse_android_resource,
        DescriptorRole.RESOURCE,
        ParseDepth.SEMANTIC,
    ),
    DescriptorSpec(
        "go",
        ("go.mod", "go.work"),
        parse_identity_manifest,
        DescriptorRole.DEPENDENCY,
        ParseDepth.IDENTITY,
    ),
    DescriptorSpec(
        "rust",
        ("Cargo.toml",),
        parse_identity_manifest,
        DescriptorRole.DEPENDENCY,
        ParseDepth.IDENTITY,
    ),
    DescriptorSpec(
        "dart",
        ("pubspec.yaml",),
        parse_identity_manifest,
        DescriptorRole.DEPENDENCY,
        ParseDepth.IDENTITY,
    ),
    DescriptorSpec(
        "swift",
        ("Package.swift", "project.pbxproj"),
        parse_identity_manifest,
        DescriptorRole.TOPOLOGY,
        ParseDepth.IDENTITY,
    ),
    DescriptorSpec(
        "dotnet",
        ("*.csproj", "*.vbproj", "*.sln", "*.slnx"),
        parse_identity_manifest,
        DescriptorRole.TOPOLOGY,
        ParseDepth.IDENTITY,
    ),
    DescriptorSpec(
        "python",
        ("pyproject.toml", "setup.cfg", "setup.py", "Pipfile"),
        parse_identity_manifest,
        DescriptorRole.DEPENDENCY,
        ParseDepth.IDENTITY,
    ),
    DescriptorSpec(
        "javascript",
        ("package.json", "jsconfig.json", "tsconfig.json"),
        parse_identity_manifest,
        DescriptorRole.DEPENDENCY,
        ParseDepth.IDENTITY,
    ),
    DescriptorSpec(
        "php",
        ("composer.json",),
        parse_identity_manifest,
        DescriptorRole.DEPENDENCY,
        ParseDepth.IDENTITY,
    ),
    DescriptorSpec(
        "perl",
        ("cpanfile", "META.json", "META.yml", "dist.ini", "Makefile.PL", "Build.PL"),
        parse_identity_manifest,
        DescriptorRole.DEPENDENCY,
        ParseDepth.IDENTITY,
    ),
    DescriptorSpec(
        "delphi",
        ("*.dproj", "*.groupproj", "*.dpk", "*.dpr"),
        parse_identity_manifest,
        DescriptorRole.TOPOLOGY,
        ParseDepth.IDENTITY,
    ),
    DescriptorSpec(
        "visual_basic",
        ("*.vbp", "*.vbg", "*.vbw"),
        parse_identity_manifest,
        DescriptorRole.TOPOLOGY,
        ParseDepth.IDENTITY,
    ),
    DescriptorSpec(
        "database_migration",
        ("dbt_project.yml", "liquibase*.xml", "flyway*.conf"),
        parse_identity_manifest,
        DescriptorRole.DEPLOYMENT,
        ParseDepth.IDENTITY,
    ),
    DescriptorSpec(
        "framework_config",
        (
            "application*.properties",
            "application*.yml",
            "application*.yaml",
            "web.xml",
            "struts*.xml",
            "mybatis*.xml",
            "appsettings*.json",
        ),
        parse_identity_manifest,
        DescriptorRole.FRAMEWORK,
        ParseDepth.IDENTITY,
        secret_bearing=True,
    ),
    DescriptorSpec(
        "runtime_secret_keys",
        (".env", ".env.*", "secrets.json", "gradle.properties"),
        parse_identity_manifest,
        DescriptorRole.SECRET_BEARING,
        ParseDepth.IDENTITY,
        secret_bearing=True,
    ),
    DescriptorSpec(
        "generated_lock",
        (
            "*.lock",
            "package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
            "go.sum",
            "Package.resolved",
        ),
        parse_identity_manifest,
        DescriptorRole.GENERATED,
        ParseDepth.IDENTITY,
        generated=True,
        max_bytes=8 * 1024 * 1024,
    ),
)


@dataclass(frozen=True)
class CoverageEntry:
    name: str
    patterns: Tuple[str, ...]
    roles: Tuple[DescriptorRole, ...]
    parse_depth: ParseDepth
    fixture_id: str
    secret_policy: str = "redact-values"
    generated_policy: str = "lower-priority"


PRIMARY_SPECIAL_FILE_COVERAGE: Mapping[str, CoverageEntry] = MappingProxyType(
    {
        "android": CoverageEntry("android", ("AndroidManifest.xml", "settings.gradle*", "build.gradle*"), (DescriptorRole.IDENTITY, DescriptorRole.TOPOLOGY), ParseDepth.SEMANTIC, "project-topology/android"),
        "cobol": CoverageEntry("cobol", ("*.cpy", "*.jcl", "Makefile"), (DescriptorRole.INTERFACE, DescriptorRole.DEPLOYMENT), ParseDepth.IDENTITY, "parser-special-files/cobol"),
        "cplus": CoverageEntry("cplus", ("CMakeLists.txt", "Makefile", "compile_commands.json"), (DescriptorRole.TOPOLOGY, DescriptorRole.GENERATED), ParseDepth.DEPENDENCY, "project-topology/native"),
        "csharp": CoverageEntry("csharp", ("*.csproj", "*.sln", "appsettings*.json"), (DescriptorRole.TOPOLOGY, DescriptorRole.CONFIGURATION), ParseDepth.IDENTITY, "parser-special-files/dotnet"),
        "dart": CoverageEntry("dart", ("pubspec.yaml", "pubspec.lock"), (DescriptorRole.IDENTITY, DescriptorRole.DEPENDENCY), ParseDepth.IDENTITY, "parser-special-files/dart"),
        "delphi": CoverageEntry("delphi", ("*.dproj", "*.groupproj", "*.dfm"), (DescriptorRole.TOPOLOGY, DescriptorRole.RESOURCE), ParseDepth.IDENTITY, "parser-special-files/delphi"),
        "go": CoverageEntry("go", ("go.mod", "go.work", "go.sum"), (DescriptorRole.IDENTITY, DescriptorRole.DEPENDENCY), ParseDepth.IDENTITY, "parser-special-files/go"),
        "java": CoverageEntry("java", ("pom.xml", "settings.gradle*", "module-info.java"), (DescriptorRole.TOPOLOGY, DescriptorRole.INTERFACE), ParseDepth.DEPENDENCY, "project-topology/maven"),
        "js": CoverageEntry("js", ("package.json", "*lock*", "jsconfig.json"), (DescriptorRole.IDENTITY, DescriptorRole.DEPENDENCY), ParseDepth.IDENTITY, "parser-special-files/javascript"),
        "kotlin": CoverageEntry("kotlin", ("settings.gradle*", "build.gradle*", "pom.xml"), (DescriptorRole.TOPOLOGY, DescriptorRole.DEPENDENCY), ParseDepth.DEPENDENCY, "project-topology/gradle"),
        "php": CoverageEntry("php", ("composer.json", "composer.lock"), (DescriptorRole.IDENTITY, DescriptorRole.DEPENDENCY), ParseDepth.IDENTITY, "parser-special-files/php"),
        "perl": CoverageEntry("perl", ("cpanfile", "META.*", "dist.ini"), (DescriptorRole.IDENTITY, DescriptorRole.DEPENDENCY), ParseDepth.IDENTITY, "parser-special-files/perl"),
        "plsql": CoverageEntry("plsql", ("*.sql", "liquibase*.xml", "flyway*.conf"), (DescriptorRole.INTERFACE, DescriptorRole.DEPLOYMENT), ParseDepth.IDENTITY, "parser-special-files/plsql"),
        "python": CoverageEntry("python", ("pyproject.toml", "requirements*.txt", "*.lock"), (DescriptorRole.IDENTITY, DescriptorRole.DEPENDENCY), ParseDepth.IDENTITY, "parser-special-files/python"),
        "rust": CoverageEntry("rust", ("Cargo.toml", "Cargo.lock"), (DescriptorRole.IDENTITY, DescriptorRole.DEPENDENCY), ParseDepth.IDENTITY, "parser-special-files/rust"),
        "sql": CoverageEntry("sql", ("*.sql", "dbt_project.yml", "liquibase*.xml"), (DescriptorRole.INTERFACE, DescriptorRole.DEPLOYMENT), ParseDepth.IDENTITY, "parser-special-files/sql"),
        "swift": CoverageEntry("swift", ("Package.swift", "project.pbxproj", "Info.plist"), (DescriptorRole.TOPOLOGY, DescriptorRole.RESOURCE), ParseDepth.IDENTITY, "parser-special-files/swift"),
        "ts": CoverageEntry("ts", ("package.json", "tsconfig.json", "*lock*"), (DescriptorRole.IDENTITY, DescriptorRole.TOPOLOGY), ParseDepth.IDENTITY, "parser-special-files/typescript"),
        "vb6": CoverageEntry("vb6", ("*.vbp", "*.vbg", "*.frm"), (DescriptorRole.TOPOLOGY, DescriptorRole.RESOURCE), ParseDepth.IDENTITY, "parser-special-files/vb6"),
        "vba": CoverageEntry("vba", ("*.bas", "*.cls", "*.frm"), (DescriptorRole.INTERFACE, DescriptorRole.RESOURCE), ParseDepth.IDENTITY, "parser-special-files/vba"),
        "vbnet": CoverageEntry("vbnet", ("*.vbproj", "*.sln", "app.config"), (DescriptorRole.TOPOLOGY, DescriptorRole.CONFIGURATION), ParseDepth.IDENTITY, "parser-special-files/vbnet"),
        "vbscript": CoverageEntry("vbscript", ("*.vbs", "*.wsf", "web.config"), (DescriptorRole.IDENTITY, DescriptorRole.DEPLOYMENT), ParseDepth.IDENTITY, "parser-special-files/vbscript"),
    }
)


FRAMEWORK_CONTEXT_COVERAGE: Mapping[str, CoverageEntry] = MappingProxyType(
    {
        "aspnet_core": CoverageEntry("aspnet_core", ("*.csproj", "appsettings*.json", "Program.cs"), (DescriptorRole.FRAMEWORK, DescriptorRole.CONFIGURATION), ParseDepth.IDENTITY, "framework-context/aspnet-core"),
        "aspnet_framework": CoverageEntry("aspnet_framework", ("*.csproj", "web.config", "Global.asax"), (DescriptorRole.FRAMEWORK, DescriptorRole.CONFIGURATION), ParseDepth.IDENTITY, "framework-context/aspnet-framework"),
        "flutter": CoverageEntry("flutter", ("pubspec.yaml", "l10n.yaml"), (DescriptorRole.FRAMEWORK, DescriptorRole.RESOURCE), ParseDepth.IDENTITY, "framework-context/flutter"),
        "mybatis": CoverageEntry("mybatis", ("mybatis*.xml", "*Mapper.xml"), (DescriptorRole.FRAMEWORK, DescriptorRole.CONFIGURATION), ParseDepth.SEMANTIC, "framework-context/mybatis"),
        "servlet_jsp": CoverageEntry("servlet_jsp", ("web.xml", "*.jsp", "*.tld"), (DescriptorRole.FRAMEWORK, DescriptorRole.RESOURCE), ParseDepth.SEMANTIC, "framework-context/servlet-jsp"),
        "spring": CoverageEntry("spring", ("application*.yml", "application*.properties", "pom.xml"), (DescriptorRole.FRAMEWORK, DescriptorRole.CONFIGURATION), ParseDepth.SEMANTIC, "framework-context/spring"),
        "struts": CoverageEntry("struts", ("struts*.xml", "*-validation.xml"), (DescriptorRole.FRAMEWORK, DescriptorRole.CONFIGURATION), ParseDepth.SEMANTIC, "framework-context/struts"),
        "fastapi_django": CoverageEntry("fastapi_django", ("manage.py", "settings.py", "urls.py"), (DescriptorRole.FRAMEWORK, DescriptorRole.CONFIGURATION), ParseDepth.IDENTITY, "framework-context/python-web"),
        "express_js": CoverageEntry("express_js", ("package.json", "routes*.js"), (DescriptorRole.FRAMEWORK, DescriptorRole.CONFIGURATION), ParseDepth.IDENTITY, "framework-context/express"),
        "laravel": CoverageEntry("laravel", ("composer.json", "artisan", "routes/*.php"), (DescriptorRole.FRAMEWORK, DescriptorRole.CONFIGURATION), ParseDepth.IDENTITY, "framework-context/laravel"),
        "database_sql": CoverageEntry("database_sql", ("*.sql", "dbt_project.yml", "liquibase*.xml"), (DescriptorRole.FRAMEWORK, DescriptorRole.DEPLOYMENT), ParseDepth.SEMANTIC, "framework-context/database-sql"),
        "database_plsql": CoverageEntry("database_plsql", ("*.sql", "flyway*.conf", "liquibase*.xml"), (DescriptorRole.FRAMEWORK, DescriptorRole.DEPLOYMENT), ParseDepth.SEMANTIC, "framework-context/database-plsql"),
    }
)


def descriptor_spec_for_path(path: str) -> Optional[DescriptorSpec]:
    return next((spec for spec in DESCRIPTOR_SPECS if spec.matches(path)), None)


def descriptor_candidates(paths: Iterable[str]) -> Tuple[str, ...]:
    return tuple(
        sorted(
            {
                path.replace("\\", "/")
                for path in paths
                if descriptor_spec_for_path(path) is not None
            }
        )
    )


__all__ = [
    "CoverageEntry",
    "DESCRIPTOR_SPECS",
    "DescriptorSpec",
    "FRAMEWORK_CONTEXT_COVERAGE",
    "PRIMARY_SPECIAL_FILE_COVERAGE",
    "descriptor_candidates",
    "descriptor_spec_for_path",
]
