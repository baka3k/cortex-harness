using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

internal static class Program
{
    private const string ProtocolVersion = "aspnet-roslyn-v1";
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        WriteIndented = false,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    public static async Task<int> Main(string[] args)
    {
        try
        {
            var manifest = Argument(args, "--manifest");
            if (string.IsNullOrWhiteSpace(manifest) || !File.Exists(manifest))
            {
                Console.Error.WriteLine("Usage: AspNetRoslynWorker --manifest <request.json>");
                return 2;
            }

            var request = JsonSerializer.Deserialize<WorkerRequest>(
                await File.ReadAllTextAsync(manifest), JsonOptions)
                ?? throw new InvalidOperationException("Manifest is empty");
            if (!string.Equals(request.ProtocolVersion, ProtocolVersion, StringComparison.Ordinal))
            {
                throw new InvalidOperationException($"Unsupported protocol: {request.ProtocolVersion}");
            }

            var response = await AnalyzeAsync(request);
            Console.Out.Write(JsonSerializer.Serialize(response, JsonOptions));
            return response.CoverageStatus == "failed" ? 3 : 0;
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine(exception.ToString());
            return 3;
        }
    }

    private static async Task<WorkerResponse> AnalyzeAsync(WorkerRequest request)
    {
        var root = Path.GetFullPath(request.Root);
        var files = request.Files
            .Select(path => ResolveInsideRoot(root, path, true))
            .Distinct(StringComparer.Ordinal)
            .OrderBy(path => path, StringComparer.Ordinal)
            .ToArray();
        var diagnostics = new List<WorkerDiagnostic>();
        var documents = new Dictionary<string, (SyntaxTree Tree, SemanticModel? Model, string ProjectPath)>(StringComparer.Ordinal);
        var fileErrors = new Dictionary<string, string>(StringComparer.Ordinal);
        var workspaceKind = "none";
        var semanticEnabled = false;

        foreach (var file in files)
        {
            try
            {
                var size = new FileInfo(file).Length;
                if (size > Math.Max(1, request.MaxFileBytes))
                {
                    throw new InvalidOperationException(
                        $"C# source exceeds max_file_bytes ({size} > {request.MaxFileBytes})");
                }
                using var timeout = new CancellationTokenSource(Math.Max(5_000, request.FileTimeoutMs));
                var text = await File.ReadAllTextAsync(file, timeout.Token);
                var tree = CSharpSyntaxTree.ParseText(
                    text, CSharpParseOptions.Default.WithLanguageVersion(LanguageVersion.Latest),
                    path: file, cancellationToken: timeout.Token);
                documents[file] = (tree, null, request.ProjectPath);
            }
            catch (Exception exception)
            {
                fileErrors[file] = exception.Message;
            }
        }

        var compilationHasErrors = false;
        var projectSemanticsUnavailable = !string.IsNullOrWhiteSpace(request.ProjectPath);
        if (!string.Equals(request.SemanticMode, "off", StringComparison.OrdinalIgnoreCase) && documents.Count > 0)
        {
            try
            {
                var syntaxTrees = documents.Values.Select(item => item.Tree).ToArray();
                var outputKind = syntaxTrees.Any(
                    tree => tree.GetRoot().DescendantNodes().OfType<GlobalStatementSyntax>().Any())
                    ? OutputKind.ConsoleApplication
                    : OutputKind.DynamicallyLinkedLibrary;
                var globalUsings = CSharpSyntaxTree.ParseText("""
                    global using System;
                    global using System.Collections.Generic;
                    global using System.Linq;
                    global using System.Threading;
                    global using System.Threading.Tasks;
                    global using Microsoft.AspNetCore.Builder;
                    global using Microsoft.AspNetCore.Http;
                    global using Microsoft.Extensions.Configuration;
                    global using Microsoft.Extensions.DependencyInjection;
                    global using Microsoft.Extensions.Hosting;
                    """, CSharpParseOptions.Default.WithLanguageVersion(LanguageVersion.Latest));
                var compilation = CSharpCompilation.Create(
                    "AspNetSemanticOverlay",
                    syntaxTrees.Append(globalUsings),
                    TrustedRuntimeReferences(),
                    new CSharpCompilationOptions(outputKind));
                foreach (var file in documents.Keys.ToArray())
                {
                    var document = documents[file];
                    documents[file] = (document.Tree, compilation.GetSemanticModel(document.Tree), document.ProjectPath);
                }
                semanticEnabled = true;
                workspaceKind = "safe_compilation";
                var compilationDiagnostics = compilation.GetDiagnostics()
                    .Where(item => item.Severity is DiagnosticSeverity.Error or DiagnosticSeverity.Warning)
                    .Take(200)
                    .ToArray();
                compilationHasErrors = compilationDiagnostics.Any(item => item.Severity == DiagnosticSeverity.Error);
                diagnostics.AddRange(compilationDiagnostics.Select(item => new WorkerDiagnostic(
                    item.Id, item.GetMessage(), item.Severity.ToString().ToLowerInvariant(),
                    item.Location.IsInSource ? Relative(root, item.Location.SourceTree?.FilePath ?? "") : "")));
                if (projectSemanticsUnavailable)
                {
                    diagnostics.Add(new WorkerDiagnostic(
                        "aspnet.roslyn.project_semantics_unavailable",
                        "Project metadata was not evaluated by the safe compiler; semantic coverage is partial",
                        "warning", request.ProjectPath));
                }
            }
            catch (Exception exception)
            {
                diagnostics.Add(new WorkerDiagnostic(
                    "aspnet.roslyn.safe_compilation_failed", exception.Message, "warning", request.ProjectPath));
                if (string.Equals(request.SemanticMode, "on", StringComparison.OrdinalIgnoreCase))
                {
                    return new WorkerResponse(ProtocolVersion, "failed", workspaceKind, false, Array.Empty<DocumentResult>(), diagnostics);
                }
            }
        }

        var results = new List<DocumentResult>();
        foreach (var file in files)
        {
            try
            {
                SyntaxTree tree;
                SemanticModel? model;
                string projectPath;
                if (documents.TryGetValue(file, out var document))
                {
                    (tree, model, projectPath) = document;
                }
                else
                {
                    throw new InvalidOperationException(fileErrors.GetValueOrDefault(file, "C# source could not be parsed"));
                }
                results.Add(new DocumentResult(Relative(root, file), true, Extract(root, tree, model, projectPath), null));
            }
            catch (Exception exception)
            {
                results.Add(new DocumentResult(Relative(root, file), false, null, exception.Message));
            }
        }

        var failures = results.Count(item => !item.Ok);
        var coverage = failures == results.Count
            ? "failed"
            : semanticEnabled && failures == 0 && !compilationHasErrors && !projectSemanticsUnavailable
                ? "complete" : "partial";
        return new WorkerResponse(ProtocolVersion, coverage, workspaceKind, semanticEnabled, results, diagnostics);
    }

    private static IReadOnlyList<MetadataReference> TrustedRuntimeReferences()
    {
        var trusted = Convert.ToString(AppContext.GetData("TRUSTED_PLATFORM_ASSEMBLIES")) ?? "";
        return trusted.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries)
            .Where(File.Exists)
            .Distinct(StringComparer.Ordinal)
            .OrderBy(path => path, StringComparer.Ordinal)
            .Select(path => MetadataReference.CreateFromFile(path))
            .ToArray();
    }

    private static DocumentEvidence Extract(string root, SyntaxTree tree, SemanticModel? model, string projectPath)
    {
        var syntaxRoot = tree.GetRoot();
        var namespaceName = syntaxRoot.DescendantNodes()
            .OfType<BaseNamespaceDeclarationSyntax>()
            .Select(item => item.Name.ToString())
            .FirstOrDefault() ?? "";
        var types = syntaxRoot.DescendantNodes()
            .OfType<BaseTypeDeclarationSyntax>()
            .Select(node =>
            {
                var symbol = model?.GetDeclaredSymbol(node);
                var qualifiedName = Display(symbol) ?? Qualified(namespaceName, node.Identifier.ValueText);
                return new TypeEvidence(
                    node.Identifier.ValueText,
                    qualifiedName,
                    node.Kind().ToString(),
                    Line(node),
                    symbol is null ? "" : CanonicalTypeId(symbol),
                    node.BaseList?.Types.Select(item => item.Type.ToString()).OrderBy(item => item, StringComparer.Ordinal).ToArray()
                        ?? Array.Empty<string>());
            })
            .OrderBy(item => item.QualifiedName, StringComparer.Ordinal)
            .ThenBy(item => item.StartLine)
            .ToArray();
        var members = syntaxRoot.DescendantNodes()
            .OfType<MemberDeclarationSyntax>()
            .Select(node => node switch
            {
                MethodDeclarationSyntax method => Member(root, tree, model?.GetDeclaredSymbol(method), method.Identifier.ValueText, "method", Line(method), method.AttributeLists),
                ConstructorDeclarationSyntax constructor => Member(root, tree, model?.GetDeclaredSymbol(constructor), constructor.Identifier.ValueText, "constructor", Line(constructor), constructor.AttributeLists),
                PropertyDeclarationSyntax property => Member(root, tree, model?.GetDeclaredSymbol(property), property.Identifier.ValueText, "property", Line(property), property.AttributeLists),
                _ => null,
            })
            .Where(item => item is not null)
            .Cast<MemberEvidence>()
            .OrderBy(item => item.QualifiedName, StringComparer.Ordinal)
            .ThenBy(item => item.StartLine)
            .ToArray();
        var invocations = syntaxRoot.DescendantNodes()
            .OfType<InvocationExpressionSyntax>()
            .Select(node =>
            {
                var symbol = model?.GetSymbolInfo(node).Symbol as IMethodSymbol;
                var constants = node.ArgumentList.Arguments.Select(argument =>
                {
                    var constant = model?.GetConstantValue(argument.Expression);
                    return constant.HasValue ? Convert.ToString(constant.Value, System.Globalization.CultureInfo.InvariantCulture) ?? "" : "";
                }).ToArray();
                return new InvocationEvidence(
                    node.Expression.ToString(), Display(symbol), Line(node), constants,
                    node.ArgumentList.Arguments.Select(argument => argument.Expression.ToString()).ToArray());
            })
            .OrderBy(item => item.StartLine)
            .ThenBy(item => item.Expression, StringComparer.Ordinal)
            .ToArray();
        var attributes = syntaxRoot.DescendantNodes()
            .OfType<AttributeSyntax>()
            .Select(node => new AttributeEvidence(
                node.Name.ToString(),
                Display(model?.GetSymbolInfo(node).Symbol?.ContainingType),
                Line(node),
                node.ArgumentList?.Arguments.Select(argument => argument.Expression.ToString()).ToArray() ?? Array.Empty<string>()))
            .OrderBy(item => item.StartLine)
            .ThenBy(item => item.Name, StringComparer.Ordinal)
            .ToArray();
        var diagnostics = tree.GetDiagnostics()
            .Take(200)
            .Select(item => new WorkerDiagnostic(
                item.Id, item.GetMessage(), item.Severity.ToString().ToLowerInvariant(), Relative(root, tree.FilePath)))
            .ToArray();
        return new DocumentEvidence(projectPath, namespaceName, types, members, attributes, invocations, diagnostics);
    }

    private static MemberEvidence Member(
        string root,
        SyntaxTree tree,
        ISymbol? symbol,
        string name,
        string kind,
        int line,
        SyntaxList<AttributeListSyntax> attributes)
        => new(
            name,
            Display(symbol) ?? name,
            kind,
            line,
            CanonicalMemberId(root, tree, symbol, name),
            symbol?.DeclaredAccessibility.ToString().ToLowerInvariant() ?? "",
            attributes.SelectMany(item => item.Attributes).Select(item => item.Name.ToString())
                .OrderBy(item => item, StringComparer.Ordinal).ToArray());

    private static string CanonicalTypeId(INamedTypeSymbol symbol)
    {
        var typeNames = new Stack<string>();
        for (INamedTypeSymbol? current = symbol; current is not null; current = current.ContainingType)
        {
            typeNames.Push(current.Name);
        }
        var namespaceName = symbol.ContainingNamespace?.IsGlobalNamespace == false
            ? symbol.ContainingNamespace.ToDisplayString()
            : "";
        return string.Join("::", string.IsNullOrEmpty(namespaceName) ? typeNames : new[] { namespaceName }.Concat(typeNames));
    }

    private static string CanonicalMemberId(string root, SyntaxTree tree, ISymbol? symbol, string sourceName)
    {
        if (symbol is not IMethodSymbol method || symbol.ContainingType is not INamedTypeSymbol containingType)
        {
            return "";
        }
        return $"{CanonicalTypeId(containingType)}::{sourceName}/{method.Parameters.Length}@{Relative(root, tree.FilePath)}";
    }

    private static string? Display(ISymbol? symbol)
        => symbol?.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat);

    private static string Qualified(string namespaceName, string name)
        => string.IsNullOrEmpty(namespaceName) ? name : $"{namespaceName}.{name}";

    private static int Line(SyntaxNode node)
        => node.GetLocation().GetLineSpan().StartLinePosition.Line + 1;

    private static string ResolveInsideRoot(string root, string path, bool requireFile)
    {
        var candidate = Path.GetFullPath(Path.IsPathRooted(path) ? path : Path.Combine(root, path));
        var relative = Path.GetRelativePath(root, candidate);
        if (relative == ".." || relative.StartsWith(".." + Path.DirectorySeparatorChar, StringComparison.Ordinal) || Path.IsPathRooted(relative))
        {
            throw new InvalidOperationException($"Path is outside root: {path}");
        }
        if (requireFile && !File.Exists(candidate))
        {
            throw new FileNotFoundException("Input file not found", candidate);
        }
        return candidate;
    }

    private static string Relative(string root, string path)
        => Path.GetRelativePath(root, path).Replace('\\', '/');

    private static string Argument(string[] args, string name)
    {
        var index = Array.IndexOf(args, name);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : "";
    }
}

internal sealed record WorkerRequest(
    string ProtocolVersion,
    string Root,
    string[] Files,
    string SemanticMode,
    string ProjectPath,
    int WorkspaceTimeoutMs,
    int FileTimeoutMs,
    int MaxFileBytes);

internal sealed record WorkerResponse(
    string ProtocolVersion,
    string CoverageStatus,
    string WorkspaceKind,
    bool SemanticEnabled,
    IReadOnlyList<DocumentResult> Results,
    IReadOnlyList<WorkerDiagnostic> Diagnostics);

internal sealed record DocumentResult(string FilePath, bool Ok, DocumentEvidence? Evidence, string? Error);
internal sealed record DocumentEvidence(
    string ProjectPath,
    string Namespace,
    IReadOnlyList<TypeEvidence> Types,
    IReadOnlyList<MemberEvidence> Members,
    IReadOnlyList<AttributeEvidence> Attributes,
    IReadOnlyList<InvocationEvidence> Invocations,
    IReadOnlyList<WorkerDiagnostic> Diagnostics);
internal sealed record TypeEvidence(string Name, string QualifiedName, string Kind, int StartLine, string CanonicalSymbolId, IReadOnlyList<string> BaseTypes);
internal sealed record MemberEvidence(string Name, string QualifiedName, string Kind, int StartLine, string CanonicalSymbolId, string Accessibility, IReadOnlyList<string> Attributes);
internal sealed record AttributeEvidence(string Name, string? QualifiedName, int StartLine, IReadOnlyList<string> Arguments);
internal sealed record InvocationEvidence(string Expression, string? QualifiedName, int StartLine, IReadOnlyList<string> ConstantArguments, IReadOnlyList<string> Arguments);
internal sealed record WorkerDiagnostic(string Code, string Message, string Severity, string FilePath);
