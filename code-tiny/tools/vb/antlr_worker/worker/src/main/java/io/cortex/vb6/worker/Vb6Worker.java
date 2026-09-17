/*
 * vb6-antlr-worker: whole-program VB6 ASG worker (plan 260917-1200).
 *
 * Protocol (mirrors roslyn_worker/Program.cs conventions):
 *   argv: --manifest <path> [--workspace-timeout-ms <n>] [--parse-cache-version <v>]
 *   manifest JSON:
 *     {
 *       "root": "<abs project root>",
 *       "project": "Sample.vbp",            // relative to root, may be ""
 *       "files": [ {"file_path": "a.bas"},
 *                  {"file_path": "frm.frm", "parse_path": "/tmp/x/frm.cls"} ]
 *     }
 *   file_path is the reporting identity (relative to root, forward slashes);
 *   parse_path overrides where the worker reads the content from (adapter
 *   materializes .frm/.ctl/.pag into temp .cls before invoking the worker).
 *
 *   stdout: ONE JSON document:
 *     {"files": [{"file_path": ..., "ok": true, "payload": {...}} |
 *                {"file_path": ..., "ok": false, "error": "..."}],
 *      "worker_meta": {...}}
 */

package io.cortex.vb6.worker;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.antlr.v4.runtime.BaseErrorListener;
import org.antlr.v4.runtime.CharStreams;
import org.antlr.v4.runtime.CommonTokenStream;
import org.antlr.v4.runtime.RecognitionException;
import org.antlr.v4.runtime.Recognizer;
import org.antlr.v4.runtime.ParserRuleContext;
import org.antlr.v4.runtime.tree.ParseTree;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import io.proleap.vb6.VisualBasic6Lexer;
import io.proleap.vb6.VisualBasic6Parser;
import io.proleap.vb6.asg.metamodel.ClazzModule;
import io.proleap.vb6.asg.metamodel.Module;
import io.proleap.vb6.asg.metamodel.Procedure;
import io.proleap.vb6.asg.metamodel.Program;
import io.proleap.vb6.asg.metamodel.StandardModule;
import io.proleap.vb6.asg.metamodel.VisibilityEnum;
import io.proleap.vb6.asg.metamodel.call.Call;
import io.proleap.vb6.asg.metamodel.call.MembersCall;
import io.proleap.vb6.asg.metamodel.statement.function.Function;
import io.proleap.vb6.asg.metamodel.statement.property.get.PropertyGet;
import io.proleap.vb6.asg.metamodel.statement.property.let.PropertyLet;
import io.proleap.vb6.asg.metamodel.statement.property.set.PropertySet;
import io.proleap.vb6.asg.metamodel.statement.sub.Sub;
import io.proleap.vb6.asg.params.VbParserParams;
import io.proleap.vb6.asg.params.impl.VbParserParamsImpl;
import io.proleap.vb6.asg.runner.VbParserRunner;
import io.proleap.vb6.asg.runner.impl.VbParserRunnerImpl;

public final class Vb6Worker {

	private static final Pattern VB_NAME_PATTERN = Pattern.compile(
			"^\\s*Attribute\\s+VB_Name\\s*=\\s*\"([^\"]+)\"", Pattern.CASE_INSENSITIVE);
	private static final Pattern IMPLEMENTS_PATTERN = Pattern.compile(
			"^\\s*Implements\\s+([A-Za-z_][A-Za-z0-9_]*)", Pattern.CASE_INSENSITIVE);

	/** Call types that reference procedures/properties and belong in the call graph. */
	private static final Map<String, Boolean> CALL_TYPES_EMITTED = new HashMap<>();

	static {
		for (String kind : new String[] {
				"SUB_CALL", "FUNCTION_CALL", "PROPERTY_GET_CALL", "PROPERTY_LET_CALL",
				"PROPERTY_SET_CALL", "UNDEFINED_CALL", "API_PROCEDURE_CALL", "API_PROPERTY_CALL" }) {
			CALL_TYPES_EMITTED.put(kind, Boolean.TRUE);
		}
	}

	private static final Gson GSON = new GsonBuilder().disableHtmlEscaping().serializeNulls().create();

	/** File identity from the manifest: reporting path + optional parse path. */
	static final class FileEntry {
		final String filePath;
		final String parsePath;
		String moduleName;      // Attribute VB_Name or filename stem
		String content;         // content of parsePath
		String originalContent; // content of root/filePath (for file_def)
		int syntaxErrors;

		FileEntry(final String filePath, final String parsePath) {
			this.filePath = filePath;
			this.parsePath = parsePath;
		}
	}

	public static void main(final String[] args) throws Exception {
		String manifestPath = null;
		long workspaceTimeoutMs = 120_000L;
		String parseCacheVersion = "";
		for (int i = 0; i < args.length; i++) {
			final String arg = args[i];
			switch (arg) {
				case "--manifest":
					manifestPath = args[++i];
					break;
				case "--workspace-timeout-ms":
					workspaceTimeoutMs = Long.parseLong(args[++i]);
					break;
				case "--parse-cache-version":
					parseCacheVersion = args[++i];
					break;
				default:
					break;
			}
		}
		if (manifestPath == null) {
			System.err.println("usage: Vb6Worker --manifest <path> [--workspace-timeout-ms n]");
			System.exit(2);
		}

		final long startedAt = System.currentTimeMillis();
		final JsonObject manifest = JsonParser.parseString(
				Files.readString(new File(manifestPath).toPath(), StandardCharsets.UTF_8)).getAsJsonObject();
		final String root = str(manifest, "root", ".");
		final String project = str(manifest, "project", "");
		final String cacheVersion = parseCacheVersion.isEmpty()
				? str(manifest, "parse_cache_version", "")
				: parseCacheVersion;

		final List<FileEntry> entries = new ArrayList<>();
		for (final JsonElement element : manifest.getAsJsonArray("files")) {
			final JsonObject fileObj = element.getAsJsonObject();
			final String filePath = fileObj.get("file_path").getAsString();
			final String parsePath = fileObj.has("parse_path")
					? fileObj.get("parse_path").getAsString()
					: null;
			entries.add(new FileEntry(filePath, parsePath));
		}

		final ExecutorService executor = Executors.newSingleThreadExecutor();
		final Future<JsonElement> future = executor.submit(
				(Callable<JsonElement>) () -> analyze(root, project, cacheVersion, entries, startedAt));
		JsonElement output;
		try {
			output = future.get(workspaceTimeoutMs, TimeUnit.MILLISECONDS);
		} catch (final TimeoutException timeout) {
			future.cancel(true);
			output = allFailed(entries, "timeout after " + workspaceTimeoutMs + "ms");
		} catch (final Exception failure) {
			output = allFailed(entries, "worker failure: " + rootMessage(failure));
		} finally {
			executor.shutdownNow();
		}
		System.out.println(GSON.toJson(output));
	}

	// ------------------------------------------------------------------
	// analysis
	// ------------------------------------------------------------------

	private static JsonElement allFailed(final List<FileEntry> entries, final String error) {
		final JsonArray files = new JsonArray();
		for (final FileEntry entry : entries) {
			final JsonObject item = new JsonObject();
			item.addProperty("file_path", entry.filePath);
			item.addProperty("ok", false);
			item.addProperty("error", error);
			files.add(item);
		}
		final JsonObject result = new JsonObject();
		result.add("files", files);
		final JsonObject meta = new JsonObject();
		meta.addProperty("workspace_kind", "vbp");
		meta.addProperty("batch_error", error);
		result.add("worker_meta", meta);
		return result;
	}

	private static JsonElement analyze(
			final String root,
			final String project,
			final String cacheVersion,
			final List<FileEntry> entries,
			final long startedAt) throws IOException {

		final Map<String, FileEntry> entriesByModuleName = new LinkedHashMap<>();

		// 1. load content + declared module names
		final java.util.Set<String> duplicateModuleNames = new java.util.HashSet<>();
		for (final FileEntry entry : entries) {
			final File parseFile = entry.parsePath != null
					? new File(entry.parsePath)
					: new File(root, entry.filePath);
			entry.content = Files.readString(parseFile.toPath(), StandardCharsets.UTF_8);
			final File originalFile = new File(root, entry.filePath);
			entry.originalContent = originalFile.isFile()
					? Files.readString(originalFile.toPath(), StandardCharsets.UTF_8)
					: entry.content;
			entry.moduleName = declaredModuleName(entry.content, entry.filePath);
			entry.syntaxErrors = countSyntaxErrors(entry.content);
			final String moduleKey = entry.moduleName.toLowerCase(Locale.ROOT);
			if (entriesByModuleName.containsKey(moduleKey)) {
				duplicateModuleNames.add(moduleKey);
			}
			entriesByModuleName.put(moduleKey, entry);
		}

		// 2. whole-program batch: EVERY file goes in (AD-02) so cross-module
		// resolution sees the complete project, regardless of parse cache state.
		List<File> batch = new ArrayList<>();
		final Map<FileEntry, File> parseFileByEntry = new LinkedHashMap<>();
		for (final FileEntry entry : entries) {
			final File file = entry.parsePath != null
					? new File(entry.parsePath)
					: new File(root, entry.filePath);
			parseFileByEntry.put(entry, file);
			batch.add(file);
		}

		final VbParserParams params = new VbParserParamsImpl();
		params.setIgnoreSyntaxErrors(true);

		final VbParserRunner runner = new VbParserRunnerImpl();
		Program program;
		try {
			program = runner.analyzeFiles(batch, params);
		} catch (final Throwable batchFailure) {
			// A single fatal file must not sink the batch: retry without the
			// files that failed pre-validation; those report ok=false.
			final List<FileEntry> excluded = new ArrayList<>();
			final List<File> retry = new ArrayList<>();
			for (final FileEntry entry : entries) {
				if (entry.syntaxErrors > 0) {
					excluded.add(entry);
				} else {
					retry.add(parseFileByEntry.get(entry));
				}
			}
			program = runner.analyzeFiles(retry, params);
			for (final FileEntry entry : excluded) {
				entry.syntaxErrors = -1; // marker: excluded from batch
			}
		}

		// 3. implements map (module name -> interface names), for Interface
		// emission (AD-10) and IMPLEMENTS edges (python registry, phase 04).
		final Map<String, List<String>> implementsMap = new LinkedHashMap<>();
		for (final Module module : program.getModules()) {
			final List<String> targets = new ArrayList<>();
			for (final String line : module.getLines()) {
				final Matcher matcher = IMPLEMENTS_PATTERN.matcher(line);
				if (matcher.find()) {
					targets.add(matcher.group(1));
				}
			}
			if (!targets.isEmpty()) {
				implementsMap.put(module.getName(), targets);
			}
		}
		final Map<String, Boolean> implementsTargets = new HashMap<>();
		for (final List<String> targets : implementsMap.values()) {
			for (final String target : targets) {
				implementsTargets.put(target.toLowerCase(Locale.ROOT), Boolean.TRUE);
			}
		}

		// 4. procedure -> symbol_id registry (callee ids may live in other files)
		final Map<Procedure, String> procedureIds = new LinkedHashMap<>();
		for (final Module module : program.getModules()) {
			final FileEntry owner = entriesByModuleName.get(
					module.getName().toLowerCase(Locale.ROOT));
			final String relPath = owner != null ? owner.filePath : module.getName();
			for (final Procedure procedure : module.getProcedures()) {
				procedureIds.put(procedure, symbolId(module.getName(), procedure, relPath));
			}
		}

		// known module names: bare type/module references (New X, receiver
		// module prefixes) are not procedure calls and must not become edges.
		final Map<String, Boolean> moduleNames = new HashMap<>();
		for (final Module module : program.getModules()) {
			moduleNames.put(module.getName().toLowerCase(Locale.ROOT), Boolean.TRUE);
		}

		// 5. per-file payloads
		final JsonArray filesOut = new JsonArray();
		int okCount = 0;
		for (final FileEntry entry : entries) {
			if (entry.syntaxErrors == -1) {
				filesOut.add(fileError(entry, "excluded after batch failure (syntax errors)"));
				continue;
			}
			final Module module = lookupModule(program, entry.moduleName);
			if (module == null) {
				filesOut.add(fileError(entry, entry.syntaxErrors > 0
						? "module not registered after parse (" + entry.syntaxErrors + " syntax errors)"
						: "module not registered after parse"));
				continue;
			}
			if (duplicateModuleNames.contains(entry.moduleName.toLowerCase(Locale.ROOT))) {
				filesOut.add(fileError(entry, "duplicate module name '" + entry.moduleName
						+ "' (two files share the same Attribute VB_Name)"));
				continue;
			}
			if (module.getProcedures().isEmpty() && entry.syntaxErrors > 0) {
				filesOut.add(fileError(entry, "no procedures after parse ("
						+ entry.syntaxErrors + " syntax errors)"));
				continue;
			}
			try {
				filesOut.add(filePayload(entry, module, program, procedureIds,
						implementsTargets, moduleNames, cacheVersion, project));
				okCount++;
			} catch (final Throwable payloadFailure) {
				filesOut.add(fileError(entry, "payload build failed: " + rootMessage(payloadFailure)));
			}
		}

		final JsonObject result = new JsonObject();
		result.add("files", filesOut);
		final JsonObject meta = new JsonObject();
		meta.addProperty("workspace_kind", "vbp");
		meta.addProperty("solution_or_project_path", project);
		meta.addProperty("jvm_startup_ms", jvmStartupMs(startedAt));
		meta.addProperty("elapsed_ms", System.currentTimeMillis() - startedAt);
		meta.addProperty("modules", program.getModules().size());
		meta.addProperty("files", entries.size());
		meta.addProperty("ok_files", okCount);
		meta.addProperty("failed_files", entries.size() - okCount);
		meta.add("implements_map", GSON.toJsonTree(implementsMap));
		meta.addProperty("parse_cache_version", cacheVersion);
		result.add("worker_meta", meta);
		return result;
	}

	private static Module lookupModule(final Program program, final String name) {
		final Module module = program.getModule(name);
		if (module != null) {
			return module;
		}
		final StandardModule standard = program.getStandardModule(name);
		if (standard != null) {
			return standard;
		}
		return program.getClazzModule(name);
	}

	// ------------------------------------------------------------------
	// per-file payload
	// ------------------------------------------------------------------

	private static JsonObject fileError(final FileEntry entry, final String error) {
		final JsonObject item = new JsonObject();
		item.addProperty("file_path", entry.filePath);
		item.addProperty("ok", false);
		item.addProperty("error", error);
		return item;
	}

	private static JsonObject filePayload(
			final FileEntry entry,
			final Module module,
			final Program program,
			final Map<Procedure, String> procedureIds,
			final Map<String, Boolean> implementsTargets,
			final Map<String, Boolean> moduleNames,
			final String cacheVersion,
			final String project) {

		final List<String> lines = module.getLines();
		final String relPath = entry.filePath;
		final String moduleName = module.getName();
		final boolean isClassModule = module instanceof ClazzModule;

		final JsonObject payload = new JsonObject();

		// --- functions -------------------------------------------------
		final JsonArray functions = new JsonArray();
		for (final Sub sub : module.getSubs()) {
			functions.add(procedureJson(sub, moduleName, "sub", lines, relPath));
		}
		for (final Function function : module.getFunctions()) {
			functions.add(procedureJson(function, moduleName, "function", lines, relPath));
		}
		for (final PropertyGet propertyGet : module.getPropertyGets()) {
			functions.add(procedureJson(propertyGet, moduleName, "property get", lines, relPath));
		}
		for (final PropertyLet propertyLet : module.getPropertyLets()) {
			functions.add(procedureJson(propertyLet, moduleName, "property let", lines, relPath));
		}
		for (final PropertySet propertySet : module.getPropertySets()) {
			functions.add(procedureJson(propertySet, moduleName, "property set", lines, relPath));
		}
		payload.add("functions", functions);

		// --- calls -----------------------------------------------------
		final JsonArray calls = new JsonArray();
		for (final Procedure procedure : module.getProcedures()) {
			collectProcedureCalls(module, procedure, program, procedureIds, moduleNames, calls);
		}
		collapseReceiverOnlyRows(calls);
		payload.add("calls", calls);

		// --- classes (module identity; symbol_id includes rel_path per the
		//     Class-ID collision fix) ------------------------------------
		final JsonArray classes = new JsonArray();
		if (isClassModule) {
			classes.add(typeJson(moduleName, kindForFile(entry.filePath), lines, relPath));
		}
		payload.add("classes", classes);

		// --- interfaces (AD-10: a class referenced by any Implements is
		//     additionally an Interface target for IMPLEMENTS edges) ------
		final JsonArray interfaces = new JsonArray();
		if (isClassModule && implementsTargets.containsKey(moduleName.toLowerCase(Locale.ROOT))) {
			interfaces.add(typeJson(moduleName, "interface", lines, relPath));
		}
		payload.add("interfaces", interfaces);

		// --- empty planes ----------------------------------------------
		payload.add("namespaces", new JsonArray());
		payload.add("relations", new JsonArray());
		payload.add("properties", new JsonArray());
		payload.add("events", new JsonArray());
		payload.add("enums", new JsonArray());
		payload.add("constants", new JsonArray());

		// --- variables (module-level + procedure-local, for late-bound
		//     detection and project model) -------------------------------
		payload.add("variables", variablesJson(module, relPath));

		// --- file def --------------------------------------------------
		final String original = entry.originalContent;
		final int lineCount = original == null ? 0 : original.split("\n", -1).length;
		final JsonObject fileDef = new JsonObject();
		fileDef.addProperty("file_path", relPath);
		fileDef.addProperty("start_line", 1);
		fileDef.addProperty("end_line", Math.max(1, lineCount));
		fileDef.addProperty("code", original == null ? "" : original);
		fileDef.addProperty("comment", "");
		fileDef.addProperty("summary", "");
		fileDef.addProperty("note", "");
		fileDef.add("imports", new JsonArray());
		fileDef.add("exports", new JsonArray());
		payload.add("file_def", fileDef);

		// --- parse meta ------------------------------------------------
		final JsonObject parseMeta = new JsonObject();
		parseMeta.addProperty("parser_language", "vb6_antlr");
		parseMeta.addProperty("parser_engine", "antlr");
		parseMeta.addProperty("parse_cache_version", cacheVersion);
		parseMeta.addProperty("has_error", entry.syntaxErrors > 0);
		parseMeta.addProperty("error_nodes", Math.max(0, entry.syntaxErrors));
		parseMeta.addProperty("line_count", Math.max(1, lineCount));
		parseMeta.addProperty("fallback_reason", "");
		parseMeta.addProperty("worker_elapsed_ms", 0);
		parseMeta.addProperty("workspace_kind", "vbp");
		parseMeta.addProperty("solution_or_project_path", project);
		parseMeta.addProperty("semantic_mode", "off");
		parseMeta.addProperty("semantic_enabled", false);
		parseMeta.add("semantic_errors", new JsonArray());
		parseMeta.addProperty("resolution_source", "asg");
		parseMeta.addProperty("requested_engine", "antlr");
		parseMeta.addProperty("module_name", moduleName);
		final JsonArray implemented = new JsonArray();
		for (final Procedure procedure : module.getProcedures()) {
			// no-op; procedures enumerated above
		}
		final List<String> ownImplements = implementsNames(lines);
		for (final String name : ownImplements) {
			implemented.add(name);
		}
		parseMeta.add("implements", implemented);
		payload.add("parse_meta", parseMeta);
		payload.addProperty("parse_cache_version", cacheVersion);

		final JsonObject item = new JsonObject();
		item.addProperty("file_path", relPath);
		item.addProperty("ok", true);
		item.add("payload", payload);
		return item;
	}

	// ------------------------------------------------------------------
	// procedures / calls / variables
	// ------------------------------------------------------------------

	private static JsonObject procedureJson(
			final Procedure procedure,
			final String moduleName,
			final String kind,
			final List<String> lines,
			final String relPath) {
		final int startLine = ctxLine(procedure.getCtx(), true);
		final int endLine = Math.max(startLine, ctxLine(procedure.getCtx(), false));
		final String code = sliceLines(lines, startLine, endLine);
		final int arity = procedure.getArgsList() == null ? 0 : procedure.getArgsList().size();
		final boolean isPrivate = procedure.getVisibility() == VisibilityEnum.PRIVATE;
		final String qualified = moduleName + "." + procedure.getName();

		final JsonObject json = new JsonObject();
		json.addProperty("symbol_id", qualified + "/" + arity + "@" + relPath);
		json.addProperty("qualified_name", qualified);
		json.addProperty("name", procedure.getName());
		json.addProperty("kind", kind);
		json.add("class_name", com.google.gson.JsonNull.INSTANCE);
		json.add("namespace_name", com.google.gson.JsonNull.INSTANCE);
		json.addProperty("file_path", relPath);
		json.addProperty("start_line", startLine);
		json.addProperty("end_line", endLine);
		json.addProperty("arity", arity);
		json.addProperty("code", code);
		json.addProperty("comment", "");
		json.addProperty("summary", "");
		json.addProperty("note", "");
		json.addProperty("module_name", moduleName);
		json.addProperty("is_private", isPrivate);
		return json;
	}

	private static String symbolId(final String moduleName, final Procedure procedure, final String relPath) {
		final int arity = procedure.getArgsList() == null ? 0 : procedure.getArgsList().size();
		return moduleName + "." + procedure.getName() + "/" + arity + "@" + relPath;
	}

	/**
	 * Collect outgoing call edges for one procedure by walking the module parse
	 * tree and resolving each call ctx through the ASG registry. This catches
	 * both resolved calls (SubCall/FunctionCall/PropertyCall with a bound
	 * target) and UNDEFINED calls, which have no target-side holder in the ASG.
	 */
	private static void collectProcedureCalls(
			final Module module,
			final Procedure procedure,
			final Program program,
			final Map<Procedure, String> procedureIds,
			final Map<String, Boolean> moduleNames,
			final JsonArray out) {

		final ParserRuleContext procedureCtx = procedure.getCtx();
		final String moduleName = module.getName();
		final String callerId = procedureIds.get(procedure);
		if (callerId == null) {
			return;
		}
		final ASGElementRegistryLike registry = registryOf(program);
		final java.util.Set<String> seen = new java.util.HashSet<>();
		final List<ParseTree> stack = new ArrayList<>();
		stack.add(procedureCtx);
		while (!stack.isEmpty()) {
			final ParseTree node = stack.remove(stack.size() - 1);
			if (node instanceof ParserRuleContext) {
				final ParserRuleContext ctx = (ParserRuleContext) node;
				final Object element = registry.lookup(ctx);
				// concrete calls only: CallDelegate wrappers re-visit the same
				// underlying call from their own context and would duplicate
				if (element instanceof Call && ((Call) element).unwrap() == element) {
					emitCall((Call) element, ctx, procedure, callerId, moduleName,
							program, procedureIds, moduleNames, seen, out);
				}
			}
			for (int i = node.getChildCount() - 1; i >= 0; i--) {
				stack.add(node.getChild(i));
			}
		}
	}

	/** Minimal indirection over ProLeap's ASGElementRegistry. */
	private interface ASGElementRegistryLike {
		Object lookup(ParserRuleContext ctx);
	}

	private static ASGElementRegistryLike registryOf(final Program program) {
		return ctx -> program.getASGElementRegistry().getASGElement(ctx);
	}

	private static void emitCall(
			final Call call,
			final ParserRuleContext ctx,
			final Procedure caller,
			final String callerId,
			final String moduleName,
			final Program program,
			final Map<Procedure, String> procedureIds,
			final Map<String, Boolean> moduleNames,
			final java.util.Set<String> seen,
			final JsonArray out) {

		final Call unwrapped = call;
		if (unwrapped instanceof MembersCall) {
			// aggregate wrapper: its leaf sub-calls are emitted from their own
			// contexts during the walk; skip the wrapper itself.
			return;
		}
		final Call.CallType callType = unwrapped.getCallType();
		if (callType == null || !CALL_TYPES_EMITTED.containsKey(callType.name())) {
			return;
		}

		// enclosing procedure check: nested procedures do not exist in VB6,
		// but calls registered at module level (outside any procedure) must
		// not be attributed to this caller.
		final Procedure enclosing = unwrapped.findScope(Procedure.class);
		if (enclosing == null || enclosing != caller) {
			return;
		}

		final String memberName = unwrapped.getName() == null ? "" : unwrapped.getName();
		if (memberName.isEmpty()) {
			return;
		}
		// bare references to modules/classes (New clsOrder, receiver prefixes)
		// are not procedure calls
		if (moduleNames.containsKey(memberName.toLowerCase(Locale.ROOT))) {
			return;
		}
		final String display = callDisplayName(unwrapped, ctx, memberName);
		final int line = ctxLine(ctx, true);
		final int column = ctx.getStart() == null ? 0 : ctx.getStart().getCharPositionInLine() + 1;
		final String dedupKey = line + "|" + callType.name() + "|" + memberName.toLowerCase(Locale.ROOT)
				+ "|" + (calleeIdOf(unwrapped) == null ? "" : calleeIdOf(unwrapped));
		if (!seen.add(dedupKey)) {
			return;
		}

		String calleeId = null;
		Integer calleeArity = null;
		String resolutionStatus;
		switch (callType) {
			case SUB_CALL: {
				final io.proleap.vb6.asg.metamodel.call.SubCall subCall =
						(io.proleap.vb6.asg.metamodel.call.SubCall) unwrapped;
				final Sub target = subCall.getSub();
				if (target != null) {
					calleeId = procedureIds.get(target);
					calleeArity = target.getArgsList() == null ? 0 : target.getArgsList().size();
					resolutionStatus = "asg_resolved";
				} else {
					resolutionStatus = "undefined";
				}
				break;
			}
			case FUNCTION_CALL: {
				final io.proleap.vb6.asg.metamodel.call.FunctionCall functionCall =
						(io.proleap.vb6.asg.metamodel.call.FunctionCall) unwrapped;
				final Function target = functionCall.getFunction();
				if (target != null) {
					calleeId = procedureIds.get(target);
					calleeArity = target.getArgsList() == null ? 0 : target.getArgsList().size();
					resolutionStatus = "asg_resolved";
				} else {
					resolutionStatus = "undefined";
				}
				break;
			}
			case PROPERTY_GET_CALL: {
				final io.proleap.vb6.asg.metamodel.call.PropertyGetCall propertyCall =
						(io.proleap.vb6.asg.metamodel.call.PropertyGetCall) unwrapped;
				final PropertyGet target = propertyCall.getPropertyGet();
				if (target != null) {
					calleeId = procedureIds.get(target);
					calleeArity = target.getArgsList() == null ? 0 : target.getArgsList().size();
					resolutionStatus = "asg_resolved";
				} else {
					resolutionStatus = "undefined";
				}
				break;
			}
			case PROPERTY_LET_CALL: {
				final io.proleap.vb6.asg.metamodel.call.PropertyLetCall propertyCall =
						(io.proleap.vb6.asg.metamodel.call.PropertyLetCall) unwrapped;
				final PropertyLet target = propertyCall.getPropertyLet();
				if (target != null) {
					calleeId = procedureIds.get(target);
					calleeArity = target.getArgsList() == null ? 0 : target.getArgsList().size();
					resolutionStatus = "asg_resolved";
				} else {
					resolutionStatus = "undefined";
				}
				break;
			}
			case PROPERTY_SET_CALL: {
				final io.proleap.vb6.asg.metamodel.call.PropertySetCall propertyCall =
						(io.proleap.vb6.asg.metamodel.call.PropertySetCall) unwrapped;
				final PropertySet target = propertyCall.getPropertySet();
				if (target != null) {
					calleeId = procedureIds.get(target);
					calleeArity = target.getArgsList() == null ? 0 : target.getArgsList().size();
					resolutionStatus = "asg_resolved";
				} else {
					resolutionStatus = "undefined";
				}
				break;
			}
			case API_PROCEDURE_CALL:
			case API_PROPERTY_CALL:
				resolutionStatus = "external";
				break;
			default:
				resolutionStatus = "undefined";
				break;
		}
		if (calleeId == null && "asg_resolved".equals(resolutionStatus)) {
			resolutionStatus = "undefined";
		}

		final JsonObject row = new JsonObject();
		row.addProperty("caller_id", callerId);
		row.addProperty("caller_scope", moduleName);
		row.addProperty("callee_name", display.isEmpty() ? memberName : display);
		if (calleeId == null) {
			row.add("callee_id", com.google.gson.JsonNull.INSTANCE);
		} else {
			row.addProperty("callee_id", calleeId);
		}
		if (calleeArity == null) {
			row.add("callee_arity", com.google.gson.JsonNull.INSTANCE);
		} else {
			row.addProperty("callee_arity", calleeArity);
		}
		row.addProperty("call_line", line);
		row.addProperty("site_column", column);
		row.addProperty("call_type", callType.name().toLowerCase(Locale.ROOT));
		row.addProperty("resolution_status", resolutionStatus);
		row.addProperty("callee_member", memberName);
		out.add(row);
	}

	private static Call unwrap(final Call call) {
		Call current = call;
		for (int depth = 0; depth < 8 && current != null; depth++) {
			final Call next = current.unwrap();
			if (next == null || next == current) {
				break;
			}
			current = next;
		}
		return current == null ? call : current;
	}

	/**
	 * Human-facing call name: for member calls the full chain text
	 * ("x.LateBound", "ord.Total", ".ProcessOrder" inside With); for simple
	 * calls the bare identifier.
	 */
	private static String callDisplayName(final Call call, final ParserRuleContext ctx, final String memberName) {
		String raw = null;
		ParseTree parent = ctx.getParent();
		while (parent != null) {
			if (parent instanceof VisualBasic6Parser.ICS_S_MembersCallContext
					|| parent instanceof VisualBasic6Parser.ECS_MemberProcedureCallContext) {
				raw = parent.getText();
				break;
			}
			parent = parent.getParent();
		}
		if (raw == null) {
			raw = ctx.getText();
		}
		String cleaned = stripArgs(sanitizeDisplay(raw), memberName);
		if (cleaned.length() > memberName.length()
				&& cleaned.substring(0, 4).equalsIgnoreCase("Call")) {
			cleaned = cleaned.substring(4);
		}
		return cleaned;
	}

	/**
	 * The ANTLR ctx text includes the argument list ("DoSomething1,2",
	 * "CalcTotal(3,4)"). Cut the display text at the last occurrence of the
	 * member identifier so only the receiver chain and the member remain
	 * ("DoSomething", "modUtil.CalcTotal").
	 */
	private static String stripArgs(final String display, final String memberName) {
		if (display == null || display.isEmpty()) {
			return memberName;
		}
		final int index = display.toLowerCase(Locale.ROOT).lastIndexOf(
				memberName.toLowerCase(Locale.ROOT));
		if (index < 0) {
			return memberName;
		}
		return display.substring(0, index) + memberName;
	}

	private static String calleeIdOf(final Call call) {
		if (call instanceof io.proleap.vb6.asg.metamodel.call.SubCall) {
			final Sub sub = ((io.proleap.vb6.asg.metamodel.call.SubCall) call).getSub();
			return sub == null ? null : sub.getName();
		}
		if (call instanceof io.proleap.vb6.asg.metamodel.call.FunctionCall) {
			final Function function = ((io.proleap.vb6.asg.metamodel.call.FunctionCall) call).getFunction();
			return function == null ? null : function.getName();
		}
		return null;
	}

	/**
	 * A member call line also yields a bare receiver row (e.g. "Debug" next to
	 * "Debug.Print"). The receiver is not a procedure call: drop it.
	 */
	private static void collapseReceiverOnlyRows(final JsonArray calls) {
		final List<JsonObject> rows = new ArrayList<>();
		for (final com.google.gson.JsonElement element : calls) {
			rows.add(element.getAsJsonObject());
		}
		final List<JsonObject> drop = new ArrayList<>();
		for (final JsonObject row : rows) {
			final String name = row.has("callee_name") ? row.get("callee_name").getAsString() : "";
			if (name.isEmpty() || name.contains(".")) {
				continue;
			}
			final boolean undefined = row.has("resolution_status")
					&& "undefined".equals(row.get("resolution_status").getAsString());
			if (!undefined) {
				continue;
			}
			final int line = row.has("call_line") ? row.get("call_line").getAsInt() : -1;
			for (final JsonObject other : rows) {
				if (other == row) {
					continue;
				}
				final String otherName = other.has("callee_name")
						? other.get("callee_name").getAsString() : "";
				final int otherLine = other.has("call_line") ? other.get("call_line").getAsInt() : -2;
				if (otherLine == line && otherName.toLowerCase(Locale.ROOT).startsWith(
						name.toLowerCase(Locale.ROOT) + ".")) {
					drop.add(row);
					break;
				}
			}
		}
		for (final JsonObject row : drop) {
			calls.remove(row);
		}
	}

	private static String sanitizeDisplay(final String text) {
		if (text == null) {
			return "";
		}
		String cleaned = text.replace("\r", "").replace("\n", "").replace(" ", "");
		if (cleaned.length() > 200) {
			cleaned = cleaned.substring(0, 200);
		}
		return cleaned;
	}

	private static JsonArray variablesJson(final Module module, final String relPath) {
		final JsonArray variables = new JsonArray();
		final String moduleName = module.getName();

		for (final io.proleap.vb6.asg.metamodel.Variable variable : module.getVariables()) {
			variables.add(variableJson(variable, moduleName, null, relPath));
		}
		for (final Procedure procedure : module.getProcedures()) {
			for (final io.proleap.vb6.asg.metamodel.Variable variable : procedure.getVariables()) {
				variables.add(variableJson(variable, moduleName, procedure.getName(), relPath));
			}
		}
		return variables;
	}

	private static JsonObject variableJson(
			final io.proleap.vb6.asg.metamodel.Variable variable,
			final String moduleName,
			final String procedureName,
			final String relPath) {
		final String name = variable.getName() == null ? "" : variable.getName();
		String typeName = "";
		try {
			if (variable.getType() != null && variable.getType().getName() != null) {
				typeName = variable.getType().getName();
			}
		} catch (final Throwable ignored) {
			typeName = "";
		}
		final boolean moduleLevel = procedureName == null;
		final boolean isPublic = variable.getVisibility() == VisibilityEnum.PUBLIC;
		final String qualified = moduleLevel
				? moduleName + "." + name
				: moduleName + "." + procedureName + "." + name;
		final int line = ctxLine(variable.getCtx(), true);
		final String code = variable.getCtx() == null ? name : variable.getCtx().getText();

		final JsonObject json = new JsonObject();
		json.addProperty("symbol_id", qualified + "@" + relPath);
		json.addProperty("qualified_name", qualified);
		json.addProperty("name", name);
		json.addProperty("type_name", typeName);
		json.addProperty("is_global", moduleLevel && isPublic);
		json.addProperty("is_shared", false);
		json.add("class_name", com.google.gson.JsonNull.INSTANCE);
		json.add("namespace_name", com.google.gson.JsonNull.INSTANCE);
		json.addProperty("file_path", relPath);
		json.addProperty("line_number", line);
		json.addProperty("code", code);
		json.addProperty("comment", "");
		json.addProperty("summary", "");
		json.addProperty("note", "");
		json.addProperty("module_name", moduleName);
		if (procedureName != null) {
			json.addProperty("procedure_name", procedureName);
		}
		return json;
	}

	private static JsonObject typeJson(
			final String name,
			final String kind,
			final List<String> lines,
			final String relPath) {
		final JsonObject json = new JsonObject();
		final String idPrefix = "interface".equals(kind) ? "interface::" : "";
		json.addProperty("symbol_id", idPrefix + name + "@" + relPath);
		json.addProperty("qualified_name", name);
		json.addProperty("name", name);
		json.addProperty("kind", kind);
		json.add("namespace_name", com.google.gson.JsonNull.INSTANCE);
		json.addProperty("file_path", relPath);
		json.addProperty("start_line", 1);
		json.addProperty("end_line", Math.max(1, lines == null ? 1 : lines.size()));
		json.addProperty("code", joinLines(lines));
		json.addProperty("comment", "");
		json.addProperty("summary", "");
		json.addProperty("note", "");
		if ("interface".equals(kind)) {
			json.add("base_interfaces", new JsonArray());
		}
		return json;
	}

	// ------------------------------------------------------------------
	// helpers
	// ------------------------------------------------------------------

	private static String kindForFile(final String filePath) {
		final String lower = filePath.toLowerCase(Locale.ROOT);
		if (lower.endsWith(".frm") || lower.endsWith(".ctl") || lower.endsWith(".pag")) {
			return "form";
		}
		return "class";
	}

	private static String declaredModuleName(final String content, final String filePath) {
		final String[] lines = content.split("\n", -1);
		for (int i = 0; i < Math.min(lines.length, 60); i++) {
			final Matcher matcher = VB_NAME_PATTERN.matcher(lines[i]);
			if (matcher.find()) {
				return matcher.group(1);
			}
		}
		final String base = filePath.contains("/")
				? filePath.substring(filePath.lastIndexOf('/') + 1)
				: filePath;
		final int dot = base.lastIndexOf('.');
		final String stem = dot > 0 ? base.substring(0, dot) : base;
		return stem.isEmpty() ? base : stem;
	}

	private static List<String> implementsNames(final List<String> lines) {
		final List<String> names = new ArrayList<>();
		if (lines == null) {
			return names;
		}
		for (final String line : lines) {
			final Matcher matcher = IMPLEMENTS_PATTERN.matcher(line);
			if (matcher.find()) {
				names.add(matcher.group(1));
			}
		}
		return names;
	}

	private static int countSyntaxErrors(final String content) {
		try {
			final VisualBasic6Lexer lexer = new VisualBasic6Lexer(CharStreams.fromString(content));
			lexer.removeErrorListeners();
			final CommonTokenStream tokens = new CommonTokenStream(lexer);
			final VisualBasic6Parser parser = new VisualBasic6Parser(tokens);
			parser.removeErrorListeners();
			final int[] errors = {0};
			parser.addErrorListener(new BaseErrorListener() {
				@Override
				public void syntaxError(
						final Recognizer<?, ?> recognizer,
						final Object offendingSymbol,
						final int line,
						final int charPositionInLine,
						final String msg,
						final RecognitionException e) {
					errors[0]++;
				}
			});
			parser.startRule();
			return errors[0];
		} catch (final Throwable failure) {
			return 1;
		}
	}

	private static int ctxLine(final ParserRuleContext ctx, final boolean start) {
		if (ctx == null) {
			return 1;
		}
		if (start) {
			return ctx.getStart() == null ? 1 : ctx.getStart().getLine();
		}
		return ctx.getStop() == null ? 1 : ctx.getStop().getLine();
	}

	private static String sliceLines(final List<String> lines, final int startLine, final int endLine) {
		if (lines == null || lines.isEmpty()) {
			return "";
		}
		final int start = Math.max(1, startLine);
		final int end = Math.min(lines.size(), Math.max(start, endLine));
		final StringBuilder builder = new StringBuilder();
		for (int i = start - 1; i < end; i++) {
			if (builder.length() > 0) {
				builder.append('\n');
			}
			builder.append(lines.get(i));
		}
		return builder.toString();
	}

	private static String joinLines(final List<String> lines) {
		if (lines == null || lines.isEmpty()) {
			return "";
		}
		return String.join("\n", lines);
	}

	private static String str(final JsonObject object, final String key, final String fallback) {
		if (object.has(key) && object.get(key).isJsonPrimitive()) {
			return object.get(key).getAsString();
		}
		return fallback;
	}

	private static String rootMessage(final Throwable failure) {
		Throwable current = failure;
		for (int depth = 0; depth < 6 && current != null; depth++) {
			if (current.getMessage() != null && !current.getMessage().isEmpty()) {
				final String text = current.getMessage();
				return text.length() > 400 ? text.substring(0, 400) : text;
			}
			current = current.getCause();
		}
		return failure.getClass().getSimpleName();
	}

	private static long jvmStartupMs(final long startedAt) {
		return Math.max(0, startedAt - VM_START);
	}

	private static final long VM_START = System.currentTimeMillis();

	private Vb6Worker() {
	}
}
