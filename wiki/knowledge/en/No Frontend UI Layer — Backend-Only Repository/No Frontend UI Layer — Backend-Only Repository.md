---
kind: frontend_style
name: No Frontend UI Layer — Backend-Only Repository
category: frontend_style
scope:
    - '**'
---

This repository is a backend-only orchestration and analysis tool (Cortex Harness) with no frontend user interface. There are no CSS, SCSS, LESS, Tailwind config, HTML templates, or component-library files anywhere in the codebase. The `frontend/` directory exists at the root but contains only placeholder `CLAUDE.md` stubs under `src/config`, `src/context`, `src/features/Tarot/components`, `src/features/TuVi/components`, and `src/hooks` — none of which contain any styling code. All styling-related references found in the repo are inside analyzer logic that *detects* frontend frameworks (MUI `makeStyles`/`createTheme`, Mantine theme patterns) in *other projects* being analyzed by the harness; they are not part of this project's own UI. Consequently, there is no frontend style system, design tokens, theme configuration, or responsive strategy to document for this repository.