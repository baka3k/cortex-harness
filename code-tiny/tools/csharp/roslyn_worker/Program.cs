using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;

namespace CSharpRoslynWorker;

internal static class Program
{
    private const string ProtocolVersion = "csharp-v1";
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
                Console.Error.WriteLine("Usage: CSharpRoslynWorker --manifest <request.json>");
                return 2;
            }

            var requestJson = await File.ReadAllTextAsync(manifest);
            var request = JsonSerializer.Deserialize<WorkerRequest>(requestJson, JsonOptions)
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
        var documents = new Dictionary<string, (SyntaxTree Tree, SemanticModel? Model)>(StringComparer.Ordinal);
        var treesByFile = new Dictionary<string, SyntaxTree>(StringComparer.Ordinal);

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
                    text,
                    CSharpParseOptions.Default.WithLanguageVersion(LanguageVersion.Latest),
                    path: file,
                    cancellationToken: timeout.Token);
                documents[file] = (tree, null);
                treesByFile[file] = tree;
            }
            catch (Exception exception)
            {
                diagnostics.Add(new WorkerDiagnostic(
                    "csharp.roslyn.file_failed", exception.Message, "error", file));
            }
        }

        var plan = CompilationService.BuildPlan(request, files, treesByFile);
        diagnostics.AddRange(plan.Diagnostics);

        if (plan.SemanticEnabled && plan.Compilation is not null)
        {
            foreach (var file in documents.Keys.ToArray())
            {
                var document = documents[file];
                var model = plan.Compilation.GetSemanticModel(document.Tree);
                documents[file] = (document.Tree, model);
            }
            var compilationDiagnostics = plan.Compilation.GetDiagnostics()
                .Where(item => item.Severity is DiagnosticSeverity.Error or DiagnosticSeverity.Warning)
                .Take(200)
                .ToArray();
            diagnostics.AddRange(compilationDiagnostics.Select(item => new WorkerDiagnostic(
                item.Id, item.GetMessage(), item.Severity.ToString().ToLowerInvariant(),
                item.Location.IsInSource
                    ? Path.GetRelativePath(root, item.Location.SourceTree?.FilePath ?? "").Replace('\\', '/')
                    : "")));
        }

        var results = new List<DocumentResult>();
        foreach (var file in files)
        {
            try
            {
                if (!documents.TryGetValue(file, out var document))
                {
                    results.Add(new DocumentResult(
                        Path.GetRelativePath(root, file).Replace('\\', '/'),
                        false, null, "file could not be parsed"));
                    continue;
                }
                var evidence = MemberExtractor.Extract(
                    root, document.Tree, document.Model,
                    request.ExtractMembers, request.ExtractSemantic,
                    request.ExtractAttributes, request.ExtractXmlDocs, request.ExtractCallsResolved);
                results.Add(new DocumentResult(
                    Path.GetRelativePath(root, file).Replace('\\', '/'),
                    true, evidence, null));
            }
            catch (Exception exception)
            {
                results.Add(new DocumentResult(
                    Path.GetRelativePath(root, file).Replace('\\', '/'),
                    false, null, exception.Message));
            }
        }

        var failures = results.Count(item => !item.Ok);
        var compilationHasErrors = diagnostics.Any(d => d.Severity == "error");
        string coverage;
        if (files.Length == 0)
        {
            coverage = "empty";
        }
        else if (failures == results.Count)
        {
            coverage = "failed";
        }
        else if (plan.SemanticEnabled && failures == 0 && !compilationHasErrors)
        {
            coverage = "full";
        }
        else if (plan.SemanticEnabled)
        {
            coverage = "partial";
        }
        else
        {
            coverage = "syntax_only";
        }

        var project = CompilationService.TryReadProjectFile(request);

        return new WorkerResponse(
            ProtocolVersion,
            coverage,
            plan.WorkspaceKind,
            plan.SemanticEnabled,
            plan.WorkspaceKind != "none",
            request.ProjectPath,
            results,
            diagnostics,
            project);
    }

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

    private static string Argument(string[] args, string name)
    {
        var index = Array.IndexOf(args, name);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : "";
    }
}
