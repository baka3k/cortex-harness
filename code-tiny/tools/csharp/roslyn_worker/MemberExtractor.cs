using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace CSharpRoslynWorker;

/// <summary>
/// Extracts the full member inventory (types, methods, properties, fields, events, delegates,
/// parameters) and resolved semantic information from a single C# file.
/// </summary>
internal static class MemberExtractor
{
    public static DocumentEvidence Extract(
        string root,
        SyntaxTree tree,
        SemanticModel? model,
        bool extractMembers,
        bool extractSemantic,
        bool extractAttributes,
        bool extractXmlDocs,
        bool extractCallsResolved)
    {
        var syntaxRoot = tree.GetRoot();
        var relPath = Path.GetRelativePath(root, tree.FilePath).Replace('\\', '/');

        var namespaceName = syntaxRoot.DescendantNodes()
            .OfType<BaseNamespaceDeclarationSyntax>()
            .Select(item => item.Name.ToString())
            .FirstOrDefault() ?? "";

        var types = syntaxRoot.DescendantNodes()
            .OfType<BaseTypeDeclarationSyntax>()
            .Select(node => BuildType(root, tree, model, node, extractSemantic, extractAttributes, extractXmlDocs))
            .OrderBy(item => item.QualifiedName, StringComparer.Ordinal)
            .ThenBy(item => item.StartLine)
            .ToArray();

        var members = new List<MemberEvidence>();
        var fields = new List<FieldEvidence>();
        var events = new List<EventEvidence>();
        var delegates = new List<DelegateEvidence>();
        var memberParameters = new List<ParameterEvidence>();

        if (extractMembers)
        {
            foreach (var node in syntaxRoot.DescendantNodes().OfType<MemberDeclarationSyntax>())
            {
                var built = BuildMember(root, tree, model, node, memberParameters,
                    extractSemantic, extractAttributes, extractXmlDocs);
                if (built is not null)
                {
                    members.Add(built);
                }
            }
            members = members
                .OrderBy(item => item.QualifiedName, StringComparer.Ordinal)
                .ThenBy(item => item.StartLine)
                .ToList();

            foreach (var node in syntaxRoot.DescendantNodes().OfType<FieldDeclarationSyntax>())
            {
                fields.AddRange(BuildFields(root, tree, model, node,
                    extractSemantic, extractAttributes, extractXmlDocs));
            }
            fields = fields
                .OrderBy(item => item.QualifiedName, StringComparer.Ordinal)
                .ThenBy(item => item.StartLine)
                .ToList();

            foreach (var node in syntaxRoot.DescendantNodes().OfType<EventFieldDeclarationSyntax>())
            {
                events.AddRange(BuildEvents(root, tree, model, node,
                    extractSemantic, extractAttributes, extractXmlDocs));
            }
            events = events
                .OrderBy(item => item.QualifiedName, StringComparer.Ordinal)
                .ThenBy(item => item.StartLine)
                .ToList();

            foreach (var node in syntaxRoot.DescendantNodes().OfType<DelegateDeclarationSyntax>())
            {
                delegates.Add(BuildDelegate(root, tree, model, node,
                    extractSemantic, extractAttributes, extractXmlDocs));
            }
            delegates = delegates
                .OrderBy(item => item.QualifiedName, StringComparer.Ordinal)
                .ThenBy(item => item.StartLine)
                .ToList();
        }

        var usings = syntaxRoot.DescendantNodes()
            .OfType<UsingDirectiveSyntax>()
            .Select(BuildUsing)
            .OrderBy(item => item.Name, StringComparer.Ordinal)
            .ToArray();

        var attributes = extractAttributes
            ? syntaxRoot.DescendantNodes()
                .OfType<AttributeSyntax>()
                .Select(node => BuildAttribute(root, tree, model, node))
                .OrderBy(item => item.StartLine)
                .ThenBy(item => item.Name, StringComparer.Ordinal)
                .ToArray()
            : Array.Empty<AttributeEvidence>();

        var calls = extractCallsResolved
            ? ExtractCalls(root, tree, model, syntaxRoot)
            : Array.Empty<InvocationEvidence>();

        var diagnostics = tree.GetDiagnostics()
            .Take(200)
            .Select(item => new WorkerDiagnostic(
                item.Id, item.GetMessage(),
                item.Severity.ToString().ToLowerInvariant(), relPath))
            .ToArray();

        var parseMeta = BuildParseMeta(model, diagnostics);

        return new DocumentEvidence(
            relPath, namespaceName,
            types,
            members,
            fields,
            events,
            delegates,
            memberParameters
                .GroupBy(p => (p.Name, p.TypeName, p.IsOptional, p.IsParams, p.IsRef, p.IsOut))
                .Select(g => g.First())
                .OrderBy(p => p.Name, StringComparer.Ordinal)
                .ToArray(),
            usings,
            attributes,
            calls,
            diagnostics,
            parseMeta);
    }

    private static TypeEvidence BuildType(
        string root,
        SyntaxTree tree,
        SemanticModel? model,
        BaseTypeDeclarationSyntax node,
        bool extractSemantic,
        bool extractAttributes,
        bool extractXmlDocs)
    {
        var symbol = model?.GetDeclaredSymbol(node) as INamedTypeSymbol;
        var nsName = node.FirstAncestorOrSelf<BaseNamespaceDeclarationSyntax>()?.Name.ToString() ?? "";
        var qualifiedName = symbol?.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat)
            ?? Qualified(nsName, node.Identifier.ValueText);
        var canonicalId = symbol is null ? "" : CanonicalTypeId(symbol);
        var kind = DetectTypeKind(node);
        // Resolve base types and interfaces from the syntax to keep coverage even when
        // semantic resolution is unavailable. When symbols are resolved, swap in the
        // canonical display form so the consumer can link by qualified name.
        var baseTypes = node.BaseList?.Types
            .Where(t => t.Type is not null && t.ToString() != "")
            .Select(t => t.ToString())
            .OrderBy(t => t, StringComparer.Ordinal)
            .ToArray() ?? Array.Empty<string>();
        var interfaces = Array.Empty<string>();
        var typeParams = (node as TypeDeclarationSyntax)?.TypeParameterList?.Parameters
            .Select(tp => tp.Identifier.ValueText)
            .ToArray() ?? Array.Empty<string>();
        var constraints = new Dictionary<string, string[]>(StringComparer.Ordinal);

        if (extractSemantic && symbol is not null)
        {
            if (symbol.BaseType is not null
                && symbol.BaseType.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat) is { } bt
                && bt != "object")
            {
                baseTypes = new[] { bt };
            }
            interfaces = symbol.Interfaces
                .Select(i => i.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat))
                .OrderBy(i => i, StringComparer.Ordinal)
                .ToArray();
            typeParams = symbol.TypeParameters.Select(tp => tp.Name).ToArray();
            constraints = symbol.TypeParameters.ToDictionary(
                tp => tp.Name,
                tp => tp.ConstraintTypes.Select(c =>
                    c.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat)).ToArray(),
                StringComparer.Ordinal);
        }

        var isAbstract = symbol?.IsAbstract
            ?? node.Modifiers.Any(SyntaxKind.AbstractKeyword);
        var isSealed = symbol?.IsSealed
            ?? node.Modifiers.Any(SyntaxKind.SealedKeyword);
        var isStatic = symbol?.IsStatic
            ?? node.Modifiers.Any(SyntaxKind.StaticKeyword);
        var isPartial = node.Modifiers.Any(SyntaxKind.PartialKeyword);
        var isRecord = node is RecordDeclarationSyntax;
        var accessibility = symbol?.DeclaredAccessibility.ToString().ToLowerInvariant()
            ?? ExtractAccessibility(node.Modifiers);
        var attributeNames = extractAttributes
            ? node.AttributeLists.SelectMany(al => al.Attributes)
                .Select(a => Display(model?.GetSymbolInfo(a).Symbol?.ContainingType) ?? a.Name.ToString())
                .OrderBy(a => a, StringComparer.Ordinal)
                .ToArray()
            : Array.Empty<string>();
        var xmlDoc = extractXmlDocs ? ExtractXmlDoc(node) : "";
        return new TypeEvidence(
            node.Identifier.ValueText,
            qualifiedName,
            kind,
            Line(node), EndLine(node),
            canonicalId,
            baseTypes, interfaces, typeParams, constraints,
            isAbstract, isSealed, isStatic, isPartial, isRecord,
            accessibility, attributeNames, xmlDoc);
    }

    private static MemberEvidence? BuildMember(
        string root,
        SyntaxTree tree,
        SemanticModel? model,
        MemberDeclarationSyntax node,
        List<ParameterEvidence> collectedParameters,
        bool extractSemantic,
        bool extractAttributes,
        bool extractXmlDocs)
    {
        return node switch
        {
            MethodDeclarationSyntax method => BuildMethod(root, tree, model, method, collectedParameters,
                extractSemantic, extractAttributes, extractXmlDocs),
            ConstructorDeclarationSyntax ctor => BuildConstructor(root, tree, model, ctor, collectedParameters,
                extractSemantic, extractAttributes, extractXmlDocs),
            PropertyDeclarationSyntax property => BuildProperty(root, tree, model, property,
                extractSemantic, extractAttributes, extractXmlDocs),
            IndexerDeclarationSyntax indexer => BuildIndexer(root, tree, model, indexer, collectedParameters,
                extractSemantic, extractAttributes, extractXmlDocs),
            _ => null,
        };
    }

    private static MemberEvidence BuildMethod(
        string root, SyntaxTree tree, SemanticModel? model, MethodDeclarationSyntax node,
        List<ParameterEvidence> collectedParameters,
        bool extractSemantic, bool extractAttributes, bool extractXmlDocs)
    {
        var symbol = model?.GetDeclaredSymbol(node);
        var attributes = BuildAttributeNames(model, node.AttributeLists, extractAttributes);
        var typeParams = node.TypeParameterList?.Parameters
            .Select(tp => tp.Identifier.ValueText)
            .ToArray() ?? Array.Empty<string>();
        var returnType = node.ReturnType.ToString();
        var parameters = BuildParameters(model, node.ParameterList.Parameters, collectedParameters);
        return new MemberEvidence(
            node.Identifier.ValueText,
            symbol?.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat) ?? node.Identifier.ValueText,
            "method",
            Line(node), EndLine(node),
            CanonicalMemberId(root, tree, symbol, node.Identifier.ValueText),
            symbol?.DeclaredAccessibility.ToString().ToLowerInvariant() ?? ExtractAccessibility(node.Modifiers),
            attributes, returnType, typeParams,
            node.Modifiers.Any(SyntaxKind.AsyncKeyword),
            node.Modifiers.Any(SyntaxKind.StaticKeyword),
            node.Modifiers.Any(SyntaxKind.VirtualKeyword),
            node.Modifiers.Any(SyntaxKind.OverrideKeyword),
            node.Modifiers.Any(SyntaxKind.AbstractKeyword),
            symbol?.IsExtensionMethod ?? node.ParameterList.Parameters.Any(p =>
                p.Modifiers.Any(SyntaxKind.ThisKeyword)),
            false, false,
            extractXmlDocs ? ExtractXmlDoc(node) : "",
            parameters);
    }

    private static MemberEvidence BuildConstructor(
        string root, SyntaxTree tree, SemanticModel? model, ConstructorDeclarationSyntax node,
        List<ParameterEvidence> collectedParameters,
        bool extractSemantic, bool extractAttributes, bool extractXmlDocs)
    {
        var symbol = model?.GetDeclaredSymbol(node);
        var attributes = BuildAttributeNames(model, node.AttributeLists, extractAttributes);
        var parameters = BuildParameters(model, node.ParameterList.Parameters, collectedParameters);
        return new MemberEvidence(
            node.Identifier.ValueText,
            symbol?.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat) ?? node.Identifier.ValueText,
            "constructor",
            Line(node), EndLine(node),
            CanonicalMemberId(root, tree, symbol, node.Identifier.ValueText),
            symbol?.DeclaredAccessibility.ToString().ToLowerInvariant() ?? ExtractAccessibility(node.Modifiers),
            attributes, "", Array.Empty<string>(),
            false,
            node.Modifiers.Any(SyntaxKind.StaticKeyword),
            false, false, false, false, false, false,
            extractXmlDocs ? ExtractXmlDoc(node) : "",
            parameters);
    }

    private static MemberEvidence BuildProperty(
        string root, SyntaxTree tree, SemanticModel? model, PropertyDeclarationSyntax node,
        bool extractSemantic, bool extractAttributes, bool extractXmlDocs)
    {
        var symbol = model?.GetDeclaredSymbol(node);
        var attributes = BuildAttributeNames(model, node.AttributeLists, extractAttributes);
        var typeName = node.Type.ToString();
        return new MemberEvidence(
            node.Identifier.ValueText,
            symbol?.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat) ?? node.Identifier.ValueText,
            "property",
            Line(node), EndLine(node),
            CanonicalMemberId(root, tree, symbol, node.Identifier.ValueText),
            symbol?.DeclaredAccessibility.ToString().ToLowerInvariant() ?? ExtractAccessibility(node.Modifiers),
            attributes, typeName, Array.Empty<string>(),
            false,
            node.Modifiers.Any(SyntaxKind.StaticKeyword),
            node.Modifiers.Any(SyntaxKind.VirtualKeyword),
            node.Modifiers.Any(SyntaxKind.OverrideKeyword),
            node.Modifiers.Any(SyntaxKind.AbstractKeyword),
            false, false, false,
            extractXmlDocs ? ExtractXmlDoc(node) : "",
            Array.Empty<ParameterEvidence>());
    }

    private static MemberEvidence BuildIndexer(
        string root, SyntaxTree tree, SemanticModel? model, IndexerDeclarationSyntax node,
        List<ParameterEvidence> collectedParameters,
        bool extractSemantic, bool extractAttributes, bool extractXmlDocs)
    {
        var symbol = model?.GetDeclaredSymbol(node);
        var attributes = BuildAttributeNames(model, node.AttributeLists, extractAttributes);
        var typeName = node.Type.ToString();
        var parameters = BuildParameters(model, node.ParameterList.Parameters, collectedParameters);
        return new MemberEvidence(
            "this[]",
            symbol?.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat) ?? "this[]",
            "indexer",
            Line(node), EndLine(node),
            CanonicalMemberId(root, tree, symbol, "this[]"),
            symbol?.DeclaredAccessibility.ToString().ToLowerInvariant() ?? ExtractAccessibility(node.Modifiers),
            attributes, typeName, Array.Empty<string>(),
            false,
            node.Modifiers.Any(SyntaxKind.StaticKeyword),
            node.Modifiers.Any(SyntaxKind.VirtualKeyword),
            node.Modifiers.Any(SyntaxKind.OverrideKeyword),
            node.Modifiers.Any(SyntaxKind.AbstractKeyword),
            false, false, false,
            extractXmlDocs ? ExtractXmlDoc(node) : "",
            parameters);
    }

    private static IEnumerable<FieldEvidence> BuildFields(
        string root, SyntaxTree tree, SemanticModel? model, FieldDeclarationSyntax node,
        bool extractSemantic, bool extractAttributes, bool extractXmlDocs)
    {
        var symbol = model?.GetDeclaredSymbol(node);
        var isConst = node.Modifiers.Any(SyntaxKind.ConstKeyword);
        var isStatic = node.Modifiers.Any(SyntaxKind.StaticKeyword);
        var isReadonly = node.Modifiers.Any(SyntaxKind.ReadOnlyKeyword);
        var isVolatile = node.Modifiers.Any(SyntaxKind.VolatileKeyword);
        var accessibility = symbol?.DeclaredAccessibility.ToString().ToLowerInvariant()
            ?? ExtractAccessibility(node.Modifiers);
        var typeName = node.Declaration.Type.ToString();
        var attributeNames = BuildAttributeNames(model, node.AttributeLists, extractAttributes);
        var xmlDoc = extractXmlDocs ? ExtractXmlDoc(node) : "";
        foreach (var variable in node.Declaration.Variables)
        {
            var constValue = variable.Initializer?.Value.ToString() ?? "";
            yield return new FieldEvidence(
                variable.Identifier.ValueText,
                variable.Identifier.ValueText,
                "field",
                Line(variable), EndLine(variable),
                $"::{variable.Identifier.ValueText}@{Path.GetRelativePath(root, tree.FilePath).Replace('\\', '/')}",
                typeName, accessibility, isStatic, isConst, isReadonly, isVolatile,
                constValue, attributeNames, xmlDoc);
        }
    }

    private static IEnumerable<EventEvidence> BuildEvents(
        string root, SyntaxTree tree, SemanticModel? model, EventFieldDeclarationSyntax node,
        bool extractSemantic, bool extractAttributes, bool extractXmlDocs)
    {
        var symbol = model?.GetDeclaredSymbol(node);
        var isStatic = node.Modifiers.Any(SyntaxKind.StaticKeyword);
        var accessibility = symbol?.DeclaredAccessibility.ToString().ToLowerInvariant()
            ?? ExtractAccessibility(node.Modifiers);
        var attributeNames = BuildAttributeNames(model, node.AttributeLists, extractAttributes);
        var xmlDoc = extractXmlDocs ? ExtractXmlDoc(node) : "";
        var delegateType = node.Declaration.Type.ToString();
        foreach (var variable in node.Declaration.Variables)
        {
            // Plan: canonical_symbol_id uses ``<qualified_name>/0@<rel_path>``
            // so the identity indexes in the graph agree with ``resolved``
            // forms; the previous leading ``::`` made every event/delegate
            // lookup miss and abort the relation write.
            yield return new EventEvidence(
                variable.Identifier.ValueText,
                variable.Identifier.ValueText,
                Line(variable), EndLine(variable),
                $"{variable.Identifier.ValueText}/0@{Path.GetRelativePath(root, tree.FilePath).Replace('\\', '/')}",
                delegateType, accessibility, isStatic, false,
                attributeNames, xmlDoc);
        }
    }

    private static DelegateEvidence BuildDelegate(
        string root, SyntaxTree tree, SemanticModel? model, DelegateDeclarationSyntax node,
        bool extractSemantic, bool extractAttributes, bool extractXmlDocs)
    {
        var symbol = model?.GetDeclaredSymbol(node);
        var attributes = BuildAttributeNames(model, node.AttributeLists, extractAttributes);
        var typeParameters = node.TypeParameterList?.Parameters
            .Select(tp => tp.Identifier.ValueText)
            .ToArray() ?? Array.Empty<string>();
        var returnType = node.ReturnType.ToString();
        var parameters = BuildParameters(model, node.ParameterList.Parameters, null);
        return new DelegateEvidence(
            node.Identifier.ValueText,
            symbol?.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat) ?? node.Identifier.ValueText,
            Line(node), EndLine(node),
            $"{node.Identifier.ValueText}/0@{Path.GetRelativePath(root, tree.FilePath).Replace('\\', '/')}",
            returnType, typeParameters,
            symbol?.DeclaredAccessibility.ToString().ToLowerInvariant() ?? ExtractAccessibility(node.Modifiers),
            attributes, parameters,
            extractXmlDocs ? ExtractXmlDoc(node) : "");
    }

    private static IReadOnlyList<string> BuildAttributeNames(
        SemanticModel? model,
        SyntaxList<AttributeListSyntax> attributeLists,
        bool extractAttributes)
    {
        if (!extractAttributes) return Array.Empty<string>();
        return attributeLists.SelectMany(al => al.Attributes)
            .Select(a => Display(model?.GetSymbolInfo(a).Symbol?.ContainingType) ?? a.Name.ToString())
            .OrderBy(a => a, StringComparer.Ordinal)
            .ToArray();
    }

    private static ParameterEvidence[] BuildParameters(
        SemanticModel? model,
        SeparatedSyntaxList<ParameterSyntax> parameters,
        List<ParameterEvidence>? collected)
    {
        var result = new List<ParameterEvidence>();
        foreach (var param in parameters)
        {
            var paramEvidence = new ParameterEvidence(
                param.Identifier.ValueText,
                param.Type?.ToString() ?? "",
                param.Default is not null,
                param.Default?.Value.ToString() ?? "",
                param.Modifiers.Any(SyntaxKind.ParamsKeyword),
                param.Modifiers.Any(SyntaxKind.RefKeyword),
                param.Modifiers.Any(SyntaxKind.OutKeyword),
                param.Modifiers.Any(SyntaxKind.InKeyword));
            result.Add(paramEvidence);
            collected?.Add(paramEvidence);
        }
        return result.ToArray();
    }

    private static UsingEvidence BuildUsing(UsingDirectiveSyntax node)
    {
        var alias = node.Alias?.Name?.Identifier.ValueText;
        return new UsingEvidence(
            node.Name?.ToString() ?? "",
            alias,
            node.StaticKeyword != default);
    }

    private static AttributeEvidence BuildAttribute(string root, SyntaxTree tree, SemanticModel? model, AttributeSyntax node)
    {
        var attributeType = model?.GetSymbolInfo(node).Symbol?.ContainingType;
        var arguments = node.ArgumentList?.Arguments
            .Select(a => a.Expression.ToString())
            .ToArray() ?? Array.Empty<string>();
        return new AttributeEvidence(
            node.Name.ToString(),
            Display(attributeType),
            Line(node),
            arguments);
    }

    private static IReadOnlyList<InvocationEvidence> ExtractCalls(
        string root, SyntaxTree tree, SemanticModel? model, SyntaxNode syntaxRoot)
    {
        var relPath = Path.GetRelativePath(root, tree.FilePath).Replace('\\', '/');
        var result = new List<InvocationEvidence>();
        foreach (var invocation in syntaxRoot.DescendantNodes().OfType<InvocationExpressionSyntax>())
        {
            var arguments = invocation.ArgumentList.Arguments
                .Select(a => a.Expression.ToString())
                .ToArray();
            var caller = invocation.Ancestors().OfType<MethodDeclarationSyntax>().FirstOrDefault()
                ?? (SyntaxNode?)invocation.Ancestors().OfType<ConstructorDeclarationSyntax>().FirstOrDefault();
            var callerId = "";
            if (caller is MethodDeclarationSyntax m)
            {
                var sym = model?.GetDeclaredSymbol(m);
                callerId = CanonicalMemberId(root, tree, sym, m.Identifier.ValueText);
            }
            else if (caller is ConstructorDeclarationSyntax c)
            {
                var sym = model?.GetDeclaredSymbol(c);
                callerId = CanonicalMemberId(root, tree, sym, c.Identifier.ValueText);
            }

            var methodSymbol = model?.GetSymbolInfo(invocation).Symbol as IMethodSymbol;
            if (methodSymbol is null)
            {
                result.Add(new InvocationEvidence(
                    invocation.Expression.ToString(), callerId, Line(invocation),
                    null, null, null, false, false, false, arguments));
            }
            else
            {
                var resolvedId = CanonicalMemberId(root, tree, methodSymbol, methodSymbol.Name);
                var resolvedName = methodSymbol.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat);
                var isAsync = methodSymbol.ReturnType is INamedTypeSymbol nt
                    && nt.ToDisplayString().StartsWith("System.Threading.Tasks.Task", StringComparison.Ordinal);
                var isVirtualDispatch = methodSymbol.IsVirtual || methodSymbol.IsAbstract || methodSymbol.IsOverride;
                result.Add(new InvocationEvidence(
                    invocation.Expression.ToString(), callerId, Line(invocation),
                    resolvedId, resolvedName, methodSymbol.Parameters.Length,
                    true, isAsync, isVirtualDispatch, arguments));
            }
        }

        return result
            .OrderBy(item => item.StartLine)
            .ThenBy(item => item.Expression, StringComparer.Ordinal)
            .ToArray();
    }

    private static string BuildParseMeta(SemanticModel? model, IReadOnlyList<WorkerDiagnostic> diagnostics)
    {
        var hasError = diagnostics.Any(d => d.Severity == "error");
        var errorNodes = diagnostics.Count(d => d.Severity == "error");
        var mode = model is null ? "syntax" : "roslyn";
        return $"parser={mode};errors={errorNodes};has_error={hasError.ToString().ToLowerInvariant()}";
    }

    private static string ExtractXmlDoc(SyntaxNode node)
    {
        var trivias = node.GetLeadingTrivia()
            .Where(t => t.IsKind(SyntaxKind.SingleLineDocumentationCommentTrivia))
            .Select(t => t.ToString());
        return string.Join("\n", trivias).Trim();
    }

    private static string ExtractAccessibility(SyntaxTokenList modifiers)
    {
        foreach (var token in modifiers)
        {
            if (token.IsKind(SyntaxKind.PublicKeyword)) return "public";
            if (token.IsKind(SyntaxKind.PrivateKeyword)) return "private";
            if (token.IsKind(SyntaxKind.ProtectedKeyword)) return "protected";
            if (token.IsKind(SyntaxKind.InternalKeyword)) return "internal";
            if (token.IsKind(SyntaxKind.FileKeyword)) return "private";
        }
        return "";
    }

    private static string CanonicalTypeId(INamedTypeSymbol symbol)
    {
        var typeNames = new Stack<string>();
        for (INamedTypeSymbol? current = symbol; current is not null; current = current.ContainingType)
        {
            typeNames.Push(current.Name);
        }
        var ns = symbol.ContainingNamespace?.IsGlobalNamespace == false
            ? symbol.ContainingNamespace.ToDisplayString()
            : "";
        return string.Join("::", string.IsNullOrEmpty(ns) ? typeNames : new[] { ns }.Concat(typeNames));
    }

    private static string CanonicalMemberId(string root, SyntaxTree tree, ISymbol? symbol, string sourceName)
    {
        if (symbol is null) return "";
        var relPath = Path.GetRelativePath(root, tree.FilePath).Replace('\\', '/');
        if (symbol is IMethodSymbol method && method.ContainingType is not null)
        {
            var containing = method.ContainingType.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat);
            return $"{containing}::{sourceName}/{method.Parameters.Length}@{relPath}";
        }
        if (symbol is IPropertySymbol property && property.ContainingType is not null)
        {
            var containing = property.ContainingType.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat);
            return $"{containing}::{sourceName}/{property.Parameters.Length}@{relPath}";
        }
        if (symbol is IFieldSymbol field && field.ContainingType is not null)
        {
            var containing = field.ContainingType.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat);
            return $"{containing}::{sourceName}@{relPath}";
        }
        if (symbol is IEventSymbol evt && evt.ContainingType is not null)
        {
            var containing = evt.ContainingType.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat);
            return $"{containing}::{sourceName}@{relPath}";
        }
        if (symbol.ContainingType is not null)
        {
            var containing = symbol.ContainingType.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat);
            return $"{containing}::{sourceName}@{relPath}";
        }
        return $"{sourceName}@{relPath}";
    }

    private static string? Display(ISymbol? symbol)
        => symbol?.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat);

    private static string DetectTypeKind(BaseTypeDeclarationSyntax node)
    {
        if (node is ClassDeclarationSyntax)
            return node.Modifiers.Any(SyntaxKind.StaticKeyword) ? "static_class" : "class";
        if (node is RecordDeclarationSyntax r)
            return r.ClassOrStructKeyword.IsKind(SyntaxKind.StructKeyword) ? "record_struct" : "record";
        if (node is StructDeclarationSyntax) return "struct";
        if (node is InterfaceDeclarationSyntax) return "interface";
        if (node is EnumDeclarationSyntax) return "enum";
        return "type";
    }

    private static int Line(SyntaxNode node)
        => node.GetLocation().GetLineSpan().StartLinePosition.Line + 1;

    private static int EndLine(SyntaxNode node)
        => node.GetLocation().GetLineSpan().EndLinePosition.Line + 1;

    private static string Qualified(string namespace_name, string name)
        => string.IsNullOrEmpty(namespace_name) ? name : $"{namespace_name}.{name}";
}
