using System.Text.RegularExpressions;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace CSharpRoslynWorker;

/// <summary>
/// Builds a compilation context — workspace mode (when .csproj/.sln is supplied) or
/// pure syntax mode (when only .cs files are available). Always degrades gracefully
/// to syntax-only output when the workspace is unavailable.
/// </summary>
internal static class CompilationService
{
    public static CompilationPlan BuildPlan(
        WorkerRequest request,
        IReadOnlyList<string> resolvedFiles,
        IReadOnlyDictionary<string, SyntaxTree> treesByFile)
    {
        var semanticWanted = !string.Equals(request.SemanticMode, "off", StringComparison.OrdinalIgnoreCase);
        var workspaceKind = "none";
        var semanticEnabled = false;
        var errors = new List<WorkerDiagnostic>();
        CSharpCompilation? compilation = null;

        if (semanticWanted && treesByFile.Count > 0)
        {
            try
            {
                var syntaxTrees = new List<SyntaxTree>();
                foreach (var file in resolvedFiles)
                {
                    if (treesByFile.TryGetValue(file, out var tree))
                    {
                        syntaxTrees.Add(tree);
                    }
                }

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
                    global using System.Collections.Concurrent;
                    """, CSharpParseOptions.Default.WithLanguageVersion(LanguageVersion.Latest));

                compilation = CSharpCompilation.Create(
                    "CSharpPrimary",
                    syntaxTrees.Append(globalUsings),
                    TrustedRuntimeReferences(),
                    new CSharpCompilationOptions(outputKind));
                workspaceKind = "safe_compilation";
                semanticEnabled = true;
            }
            catch (Exception exception)
            {
                errors.Add(new WorkerDiagnostic(
                    "csharp.roslyn.safe_compilation_failed", exception.Message, "warning", request.ProjectPath));
                if (string.Equals(request.SemanticMode, "on", StringComparison.OrdinalIgnoreCase))
                {
                    throw;
                }
                compilation = null;
                semanticEnabled = false;
            }
        }

        return new CompilationPlan(workspaceKind, compilation, semanticEnabled, errors);
    }

    public static IReadOnlyList<MetadataReference> TrustedRuntimeReferences()
    {
        var trusted = Convert.ToString(AppContext.GetData("TRUSTED_PLATFORM_ASSEMBLIES")) ?? "";
        return trusted.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries)
            .Where(File.Exists)
            .Distinct(StringComparer.Ordinal)
            .OrderBy(path => path, StringComparer.Ordinal)
            .Select(path => MetadataReference.CreateFromFile(path))
            .ToArray();
    }

    public static ProjectMetadata? TryReadProjectFile(WorkerRequest request)
    {
        if (string.IsNullOrWhiteSpace(request.ProjectPath))
        {
            return null;
        }

        var full = Path.IsPathRooted(request.ProjectPath)
            ? request.ProjectPath
            : Path.GetFullPath(Path.Combine(request.Root, request.ProjectPath));
        if (!File.Exists(full))
        {
            return new ProjectMetadata(request.ProjectPath, "", "", "",
                Array.Empty<PackageReference>(), Array.Empty<string>());
        }

        var text = File.ReadAllText(full);
        var targetFramework = ExtractTag(text, "TargetFramework") ?? "";
        var sdk = ExtractTopLevelSdk(full) ?? "";
        var outputType = ExtractTag(text, "OutputType") ?? "Library";
        var nugets = ExtractPackageReferences(text);
        var projRefs = ExtractProjectReferences(text);
        return new ProjectMetadata(
            request.ProjectPath, targetFramework, sdk, outputType, nugets, projRefs);
    }

    private static string? ExtractTag(string text, string tag)
    {
        var match = Regex.Match(text, $"<{tag}>([^<]+)</{tag}>", RegexOptions.IgnoreCase);
        return match.Success ? match.Groups[1].Value.Trim() : null;
    }

    private static string? ExtractTopLevelSdk(string projectFile)
    {
        try
        {
            using var reader = File.OpenText(projectFile);
            var firstLine = reader.ReadLine() ?? "";
            var match = Regex.Match(firstLine, @"Sdk=""([^""]+)""", RegexOptions.IgnoreCase);
            return match.Success ? match.Groups[1].Value : null;
        }
        catch
        {
            return null;
        }
    }

    private static IReadOnlyList<PackageReference> ExtractPackageReferences(string text)
    {
        var packages = new List<PackageReference>();
        var matches = Regex.Matches(
            text,
            @"<PackageReference\s+Include\s*=\s*""(?<name>[^""]+)""\s+Version\s*=\s*""(?<version>[^""]+)""(?<rest>[^/]*)/?>",
            RegexOptions.IgnoreCase);
        foreach (Match match in matches)
        {
            var name = match.Groups["name"].Value;
            var version = match.Groups["version"].Value;
            var isDevelopment = match.Groups["rest"].Value.Contains(
                "PrivateAssets=\"all\"", StringComparison.OrdinalIgnoreCase);
            packages.Add(new PackageReference(name, version, isDevelopment));
        }
        return packages;
    }

    private static IReadOnlyList<string> ExtractProjectReferences(string text)
    {
        var refs = new List<string>();
        var matches = Regex.Matches(
            text,
            @"<ProjectReference\s+Include\s*=\s*""([^""]+)""",
            RegexOptions.IgnoreCase);
        foreach (Match match in matches)
        {
            refs.Add(match.Groups[1].Value);
        }
        return refs;
    }
}

internal sealed record CompilationPlan(
    string WorkspaceKind,
    CSharpCompilation? Compilation,
    bool SemanticEnabled,
    IReadOnlyList<WorkerDiagnostic> Diagnostics);
