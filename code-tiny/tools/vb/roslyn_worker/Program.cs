using System.Diagnostics;
using System.Text.Json;
using Microsoft.Build.Locator;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.MSBuild;
using Microsoft.CodeAnalysis.VisualBasic;
using Microsoft.CodeAnalysis.VisualBasic.Syntax;

internal sealed class Options
{
    public required string Root { get; init; }
    public string? File { get; init; }
    public string? FilesManifest { get; init; }
    public string Semantic { get; init; } = "auto";
    public int WorkspaceTimeoutMs { get; init; } = 120_000;
    public int FileTimeoutMs { get; init; } = 60_000;
    public string ParseCacheVersion { get; init; } = "vb-family-v2026-04-03-2";
}

internal sealed class WorkspaceContext
{
    public required string Kind { get; init; }
    public required string SolutionOrProjectPath { get; init; }
    public required bool SemanticEnabled { get; init; }
    public required Dictionary<string, Document> DocumentByPath { get; init; }
    public required List<string> SemanticErrors { get; init; }
}

internal static class Program
{
    private static readonly HashSet<string> CallKeywords = new(StringComparer.OrdinalIgnoreCase)
    {
        "if", "while", "for", "select", "return", "cint", "cstr", "cdbl", "ctype", "directcast", "trycast"
    };

    public static async Task<int> Main(string[] args)
    {
        try
        {
            var options = ParseArgs(args);
            if (options is null)
            {
                PrintUsage();
                return 2;
            }

            var files = LoadFiles(options).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
            if (files.Count == 0)
            {
                await WriteJsonAsync(new Dictionary<string, object?>
                {
                    ["workspace_kind"] = "none",
                    ["solution_or_project_path"] = "",
                    ["semantic_enabled"] = false,
                    ["results"] = Array.Empty<object>(),
                });
                return 0;
            }

            var workspace = await BuildWorkspaceContextAsync(options, files);
            var results = new List<Dictionary<string, object?>>();

            foreach (var file in files)
            {
                try
                {
                    using var cts = new CancellationTokenSource(options.FileTimeoutMs);
                    var payload = await ParseFileAsync(file, options, workspace, cts.Token);
                    results.Add(new Dictionary<string, object?>
                    {
                        ["file_path"] = ToRelPath(options.Root, file),
                        ["ok"] = true,
                        ["payload"] = payload,
                        ["error"] = "",
                    });
                }
                catch (OperationCanceledException)
                {
                    results.Add(new Dictionary<string, object?>
                    {
                        ["file_path"] = ToRelPath(options.Root, file),
                        ["ok"] = false,
                        ["payload"] = null,
                        ["error"] = $"timeout after {options.FileTimeoutMs}ms",
                    });
                }
                catch (Exception ex)
                {
                    results.Add(new Dictionary<string, object?>
                    {
                        ["file_path"] = ToRelPath(options.Root, file),
                        ["ok"] = false,
                        ["payload"] = null,
                        ["error"] = ex.Message,
                    });
                }
            }

            await WriteJsonAsync(new Dictionary<string, object?>
            {
                ["workspace_kind"] = workspace.Kind,
                ["solution_or_project_path"] = workspace.SolutionOrProjectPath,
                ["semantic_enabled"] = workspace.SemanticEnabled,
                ["semantic_errors"] = workspace.SemanticErrors,
                ["results"] = results,
            });
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[roslyn-worker] fatal: {ex}");
            return 1;
        }
    }

    private static Options? ParseArgs(string[] args)
    {
        var map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        for (var i = 0; i < args.Length; i++)
        {
            var token = args[i];
            if (!token.StartsWith("--", StringComparison.Ordinal))
            {
                continue;
            }

            var key = token[2..];
            if (i + 1 >= args.Length || args[i + 1].StartsWith("--", StringComparison.Ordinal))
            {
                map[key] = "true";
                continue;
            }

            map[key] = args[i + 1];
            i++;
        }

        if (!map.TryGetValue("root", out var root) || string.IsNullOrWhiteSpace(root))
        {
            return null;
        }

        map.TryGetValue("file", out var file);
        map.TryGetValue("files-manifest", out var filesManifest);
        map.TryGetValue("semantic", out var semantic);
        map.TryGetValue("workspace-timeout-ms", out var workspaceTimeoutRaw);
        map.TryGetValue("file-timeout-ms", out var fileTimeoutRaw);
        map.TryGetValue("parse-cache-version", out var cacheVersion);

        var mode = (semantic ?? "auto").Trim().ToLowerInvariant();
        if (mode is not ("off" or "auto" or "on"))
        {
            return null;
        }

        if (string.IsNullOrWhiteSpace(file) && string.IsNullOrWhiteSpace(filesManifest))
        {
            return null;
        }

        var workspaceTimeout = 120_000;
        if (!string.IsNullOrWhiteSpace(workspaceTimeoutRaw) && int.TryParse(workspaceTimeoutRaw, out var ws))
        {
            workspaceTimeout = Math.Max(5_000, ws);
        }

        var fileTimeout = 60_000;
        if (!string.IsNullOrWhiteSpace(fileTimeoutRaw) && int.TryParse(fileTimeoutRaw, out var ft))
        {
            fileTimeout = Math.Max(5_000, ft);
        }

        return new Options
        {
            Root = NormalizePath(root),
            File = string.IsNullOrWhiteSpace(file) ? null : file,
            FilesManifest = string.IsNullOrWhiteSpace(filesManifest) ? null : filesManifest,
            Semantic = mode,
            WorkspaceTimeoutMs = workspaceTimeout,
            FileTimeoutMs = fileTimeout,
            ParseCacheVersion = string.IsNullOrWhiteSpace(cacheVersion) ? "vb-family-v2026-04-03-2" : cacheVersion,
        };
    }

    private static void PrintUsage()
    {
        Console.Error.WriteLine("Usage: RoslynVbWorker --root <path> (--file <path> | --files-manifest <path>) [--semantic off|auto|on] [--workspace-timeout-ms N] [--file-timeout-ms N] [--parse-cache-version V]");
    }

    private static List<string> LoadFiles(Options options)
    {
        var files = new List<string>();
        if (!string.IsNullOrWhiteSpace(options.File))
        {
            var one = ResolvePath(options.Root, options.File!);
            if (File.Exists(one) && one.EndsWith(".vb", StringComparison.OrdinalIgnoreCase))
            {
                files.Add(one);
            }
        }

        if (!string.IsNullOrWhiteSpace(options.FilesManifest))
        {
            var manifestPath = ResolvePath(options.Root, options.FilesManifest!);
            if (File.Exists(manifestPath))
            {
                var text = File.ReadAllText(manifestPath);
                foreach (var raw in ParseManifest(text))
                {
                    var resolved = ResolvePath(options.Root, raw);
                    if (File.Exists(resolved) && resolved.EndsWith(".vb", StringComparison.OrdinalIgnoreCase))
                    {
                        files.Add(resolved);
                    }
                }
            }
        }

        return files;
    }

    private static IEnumerable<string> ParseManifest(string text)
    {
        var stripped = text.TrimStart();
        if (stripped.StartsWith("{") || stripped.StartsWith("["))
        {
            using var doc = JsonDocument.Parse(text);
            if (doc.RootElement.ValueKind == JsonValueKind.Object && doc.RootElement.TryGetProperty("files", out var filesEl) && filesEl.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in filesEl.EnumerateArray())
                {
                    if (item.ValueKind == JsonValueKind.String)
                    {
                        var value = item.GetString();
                        if (!string.IsNullOrWhiteSpace(value))
                        {
                            yield return value;
                        }
                    }
                }
                yield break;
            }

            if (doc.RootElement.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in doc.RootElement.EnumerateArray())
                {
                    if (item.ValueKind == JsonValueKind.String)
                    {
                        var value = item.GetString();
                        if (!string.IsNullOrWhiteSpace(value))
                        {
                            yield return value;
                        }
                    }
                }
                yield break;
            }
        }

        foreach (var line in text.Split('\n'))
        {
            var value = line.Trim();
            if (!string.IsNullOrWhiteSpace(value))
            {
                yield return value;
            }
        }
    }

    private static async Task<WorkspaceContext> BuildWorkspaceContextAsync(Options options, List<string> files)
    {
        var semanticErrors = new List<string>();
        if (options.Semantic == "off")
        {
            return new WorkspaceContext
            {
                Kind = "none",
                SolutionOrProjectPath = "",
                SemanticEnabled = false,
                DocumentByPath = new Dictionary<string, Document>(StringComparer.OrdinalIgnoreCase),
                SemanticErrors = semanticErrors,
            };
        }

        var (workspaceKind, workspacePath) = FindWorkspace(options.Root);
        if (options.Semantic == "auto" && workspaceKind == "none")
        {
            return new WorkspaceContext
            {
                Kind = "none",
                SolutionOrProjectPath = "",
                SemanticEnabled = false,
                DocumentByPath = new Dictionary<string, Document>(StringComparer.OrdinalIgnoreCase),
                SemanticErrors = semanticErrors,
            };
        }

        if (workspaceKind == "none")
        {
            semanticErrors.Add("semantic requested but no .sln/.vbproj found");
            return new WorkspaceContext
            {
                Kind = "none",
                SolutionOrProjectPath = "",
                SemanticEnabled = false,
                DocumentByPath = new Dictionary<string, Document>(StringComparer.OrdinalIgnoreCase),
                SemanticErrors = semanticErrors,
            };
        }

        try
        {
            if (!MSBuildLocator.IsRegistered)
            {
                MSBuildLocator.RegisterDefaults();
            }

            using var cts = new CancellationTokenSource(options.WorkspaceTimeoutMs);
            var workspace = MSBuildWorkspace.Create();
            workspace.WorkspaceFailed += (_, evt) =>
            {
                if (!string.IsNullOrWhiteSpace(evt.Diagnostic.Message))
                {
                    semanticErrors.Add(evt.Diagnostic.Message);
                }
            };

            var docs = new Dictionary<string, Document>(StringComparer.OrdinalIgnoreCase);
            if (workspaceKind == "solution")
            {
                var solution = await workspace.OpenSolutionAsync(workspacePath, cancellationToken: cts.Token);
                foreach (var project in solution.Projects)
                {
                    foreach (var doc in project.Documents)
                    {
                        if (doc.FilePath is null)
                        {
                            continue;
                        }

                        var normalized = NormalizePath(doc.FilePath);
                        if (!normalized.EndsWith(".vb", StringComparison.OrdinalIgnoreCase))
                        {
                            continue;
                        }

                        docs[normalized] = doc;
                    }
                }
            }
            else
            {
                var project = await workspace.OpenProjectAsync(workspacePath, cancellationToken: cts.Token);
                foreach (var doc in project.Documents)
                {
                    if (doc.FilePath is null)
                    {
                        continue;
                    }

                    var normalized = NormalizePath(doc.FilePath);
                    if (!normalized.EndsWith(".vb", StringComparison.OrdinalIgnoreCase))
                    {
                        continue;
                    }

                    docs[normalized] = doc;
                }
            }

            return new WorkspaceContext
            {
                Kind = workspaceKind,
                SolutionOrProjectPath = workspacePath,
                SemanticEnabled = docs.Count > 0,
                DocumentByPath = docs,
                SemanticErrors = semanticErrors,
            };
        }
        catch (Exception ex)
        {
            semanticErrors.Add(ex.Message);
            return new WorkspaceContext
            {
                Kind = workspaceKind,
                SolutionOrProjectPath = workspacePath,
                SemanticEnabled = false,
                DocumentByPath = new Dictionary<string, Document>(StringComparer.OrdinalIgnoreCase),
                SemanticErrors = semanticErrors,
            };
        }
    }

    private static (string Kind, string Path) FindWorkspace(string root)
    {
        try
        {
            var sln = Directory.EnumerateFiles(root, "*.sln", SearchOption.AllDirectories).FirstOrDefault();
            if (!string.IsNullOrWhiteSpace(sln))
            {
                return ("solution", NormalizePath(sln));
            }

            var vbproj = Directory.EnumerateFiles(root, "*.vbproj", SearchOption.AllDirectories).FirstOrDefault();
            if (!string.IsNullOrWhiteSpace(vbproj))
            {
                return ("project", NormalizePath(vbproj));
            }
        }
        catch
        {
            // ignore workspace scan issues
        }

        return ("none", "");
    }

    private static async Task<Dictionary<string, object?>> ParseFileAsync(
        string file,
        Options options,
        WorkspaceContext workspace,
        CancellationToken cancellationToken)
    {
        var sw = Stopwatch.StartNew();
        var relPath = ToRelPath(options.Root, file);
        var source = await File.ReadAllTextAsync(file, cancellationToken);
        var syntaxTree = VisualBasicSyntaxTree.ParseText(source, path: file, cancellationToken: cancellationToken);
        var rootNode = await syntaxTree.GetRootAsync(cancellationToken);

        SemanticModel? semanticModel = null;
        var semanticEnabled = false;
        if (workspace.SemanticEnabled && workspace.DocumentByPath.TryGetValue(NormalizePath(file), out var document))
        {
            try
            {
                semanticModel = await document.GetSemanticModelAsync(cancellationToken);
                var docRoot = await document.GetSyntaxRootAsync(cancellationToken);
                if (docRoot is not null)
                {
                    rootNode = docRoot;
                    syntaxTree = docRoot.SyntaxTree;
                    source = docRoot.SyntaxTree.ToString();
                }
                semanticEnabled = semanticModel is not null;
            }
            catch
            {
                semanticEnabled = false;
            }
        }

        var lineCount = source.Length == 0 ? 1 : source.Count(ch => ch == '\n') + 1;
        var diagnostics = syntaxTree.GetDiagnostics(cancellationToken);
        var hasError = diagnostics.Any(d => d.Severity == DiagnosticSeverity.Error);
        var errorNodes = diagnostics.Count(d => d.Severity == DiagnosticSeverity.Error);

        var imports = rootNode.DescendantNodes().OfType<ImportsStatementSyntax>()
            .SelectMany(stmt => stmt.ImportsClauses)
            .Select(clause => clause.ToString().Trim())
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        var namespaces = new List<Dictionary<string, object?>>();
        foreach (var ns in rootNode.DescendantNodes().OfType<NamespaceBlockSyntax>())
        {
            var qualified = GetQualifiedNamespace(ns);
            var (startLine, endLine) = GetLineSpan(ns);
            namespaces.Add(new Dictionary<string, object?>
            {
                ["symbol_id"] = $"namespace::{qualified}@{relPath}",
                ["qualified_name"] = qualified,
                ["name"] = ns.NamespaceStatement.Name.ToString(),
                ["file_path"] = relPath,
                ["start_line"] = startLine,
                ["end_line"] = endLine,
                ["code"] = ns.ToFullString(),
                ["comment"] = "",
                ["summary"] = "",
                ["note"] = BuildNote(ns.ToFullString(), "", ""),
            });
        }

        var classes = new List<Dictionary<string, object?>>();
        foreach (var typeBlock in rootNode.DescendantNodes().OfType<TypeBlockSyntax>())
        {
            var typeName = GetTypeName(typeBlock);
            if (string.IsNullOrWhiteSpace(typeName))
            {
                continue;
            }

            var namespaceName = GetEnclosingNamespace(typeBlock);
            var qualified = JoinDot(namespaceName, typeName);
            var (startLine, endLine) = GetLineSpan(typeBlock);
            classes.Add(new Dictionary<string, object?>
            {
                ["symbol_id"] = string.IsNullOrWhiteSpace(qualified) ? typeName : qualified,
                ["qualified_name"] = qualified,
                ["name"] = typeName,
                ["kind"] = GetTypeKind(typeBlock),
                ["namespace_name"] = namespaceName,
                ["file_path"] = relPath,
                ["start_line"] = startLine,
                ["end_line"] = endLine,
                ["code"] = typeBlock.ToFullString(),
                ["comment"] = "",
                ["summary"] = "",
                ["note"] = BuildNote(typeBlock.ToFullString(), "", ""),
            });
        }

        var functions = new List<Dictionary<string, object?>>();
        var calls = new List<Dictionary<string, object?>>();
        foreach (var methodBlock in rootNode.DescendantNodes().OfType<MethodBlockBaseSyntax>())
        {
            if (!TryGetMethodIdentity(methodBlock, relPath, out var name, out var kind, out var namespaceName, out var className, out var arity, out var qualifiedName, out var symbolId))
            {
                continue;
            }

            var (startLine, endLine) = GetLineSpan(methodBlock);
            functions.Add(new Dictionary<string, object?>
            {
                ["symbol_id"] = symbolId,
                ["qualified_name"] = qualifiedName,
                ["name"] = name,
                ["kind"] = kind,
                ["class_name"] = className,
                ["namespace_name"] = namespaceName,
                ["file_path"] = relPath,
                ["start_line"] = startLine,
                ["end_line"] = endLine,
                ["arity"] = arity,
                ["code"] = methodBlock.ToFullString(),
                ["comment"] = "",
                ["summary"] = "",
                ["note"] = BuildNote(methodBlock.ToFullString(), "", ""),
            });

            foreach (var invocation in methodBlock.DescendantNodes().OfType<InvocationExpressionSyntax>())
            {
                var (callLine, _) = GetLineSpan(invocation);
                var calleeName = NormalizeCallee(invocation.Expression.ToString());
                if (string.IsNullOrWhiteSpace(calleeName))
                {
                    continue;
                }

                var simple = calleeName.Split('.').LastOrDefault() ?? calleeName;
                if (CallKeywords.Contains(simple))
                {
                    continue;
                }

                string? calleeId = null;
                int? calleeArity = null;
                if (semanticModel is not null)
                {
                    var symbol = semanticModel.GetSymbolInfo(invocation, cancellationToken).Symbol
                        ?? semanticModel.GetSymbolInfo(invocation, cancellationToken).CandidateSymbols.FirstOrDefault();
                    if (symbol is not null)
                    {
                        calleeName = BuildSymbolName(symbol);
                        calleeArity = GetSymbolArity(symbol);
                        calleeId = BuildSymbolId(symbol, options.Root);
                    }
                }

                calls.Add(new Dictionary<string, object?>
                {
                    ["caller_id"] = symbolId,
                    ["caller_scope"] = GetCallerScope(qualifiedName, namespaceName),
                    ["callee_name"] = calleeName,
                    ["callee_id"] = calleeId,
                    ["callee_arity"] = calleeArity,
                    ["call_line"] = callLine,
                });
            }
        }

        var properties = new List<Dictionary<string, object?>>();
        foreach (var prop in rootNode.DescendantNodes().OfType<PropertyBlockSyntax>())
        {
            var statement = prop.PropertyStatement;
            var name = statement.Identifier.Text;
            var namespaceName = GetEnclosingNamespace(prop);
            var className = GetEnclosingType(prop);
            var qualified = JoinDot(namespaceName, className, name);
            var (startLine, endLine) = GetLineSpan(prop);
            var parameters = statement.ParameterList?.ToString().Trim() ?? "";
            var returnType = ExtractTypeName(statement.AsClause);

            properties.Add(new Dictionary<string, object?>
            {
                ["symbol_id"] = $"{qualified}@{relPath}",
                ["qualified_name"] = qualified,
                ["name"] = name,
                ["kind"] = "get",
                ["class_name"] = className,
                ["namespace_name"] = namespaceName,
                ["file_path"] = relPath,
                ["start_line"] = startLine,
                ["end_line"] = endLine,
                ["parameters"] = parameters,
                ["return_type"] = returnType,
                ["code"] = prop.ToFullString(),
                ["comment"] = "",
                ["summary"] = "",
                ["note"] = BuildNote(prop.ToFullString(), "", ""),
            });
        }

        var events = new List<Dictionary<string, object?>>();
        foreach (var evt in rootNode.DescendantNodes().OfType<EventStatementSyntax>())
        {
            var name = evt.Identifier.Text;
            var namespaceName = GetEnclosingNamespace(evt);
            var className = GetEnclosingType(evt);
            var qualified = JoinDot(namespaceName, className, name);
            var (startLine, endLine) = GetLineSpan(evt);
            var parameters = evt.ParameterList?.ToString().Trim() ?? "";

            events.Add(new Dictionary<string, object?>
            {
                ["symbol_id"] = $"{qualified}@{relPath}",
                ["qualified_name"] = qualified,
                ["name"] = name,
                ["class_name"] = className,
                ["namespace_name"] = namespaceName,
                ["file_path"] = relPath,
                ["start_line"] = startLine,
                ["end_line"] = endLine,
                ["parameters"] = parameters,
                ["code"] = evt.ToFullString(),
                ["comment"] = "",
                ["summary"] = "",
                ["note"] = BuildNote(evt.ToFullString(), "", ""),
            });
        }

        var interfaces = new List<Dictionary<string, object?>>();
        foreach (var iface in rootNode.DescendantNodes().OfType<InterfaceBlockSyntax>())
        {
            var name = iface.InterfaceStatement.Identifier.Text;
            var namespaceName = GetEnclosingNamespace(iface);
            var qualified = JoinDot(namespaceName, name);
            var (startLine, endLine) = GetLineSpan(iface);
            var baseIfaces = iface.Inherits
                .SelectMany(inh => inh.Types)
                .Select(t => t.ToString())
                .Where(v => !string.IsNullOrWhiteSpace(v))
                .ToList();

            interfaces.Add(new Dictionary<string, object?>
            {
                ["symbol_id"] = $"{qualified}@{relPath}",
                ["qualified_name"] = qualified,
                ["name"] = name,
                ["namespace_name"] = namespaceName,
                ["file_path"] = relPath,
                ["start_line"] = startLine,
                ["end_line"] = endLine,
                ["base_interfaces"] = baseIfaces,
                ["code"] = iface.ToFullString(),
                ["comment"] = "",
                ["summary"] = "",
                ["note"] = BuildNote(iface.ToFullString(), "", ""),
            });
        }

        var enums = new List<Dictionary<string, object?>>();
        foreach (var en in rootNode.DescendantNodes().OfType<EnumBlockSyntax>())
        {
            var name = en.EnumStatement.Identifier.Text;
            var namespaceName = GetEnclosingNamespace(en);
            var className = GetEnclosingType(en);
            var qualified = JoinDot(namespaceName, className, name);
            var (startLine, endLine) = GetLineSpan(en);
            var members = new List<object[]>();
            foreach (var member in en.Members.OfType<EnumMemberDeclarationSyntax>())
            {
                var memberName = member.Identifier.Text;
                var value = member.Initializer?.Value.ToString() ?? "";
                members.Add(new object[] { memberName, value });
            }

            enums.Add(new Dictionary<string, object?>
            {
                ["symbol_id"] = $"{qualified}@{relPath}",
                ["qualified_name"] = qualified,
                ["name"] = name,
                ["namespace_name"] = namespaceName,
                ["class_name"] = className,
                ["file_path"] = relPath,
                ["start_line"] = startLine,
                ["end_line"] = endLine,
                ["members"] = members,
                ["code"] = en.ToFullString(),
                ["comment"] = "",
                ["summary"] = "",
                ["note"] = BuildNote(en.ToFullString(), "", ""),
            });
        }

        var constants = new List<Dictionary<string, object?>>();
        var variables = new List<Dictionary<string, object?>>();
        foreach (var field in rootNode.DescendantNodes().OfType<FieldDeclarationSyntax>())
        {
            var isConst = field.Modifiers.Any(mod => mod.IsKind(SyntaxKind.ConstKeyword));
            var isShared = field.Modifiers.Any(mod => mod.IsKind(SyntaxKind.SharedKeyword));
            var isGlobal = field.Modifiers.Any(mod => mod.IsKind(SyntaxKind.PublicKeyword) || mod.IsKind(SyntaxKind.FriendKeyword));

            foreach (var declarator in field.Declarators)
            {
                var typeName = ExtractTypeName(declarator.AsClause);
                if (string.IsNullOrWhiteSpace(typeName))
                {
                    typeName = "Variant";
                }
                var value = declarator.Initializer?.Value.ToString() ?? "";

                foreach (var n in declarator.Names)
                {
                    var name = n.Identifier.Text;
                    var namespaceName = GetEnclosingNamespace(field);
                    var className = GetEnclosingType(field);
                    var qualified = JoinDot(namespaceName, className, name);
                    var (lineNum, _) = GetLineSpan(n);

                    if (isConst)
                    {
                        constants.Add(new Dictionary<string, object?>
                        {
                            ["symbol_id"] = $"{qualified}@{relPath}",
                            ["qualified_name"] = qualified,
                            ["name"] = name,
                            ["value"] = value,
                            ["type_name"] = typeName,
                            ["class_name"] = className,
                            ["namespace_name"] = namespaceName,
                            ["file_path"] = relPath,
                            ["line_number"] = lineNum,
                            ["code"] = field.ToFullString(),
                            ["comment"] = "",
                            ["summary"] = "",
                            ["note"] = BuildNote(field.ToFullString(), "", ""),
                        });
                    }
                    else
                    {
                        variables.Add(new Dictionary<string, object?>
                        {
                            ["symbol_id"] = $"{qualified}@{relPath}",
                            ["qualified_name"] = qualified,
                            ["name"] = name,
                            ["type_name"] = typeName,
                            ["is_global"] = isGlobal,
                            ["is_shared"] = isShared,
                            ["class_name"] = className,
                            ["namespace_name"] = namespaceName,
                            ["file_path"] = relPath,
                            ["line_number"] = lineNum,
                            ["code"] = field.ToFullString(),
                            ["comment"] = "",
                            ["summary"] = "",
                            ["note"] = BuildNote(field.ToFullString(), "", ""),
                        });
                    }
                }
            }
        }

        sw.Stop();
        var parseMeta = new Dictionary<string, object?>
        {
            ["parser_language"] = "vbnet_roslyn",
            ["parse_cache_version"] = options.ParseCacheVersion,
            ["has_error"] = hasError,
            ["error_nodes"] = errorNodes,
            ["line_count"] = lineCount,
            ["parser_engine"] = "roslyn",
            ["semantic_mode"] = options.Semantic,
            ["semantic_enabled"] = semanticEnabled,
            ["fallback_reason"] = "",
            ["worker_elapsed_ms"] = sw.ElapsedMilliseconds,
            ["workspace_kind"] = workspace.Kind,
            ["solution_or_project_path"] = workspace.SolutionOrProjectPath,
            ["semantic_errors"] = workspace.SemanticErrors,
            ["resolution_source"] = semanticEnabled ? "semantic" : "syntax",
        };

        var payload = new Dictionary<string, object?>
        {
            ["functions"] = functions,
            ["calls"] = calls,
            ["classes"] = classes,
            ["namespaces"] = namespaces,
            ["relations"] = Array.Empty<object>(),
            ["properties"] = properties,
            ["events"] = events,
            ["interfaces"] = interfaces,
            ["enums"] = enums,
            ["constants"] = constants,
            ["variables"] = variables,
            ["file_def"] = new Dictionary<string, object?>
            {
                ["file_path"] = relPath,
                ["start_line"] = 1,
                ["end_line"] = lineCount,
                ["code"] = source,
                ["comment"] = "",
                ["summary"] = "",
                ["note"] = BuildNote(source, "", ""),
                ["imports"] = imports,
                ["exports"] = Array.Empty<object>(),
            },
            ["parse_meta"] = parseMeta,
            ["parse_cache_version"] = options.ParseCacheVersion,
        };

        return payload;
    }

    private static bool TryGetMethodIdentity(
        MethodBlockBaseSyntax block,
        string relPath,
        out string name,
        out string kind,
        out string? namespaceName,
        out string? className,
        out int arity,
        out string qualifiedName,
        out string symbolId)
    {
        name = "";
        kind = "";
        namespaceName = GetEnclosingNamespace(block);
        className = GetEnclosingType(block);
        arity = 0;
        qualifiedName = "";
        symbolId = "";

        var statement = block.BlockStatement;
        switch (statement)
        {
            case MethodStatementSyntax ms:
                name = ms.Identifier.Text;
                kind = ms.IsKind(SyntaxKind.FunctionStatement) ? "function" : "sub";
                arity = ms.ParameterList?.Parameters.Count ?? 0;
                break;
            case OperatorStatementSyntax os:
                name = $"Operator{os.OperatorToken.Text}";
                kind = "operator";
                arity = os.ParameterList?.Parameters.Count ?? 0;
                break;
            case AccessorStatementSyntax:
                return false;
            default:
                return false;
        }

        qualifiedName = JoinDot(namespaceName, className, name);
        symbolId = string.IsNullOrWhiteSpace(qualifiedName)
            ? $"{name}/{arity}@{relPath}"
            : $"{qualifiedName}/{arity}@{relPath}";
        return true;
    }

    private static string BuildSymbolName(ISymbol symbol)
    {
        if (symbol is IMethodSymbol method && method.MethodKind == MethodKind.ReducedExtension)
        {
            symbol = method.ReducedFrom ?? method;
        }

        var containingType = symbol.ContainingType?.ToDisplayString();
        var containingNamespace = symbol.ContainingNamespace?.ToDisplayString();
        var baseName = symbol.Name;
        if (symbol is IMethodSymbol m && m.MethodKind == MethodKind.Constructor)
        {
            baseName = "New";
        }

        var full = JoinDot(
            string.IsNullOrWhiteSpace(containingNamespace) || containingNamespace == "<global namespace>" ? null : containingNamespace,
            containingType,
            baseName);

        if (symbol is IMethodSymbol methodSymbol)
        {
            return $"{full}/{methodSymbol.Parameters.Length}";
        }

        if (symbol.ContainingAssembly is not null && symbol.Locations.All(loc => !loc.IsInSource))
        {
            return $"{full} [{symbol.ContainingAssembly.Identity}]";
        }

        return full;
    }

    private static int? GetSymbolArity(ISymbol symbol)
    {
        return symbol is IMethodSymbol method ? method.Parameters.Length : null;
    }

    private static string? BuildSymbolId(ISymbol symbol, string root)
    {
        if (symbol is not IMethodSymbol method)
        {
            return null;
        }

        var loc = method.Locations.FirstOrDefault(l => l.IsInSource && l.SourceTree?.FilePath is not null);
        if (loc is null || loc.SourceTree?.FilePath is null)
        {
            return null;
        }

        var filePath = NormalizePath(loc.SourceTree.FilePath);
        var relPath = ToRelPath(root, filePath);
        var namespaceName = method.ContainingNamespace?.ToDisplayString();
        if (namespaceName == "<global namespace>")
        {
            namespaceName = null;
        }
        var typeName = method.ContainingType?.ToDisplayString();
        var methodName = method.MethodKind == MethodKind.Constructor ? "New" : method.Name;
        var qualified = JoinDot(namespaceName, typeName, methodName);
        if (string.IsNullOrWhiteSpace(qualified))
        {
            qualified = methodName;
        }
        return $"{qualified}/{method.Parameters.Length}@{relPath}";
    }

    private static string NormalizeCallee(string text)
    {
        var compact = string.Concat(text.Where(ch => !char.IsWhiteSpace(ch)));
        compact = compact.Replace("?.", ".", StringComparison.Ordinal);
        return compact.Trim('.');
    }

    private static string GetCallerScope(string qualifiedName, string? namespaceName)
    {
        var idx = qualifiedName.LastIndexOf('.');
        if (idx > 0)
        {
            return qualifiedName[..idx];
        }
        return namespaceName ?? "";
    }

    private static string GetTypeName(TypeBlockSyntax typeBlock)
    {
        return typeBlock.BlockStatement switch
        {
            ClassStatementSyntax cls => cls.Identifier.Text,
            ModuleStatementSyntax module => module.Identifier.Text,
            StructureStatementSyntax st => st.Identifier.Text,
            InterfaceStatementSyntax iface => iface.Identifier.Text,
            _ => "",
        };
    }

    private static string ExtractTypeName(AsClauseSyntax? asClause)
    {
        return asClause switch
        {
            SimpleAsClauseSyntax simple => simple.Type?.ToString() ?? "",
            AsNewClauseSyntax asNew => asNew.NewExpression?.Type()?.ToString() ?? "",
            _ => "",
        };
    }

    private static string GetTypeKind(TypeBlockSyntax typeBlock)
    {
        return typeBlock.BlockStatement.Kind() switch
        {
            SyntaxKind.ClassStatement => "class",
            SyntaxKind.ModuleStatement => "module",
            SyntaxKind.StructureStatement => "structure",
            SyntaxKind.InterfaceStatement => "interface",
            SyntaxKind.EnumStatement => "enum",
            _ => "class",
        };
    }

    private static string? GetEnclosingNamespace(SyntaxNode node)
    {
        var ns = node.Ancestors().OfType<NamespaceBlockSyntax>().Reverse().Select(n => n.NamespaceStatement.Name.ToString()).ToList();
        if (ns.Count == 0)
        {
            return null;
        }
        return string.Join('.', ns);
    }

    private static string? GetQualifiedNamespace(NamespaceBlockSyntax node)
    {
        var outer = node.Ancestors().OfType<NamespaceBlockSyntax>().Reverse().Select(n => n.NamespaceStatement.Name.ToString()).ToList();
        outer.Add(node.NamespaceStatement.Name.ToString());
        return string.Join('.', outer.Where(s => !string.IsNullOrWhiteSpace(s)));
    }

    private static string? GetEnclosingType(SyntaxNode node)
    {
        var types = node.Ancestors().OfType<TypeBlockSyntax>().Reverse().Select(GetTypeName).Where(v => !string.IsNullOrWhiteSpace(v)).ToList();
        if (types.Count == 0)
        {
            return null;
        }
        return string.Join('.', types);
    }

    private static string JoinDot(params string?[] parts)
    {
        return string.Join('.', parts.Where(p => !string.IsNullOrWhiteSpace(p))!);
    }

    private static (int StartLine, int EndLine) GetLineSpan(SyntaxNode node)
    {
        var span = node.GetLocation().GetLineSpan();
        return (span.StartLinePosition.Line + 1, span.EndLinePosition.Line + 1);
    }

    private static string BuildNote(string code, string comment, string summary)
    {
        var parts = new List<string>();
        if (!string.IsNullOrWhiteSpace(summary))
        {
            parts.Add($"Summary:\n{summary}");
        }
        if (!string.IsNullOrWhiteSpace(comment))
        {
            parts.Add($"Comment:\n{comment}");
        }
        if (!string.IsNullOrWhiteSpace(code))
        {
            parts.Add($"Code:\n{code}");
        }
        return string.Join("\n\n", parts);
    }

    private static string ResolvePath(string root, string value)
    {
        if (Path.IsPathRooted(value))
        {
            return NormalizePath(value);
        }
        return NormalizePath(Path.Combine(root, value));
    }

    private static string NormalizePath(string path)
    {
        return Path.GetFullPath(path).Replace('\\', '/');
    }

    private static string ToRelPath(string root, string path)
    {
        var rel = Path.GetRelativePath(root, path).Replace('\\', '/');
        return rel;
    }

    private static async Task WriteJsonAsync(object payload)
    {
        var options = new JsonSerializerOptions
        {
            WriteIndented = false,
        };
        await Console.Out.WriteAsync(JsonSerializer.Serialize(payload, options));
    }
}
