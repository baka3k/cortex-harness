# MCP Capability Acceptance Matrix

Unified MCP uses one shared graph query engine for most languages. The parser
profile controls labels, relationships, and support claims; it is not a separate
query backend. Public responses therefore report `query_engine=graph_generic`
instead of the internal compatibility key `cplus`.

Support levels are independent across four dimensions:

- `full`: dedicated facts and query contract are available.
- `partial`: useful facts exist, but coverage is framework- or syntax-dependent.
- `generic`: shared symbol/call queries only; language-specific precision is not claimed.
- `none`: the profile must not advertise the capability.

| Canonical profile | Symbols | Calls | Endpoints | Database | Acceptance evidence |
|---|---:|---:|---:|---:|---|
| android | full | full | none | none | analyzer entrypoint regression |
| cplus | full | full | none | none | C/C++ resource fixture |
| python | full | partial | partial | none | FastAPI + Django source fixtures |
| javascript | full | partial | partial | none | Express JS source fixture |
| typescript | full | full | none | none | primary analyzer regression; no endpoint claim |
| php | full | partial | partial | none | Laravel source fixture |
| csharp | full | full | none | none | C# + ASP.NET fixture suite |
| sql | full | none | none | full | SQL schema/procedure fixture |
| plsql | full | none | none | full | PL/SQL package-body fixture |
| jvm | generic | generic | none | none | Java/Kotlin framework fixtures |
| go | generic | generic | none | none | primary analyzer regression |
| perl | generic | generic | none | none | Perl parser fixtures |
| rust | generic | generic | none | none | primary analyzer regression |
| swift | generic | generic | none | none | primary analyzer regression |
| delphi | generic | generic | none | none | analyzer entrypoint regression |
| vbnet | generic | generic | none | none | analyzer entrypoint regression |
| visual_basic | generic | generic | none | none | VB6/VBA/VBScript entrypoint regression |
| cobol | full | full | none | none | COBOL source fixture suite |
| spring | full | full | full | full | Spring framework fixtures |
| servlet_jsp | full | full | full | none | Servlet/JSP fixtures |
| mybatis | full | full | none | full | MyBatis fixtures |
| struts | full | full | full | full | Struts fixtures |
| flutter | full | full | none | none | Dart/Flutter fixtures |
| aspnet_framework | full | full | full | full | ASP.NET Framework fixtures |
| aspnet_core | full | full | full | full | ASP.NET Core fixtures |

The executable contract is in `tests/test_mcp_acceptance_matrix.py`. The web and
database rows parse checked-in source fixtures and assert emitted graph node and
relationship rows. Other rows link to their existing parser/framework regression
suites; a route alias alone is never accepted as evidence for the newly added
semantic capabilities.

Endpoint query tools additionally inspect the live graph schema. If required
labels such as `ApiEndpoint` or relationships such as `HANDLES` are absent (or
schema inspection is unavailable), the tool returns `capability_unavailable`
instead of a misleading empty result.
