# Vendored: proleap-vb6-parser

- Upstream: https://github.com/uwol/proleap-vb6-parser
- Pinned commit: `53e7b5c5754d108225be5a925026d3608c6abe75` (branch `main`, pom
  version `3.0.0`; there is no `v3.0.0` git tag — tags stop at v2.2.0)
- Vendored on: 2026-09-17
- License: MIT (see `LICENSE` in this directory)
- Scope: `pom.xml`, `LICENSE`, and `src/main` only. `src/test` (181 test
  `.cls` fixtures + unit tests) and `.github/` are NOT vendored to keep the
  repository lean; they are not needed to build the jar.

## Why vendored (not a dependency)

`io.github.uwol:proleap-vb6-parser` is **not published to Maven Central**
(directory 404; author confirmed in upstream issue #18 that artifacts must be
built locally via `mvn clean install`). Precedent: `roslyn_worker/` is also
built from in-repo source with `dotnet build`. This is a source copy, not a
git submodule (the repo recently removed subproject tree-sitter-swift for the
same reason).

## Build

The parent worker build (`code-tiny/tools/vb/antlr_worker/pom.xml`) builds
this module first:

```
mvn -q -f code-tiny/tools/vb/antlr_worker/pom.xml -DskipTests package
```

Upstream declares `maven.compiler.source/target=17` (unchanged); the
`vb6-antlr-worker` module additionally pins `maven.compiler.release=17` so a
JDK 26 toolchain still produces JDK-17-compatible bytecode.
