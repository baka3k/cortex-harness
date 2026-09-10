using System.Text.Json.Serialization;

namespace CSharpRoslynWorker;

/// <summary>
/// Request manifest exchanged with the Python orchestrator.
/// </summary>
internal sealed record WorkerRequest(
    string ProtocolVersion,
    string Root,
    string[] Files,
    string SemanticMode,
    string ProjectPath,
    int WorkspaceTimeoutMs,
    int FileTimeoutMs,
    int MaxFileBytes,
    bool ExtractMembers,
    bool ExtractSemantic,
    bool ExtractAttributes,
    bool ExtractXmlDocs,
    bool ExtractCallsResolved);

internal sealed record WorkerResponse(
    string ProtocolVersion,
    string CoverageStatus,
    string WorkspaceKind,
    bool SemanticEnabled,
    bool WorkspaceRequested,
    string ProjectPath,
    IReadOnlyList<DocumentResult> Results,
    IReadOnlyList<WorkerDiagnostic> Diagnostics,
    ProjectMetadata? Project);

internal sealed record DocumentResult(string FilePath, bool Ok, DocumentEvidence? Evidence, string? Error);

internal sealed record DocumentEvidence(
    string FilePath,
    string Namespace,
    IReadOnlyList<TypeEvidence> Types,
    IReadOnlyList<MemberEvidence> Members,
    IReadOnlyList<FieldEvidence> Fields,
    IReadOnlyList<EventEvidence> Events,
    IReadOnlyList<DelegateEvidence> Delegates,
    IReadOnlyList<ParameterEvidence> Parameters,
    IReadOnlyList<UsingEvidence> Usings,
    IReadOnlyList<AttributeEvidence> Attributes,
    IReadOnlyList<InvocationEvidence> Calls,
    IReadOnlyList<WorkerDiagnostic> Diagnostics,
    string ParseMeta);

internal sealed record TypeEvidence(
    string Name,
    string QualifiedName,
    string Kind,
    int StartLine,
    int EndLine,
    string CanonicalSymbolId,
    IReadOnlyList<string> BaseTypes,
    IReadOnlyList<string> ImplementedInterfaces,
    IReadOnlyList<string> TypeParameters,
    IReadOnlyDictionary<string, string[]> TypeConstraints,
    bool IsAbstract,
    bool IsSealed,
    bool IsStatic,
    bool IsPartial,
    bool IsRecord,
    string Accessibility,
    IReadOnlyList<string> Attributes,
    string XmlDoc);

internal sealed record MemberEvidence(
    string Name,
    string QualifiedName,
    string Kind,
    int StartLine,
    int EndLine,
    string CanonicalSymbolId,
    string Accessibility,
    IReadOnlyList<string> Attributes,
    string ReturnType,
    IReadOnlyList<string> TypeParameters,
    bool IsAsync,
    bool IsStatic,
    bool IsVirtual,
    bool IsOverride,
    bool IsAbstract,
    bool IsExtensionMethod,
    bool IsLambda,
    bool IsLocalFunction,
    string XmlDoc,
    IReadOnlyList<ParameterEvidence> Parameters);

internal sealed record FieldEvidence(
    string Name,
    string QualifiedName,
    string Kind,
    int StartLine,
    int EndLine,
    string CanonicalSymbolId,
    string TypeName,
    string Accessibility,
    bool IsStatic,
    bool IsConst,
    bool IsReadonly,
    bool IsVolatile,
    string ConstantValue,
    IReadOnlyList<string> Attributes,
    string XmlDoc);

internal sealed record EventEvidence(
    string Name,
    string QualifiedName,
    int StartLine,
    int EndLine,
    string CanonicalSymbolId,
    string DelegateType,
    string Accessibility,
    bool IsStatic,
    bool IsAbstract,
    IReadOnlyList<string> Attributes,
    string XmlDoc);

internal sealed record DelegateEvidence(
    string Name,
    string QualifiedName,
    int StartLine,
    int EndLine,
    string CanonicalSymbolId,
    string ReturnType,
    IReadOnlyList<string> TypeParameters,
    string Accessibility,
    IReadOnlyList<string> Attributes,
    IReadOnlyList<ParameterEvidence> Parameters,
    string XmlDoc);

internal sealed record ParameterEvidence(
    string Name,
    string TypeName,
    bool IsOptional,
    string DefaultValue,
    bool IsParams,
    bool IsRef,
    bool IsOut,
    bool IsIn);

internal sealed record UsingEvidence(
    string Name,
    string? Alias,
    bool IsStatic);

internal sealed record AttributeEvidence(
    string Name,
    string? QualifiedName,
    int StartLine,
    IReadOnlyList<string> Arguments);

internal sealed record InvocationEvidence(
    string Expression,
    string CallerId,
    int StartLine,
    string? ResolvedCalleeId,
    string? ResolvedCalleeName,
    int? CalleeArity,
    bool Resolved,
    bool IsAsync,
    bool IsVirtualDispatch,
    IReadOnlyList<string> Arguments);

internal sealed record WorkerDiagnostic(string Code, string Message, string Severity, string FilePath);

internal sealed record ProjectMetadata(
    string ProjectPath,
    string TargetFramework,
    string Sdk,
    string OutputType,
    IReadOnlyList<PackageReference> NuGetPackages,
    IReadOnlyList<string> ProjectReferences);

internal sealed record PackageReference(string Name, string Version, bool IsDevelopment);
