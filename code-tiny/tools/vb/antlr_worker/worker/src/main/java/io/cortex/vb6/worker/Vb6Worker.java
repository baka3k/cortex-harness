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
import org.antlr.v4.runtime.Token;
import org.antlr.v4.runtime.misc.Interval;
import org.antlr.v4.runtime.tree.ParseTree;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import io.proleap.vb6.VisualBasic6Lexer;
import io.proleap.vb6.VisualBasic6Parser;
import io.proleap.vb6.asg.metamodel.Arg;
import io.proleap.vb6.asg.metamodel.Attribute;
import io.proleap.vb6.asg.metamodel.ClazzModule;
import io.proleap.vb6.asg.metamodel.Module;
import io.proleap.vb6.asg.metamodel.Procedure;
import io.proleap.vb6.asg.metamodel.Program;
import io.proleap.vb6.asg.metamodel.StandardModule;
import io.proleap.vb6.asg.metamodel.VisibilityEnum;
import io.proleap.vb6.asg.metamodel.call.Call;
import io.proleap.vb6.asg.metamodel.call.MembersCall;
import io.proleap.vb6.asg.metamodel.statement.constant.Constant;
import io.proleap.vb6.asg.metamodel.statement.enumeration.Enumeration;
import io.proleap.vb6.asg.metamodel.statement.enumeration.EnumerationConstant;
import io.proleap.vb6.asg.metamodel.statement.event.Event;
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
		boolean designerStripped; // adapter blanked the designer block (strip fallback)

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
			final FileEntry entry = new FileEntry(filePath, parsePath);
			entry.designerStripped = fileObj.has("designer_stripped")
					&& fileObj.get("designer_stripped").getAsBoolean();
			entries.add(entry);
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
		final boolean isDesignerModule = kindForFile(relPath).equals("form");
		final CommentIndex comments = CommentIndex.of(module);

		final JsonObject payload = new JsonObject();

		// --- functions -------------------------------------------------
		final JsonArray functions = new JsonArray();
		for (final Sub sub : module.getSubs()) {
			functions.add(procedureJson(sub, moduleName, "sub", lines, relPath, comments));
		}
		for (final Function function : module.getFunctions()) {
			functions.add(procedureJson(function, moduleName, "function", lines, relPath, comments));
		}
		for (final PropertyGet propertyGet : module.getPropertyGets()) {
			functions.add(procedureJson(propertyGet, moduleName, "property get", lines, relPath, comments));
		}
		for (final PropertyLet propertyLet : module.getPropertyLets()) {
			functions.add(procedureJson(propertyLet, moduleName, "property let", lines, relPath, comments));
		}
		for (final PropertySet propertySet : module.getPropertySets()) {
			functions.add(procedureJson(propertySet, moduleName, "property set", lines, relPath, comments));
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
			final JsonObject classJson = typeJson(moduleName, kindForFile(relPath), lines, relPath);
			final String classComment = comments.forDeclaration(1);
			classJson.addProperty("comment", classComment);
			classJson.addProperty("summary", classComment);
			classJson.addProperty("note", buildNote(classJson.get("code").getAsString(), classComment, ""));
			classes.add(classJson);
		}
		payload.add("classes", classes);

		// --- interfaces (AD-10: a class referenced by any Implements is
		//     additionally an Interface target for IMPLEMENTS edges) ------
		final JsonArray interfaces = new JsonArray();
		if (isClassModule && implementsTargets.containsKey(moduleName.toLowerCase(Locale.ROOT))) {
			interfaces.add(typeJson(moduleName, "interface", lines, relPath));
		}
		payload.add("interfaces", interfaces);

		// --- empty planes (namespaces/relations out of scope) -----------
		payload.add("namespaces", new JsonArray());
		payload.add("relations", new JsonArray());
		payload.add("properties", new JsonArray()); // ANTLR represents properties as functions (kind property get/let/set)

		// --- hydrated planes (plan 260917-1628 phase 01) ----------------
		payload.add("events", eventsJson(module, lines, relPath, comments));
		payload.add("enums", enumsJson(module, lines, relPath, comments));
		payload.add("constants", constantsJson(module, lines, relPath, comments));
		payload.add("declares", declaresJson(module, moduleName, lines, relPath, comments));

		// --- controls[] (phase 02: designer files only; parse-tree walk) --
		payload.add("controls", isDesignerModule ? controlsJson(module) : new JsonArray());

		// --- variables (module-level + procedure-local, for late-bound
		//     detection and project model) -------------------------------
		payload.add("variables", variablesJson(module, relPath));

		// --- anchor planes (plan 260924 phase 02): New / With targets /
		//     member+state access / ReDim — pure parse-tree ctx walks per
		//     procedure (no extra parse pass). Every row carries the owner
		//     `proc` (red-team H4) and leaves receiver/New targets RAW for
		//     the Python resolver (phase 03) -------------------------------
		final java.util.Set<String> stateNames = programStateNames(program);
		final JsonArray instantiations = new JsonArray();
		final JsonArray withTargets = new JsonArray();
		final JsonArray uiAccess = new JsonArray();
		final JsonArray redims = new JsonArray();
		for (final Procedure procedure : module.getProcedures()) {
			collectAnchorPlanes(procedure, stateNames, instantiations, withTargets, uiAccess, redims);
		}
		payload.add("instantiations", instantiations);
		payload.add("with_targets", withTargets);
		payload.add("ui_access", uiAccess);
		payload.add("redim", redims);

		// --- file def --------------------------------------------------
		final String original = entry.originalContent;
		final int lineCount = original == null ? 0 : original.split("\n", -1).length;
		final JsonObject fileDef = new JsonObject();
		fileDef.addProperty("file_path", relPath);
		fileDef.addProperty("start_line", 1);
		fileDef.addProperty("end_line", Math.max(1, lineCount));
		fileDef.addProperty("code", original == null ? "" : original);
		fileDef.addProperty("comment", comments.forDeclaration(1));
		fileDef.addProperty("summary", entry.designerStripped
				? "Designer block stripped (VB6_ANTLR_STRIP_DESIGNER=1); control tree unavailable."
				: "");
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
		parseMeta.addProperty("designer_stripped", entry.designerStripped);
		parseMeta.addProperty("declares_regex_support", false);
		parseMeta.add("module_attributes", moduleAttributesJson(module, program));
		final JsonArray implemented = new JsonArray();
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
			final String relPath,
			final CommentIndex comments) {
		final int startLine = ctxLine(procedure.getCtx(), true);
		final int endLine = Math.max(startLine, ctxLine(procedure.getCtx(), false));
		final String code = sliceLines(lines, startLine, endLine);
		final int arity = procedure.getArgsList() == null ? 0 : procedure.getArgsList().size();
		final ArityInfo arityInfo = arityInfo(procedure);
		final boolean isPrivate = procedure.getVisibility() == VisibilityEnum.PRIVATE;
		final String qualified = moduleName + "." + procedure.getName();
		final String comment = comments.forDeclaration(startLine);

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
		json.addProperty("min_arity", arityInfo.minArity);
		json.addProperty("has_optional_args", arityInfo.hasOptionalArgs);
		json.addProperty("has_paramarray", arityInfo.hasParamArray);
		// anchor graph (plan 260924): declared parameter + return types feed
		// USES_TYPE publication and COM receiver classification (resolver P3)
		json.add("param_types", paramTypesJson(procedure));
		json.add("param_names", paramNamesJson(procedure));
		json.addProperty("return_type", returnTypeOf(procedure));
		json.addProperty("code", code);
		json.addProperty("comment", comment);
		json.addProperty("summary", comment);
		json.addProperty("note", buildNote(code, comment, ""));
		json.addProperty("module_name", moduleName);
		json.addProperty("is_private", isPrivate);
		return json;
	}

	/** min_arity excludes Optional args AND the ParamArray arg (red-team F6). */
	private static ArityInfo arityInfo(final Procedure procedure) {
		final ArityInfo info = new ArityInfo();
		if (procedure.getArgsList() == null) {
			return info;
		}
		for (final Arg arg : procedure.getArgsList()) {
			final boolean paramArray = arg.getCtx() != null && arg.getCtx().PARAMARRAY() != null;
			if (paramArray) {
				info.hasParamArray = true;
				continue;
			}
			if (arg.isOptional()) {
				info.hasOptionalArgs = true;
				continue;
			}
			info.minArity++;
		}
		return info;
	}

	private static final class ArityInfo {
		int minArity;
		boolean hasOptionalArgs;
		boolean hasParamArray;
	}

	/** Declared parameter types ("" when the arg carries no As clause). */
	private static JsonArray paramTypesJson(final Procedure procedure) {
		final JsonArray paramTypes = new JsonArray();
		if (procedure.getArgsList() == null) {
			return paramTypes;
		}
		for (final Arg arg : procedure.getArgsList()) {
			String typeName = "";
			if (arg.getCtx() != null && arg.getCtx().asTypeClause() != null) {
				typeName = stripAsPrefix(arg.getCtx().asTypeClause().getText());
			}
			paramTypes.add(typeName);
		}
		return paramTypes;
	}

	/** Parameter NAMES aligned with {@link #paramTypesJson} (typed-receiver lookup). */
	private static JsonArray paramNamesJson(final Procedure procedure) {
		final JsonArray paramNames = new JsonArray();
		if (procedure.getArgsList() == null) {
			return paramNames;
		}
		for (final Arg arg : procedure.getArgsList()) {
			paramNames.add(arg.getName() == null ? "" : arg.getName());
		}
		return paramNames;
	}

	/** Declared return type of Function/Property Get ("" otherwise). */
	private static String returnTypeOf(final Procedure procedure) {
		final ParserRuleContext ctx = procedure.getCtx();
		if (ctx instanceof VisualBasic6Parser.FunctionStmtContext
				&& ((VisualBasic6Parser.FunctionStmtContext) ctx).asTypeClause() != null) {
			return stripAsPrefix(((VisualBasic6Parser.FunctionStmtContext) ctx).asTypeClause().getText());
		}
		if (ctx instanceof VisualBasic6Parser.PropertyGetStmtContext
				&& ((VisualBasic6Parser.PropertyGetStmtContext) ctx).asTypeClause() != null) {
			return stripAsPrefix(((VisualBasic6Parser.PropertyGetStmtContext) ctx).asTypeClause().getText());
		}
		return "";
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
				if (ctx instanceof VisualBasic6Parser.DictionaryCallStmtContext) {
					// default-member access (obj!Field): ProLeap leaves these
					// UNREGISTERED in the ASG registry (spike 1.1), so emit
					// straight from the parse ctx — same dedup/collapse
					// pipeline as registry-bound rows (red-team F17)
					emitDictionaryCall((VisualBasic6Parser.DictionaryCallStmtContext) ctx,
							callerId, moduleName, seen, out);
					continue;
				}
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

	/**
	 * Dictionary (default-member) call row from its parse ctx. The receiver
	 * chain comes from the enclosing iCS_S_MembersCall text ("rs!FieldName");
	 * the member is the bare identifier after '!'.
	 */
	private static void emitDictionaryCall(
			final VisualBasic6Parser.DictionaryCallStmtContext ctx,
			final String callerId,
			final String moduleName,
			final java.util.Set<String> seen,
			final JsonArray out) {

		if (ctx.ambiguousIdentifier() == null) {
			return;
		}
		final String memberName = ctx.ambiguousIdentifier().getText();
		if (memberName == null || memberName.isEmpty()) {
			return;
		}
		String display = memberName;
		String fallback = null;   // receiver-less chain text ("!Field.Count")
		int bestLength = Integer.MAX_VALUE;
		ParseTree parent = ctx.getParent();
		while (parent instanceof ParserRuleContext) {
			final String text = ((ParserRuleContext) parent).getText();
			final String lower = text.toLowerCase(Locale.ROOT);
			if (lower.contains("!" + memberName.toLowerCase(Locale.ROOT))
					&& text.length() < bestLength) {
				bestLength = text.length();
				if (text.startsWith("!")) {
					fallback = text;
				} else {
					display = text;
					break; // smallest ancestor carrying the receiver chain
				}
			}
			if (!lower.contains("!") && !lower.endsWith(memberName.toLowerCase(Locale.ROOT))) {
				break; // left the access chain entirely
			}
			parent = parent.getParent();
		}
		if (display.equals(memberName) && fallback != null) {
			display = fallback;
		}
		final int line = ctxLine(ctx, true);
		final int column = ctx.getStart() == null ? 0 : ctx.getStart().getCharPositionInLine() + 1;
		// dedup on the full display: rs!A and rs2!A on one line are two sites
		final String dedupKey = line + "|DICTIONARY_CALL|" + display.toLowerCase(Locale.ROOT)
				+ "|" + column;
		if (!seen.add(dedupKey)) {
			return;
		}

		final JsonObject row = new JsonObject();
		row.addProperty("caller_id", callerId);
		row.addProperty("caller_scope", moduleName);
		row.addProperty("callee_name", display);
		row.add("callee_id", com.google.gson.JsonNull.INSTANCE);
		row.add("callee_arity", com.google.gson.JsonNull.INSTANCE);
		row.addProperty("call_line", line);
		row.addProperty("site_column", column);
		row.addProperty("call_type", "dictionary_call");
		row.addProperty("resolution_status", "undefined");
		row.addProperty("callee_member", memberName);
		row.addProperty("default_member", true);
		out.add(row);
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
				&& cleaned.length() >= 4
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

	// ------------------------------------------------------------------
	// anchor planes (plan 260924 phase 02)
	// ------------------------------------------------------------------

	/**
	 * Module-level variable + constant names across the WHOLE program (S1/S2
	 * evidence): bare identifiers matching these are state anchors. The set is
	 * what keeps bare-name ui_access rows precise — without it every local
	 * variable and builtin would flood the plane.
	 */
	private static java.util.Set<String> programStateNames(final Program program) {
		final java.util.Set<String> names = new java.util.HashSet<>();
		for (final Module module : program.getModules()) {
			for (final io.proleap.vb6.asg.metamodel.Variable variable : module.getVariables()) {
				if (variable.getName() != null) {
					names.add(variable.getName().toLowerCase(Locale.ROOT));
				}
			}
			for (final Constant constant : module.getConstants()) {
				if (constant.getName() != null) {
					names.add(constant.getName().toLowerCase(Locale.ROOT));
				}
			}
		}
		return names;
	}

	/**
	 * Walk one procedure's parse tree and fill the anchor planes:
	 *
	 * - instantiations[]: `Set x = New X`, `Dim x As New X`, `With New X`
	 *   (vsNew contexts + asTypeClause NEW + WithStmt NEW) → {proc, name,
	 *   line, call_type:"NEW"} (AD-02: New X is NOT a procedure call — the
	 *   moduleNames filter of the calls walk no longer hides it)
	 * - with_targets[]: `With <EXPR>` → {proc, expr_raw, line,
	 *   block_end_line} — block_end_line is what lets the resolver attach
	 *   member rows to the INNERMOST nested block (red-team H4)
	 * - ui_access[]: member chains (`txt.Text`, `.SetFocus`, `obj.Method 1`)
	 *   → {proc, receiver_raw, member, access:read|write, via_with, line};
	 *   plus bare module-state references (`AppStatus = 1`, reads of
	 *   `MAX_LOGIN_TRIES`) → same shape with member:"" — receiver_raw holds
	 *   the state name. Names are RAW; classification happens in Python.
	 * - redim[]: `ReDim [Preserve] arr(...)` per redimSubStmt → {proc, name,
	 *   preserve, line} (g4:461-463)
	 */
	private static void collectAnchorPlanes(
			final Procedure procedure,
			final java.util.Set<String> stateNames,
			final JsonArray instantiations,
			final JsonArray withTargets,
			final JsonArray uiAccess,
			final JsonArray redims) {

		final String procName = procedure.getName() == null ? "" : procedure.getName();
		if (procedure.getCtx() == null) {
			return;
		}
		final java.util.Set<String> seenUi = new java.util.HashSet<>();
		final List<ParseTree> stack = new ArrayList<>();
		stack.add(procedure.getCtx());
		while (!stack.isEmpty()) {
			final ParseTree node = stack.remove(stack.size() - 1);
			if (node instanceof ParserRuleContext) {
				final ParserRuleContext ctx = (ParserRuleContext) node;
				if (ctx instanceof VisualBasic6Parser.VsNewContext) {
					emitInstantiation(((VisualBasic6Parser.VsNewContext) ctx).valueStmt(),
							procName, instantiations);
					// the New target is a type reference, not a member access —
					// descending would emit an `ADODB.Recordset` chain row
					continue;
				} else if (ctx instanceof VisualBasic6Parser.AsTypeClauseContext) {
					final VisualBasic6Parser.AsTypeClauseContext asType =
							(VisualBasic6Parser.AsTypeClauseContext) ctx;
					if (asType.NEW() != null) {
						emitInstantiation(asType.type(), procName, instantiations);
						continue;
					}
				} else if (ctx instanceof VisualBasic6Parser.WithStmtContext) {
					final VisualBasic6Parser.WithStmtContext with =
							(VisualBasic6Parser.WithStmtContext) ctx;
					final JsonObject row = new JsonObject();
					row.addProperty("proc", procName);
					row.addProperty("expr_raw", with.implicitCallStmt_InStmt() == null
							? "" : sanitizeDisplay(with.implicitCallStmt_InStmt().getText()));
					row.addProperty("is_new", with.NEW() != null);
					row.addProperty("line", ctxLine(with, true));
					row.addProperty("block_end_line", ctxLine(with, false));
					withTargets.add(row);
					if (with.NEW() != null) {
						emitInstantiation(with.implicitCallStmt_InStmt(), procName, instantiations);
					}
				} else if (ctx instanceof VisualBasic6Parser.RedimStmtContext) {
					final VisualBasic6Parser.RedimStmtContext redim =
							(VisualBasic6Parser.RedimStmtContext) ctx;
					final boolean preserve = redim.PRESERVE() != null;
					for (final VisualBasic6Parser.RedimSubStmtContext sub : redim.redimSubStmt()) {
						final JsonObject row = new JsonObject();
						row.addProperty("proc", procName);
						row.addProperty("name", sub.implicitCallStmt_InStmt() == null
								? "" : sub.implicitCallStmt_InStmt().getText());
						row.addProperty("preserve", preserve);
						row.addProperty("line", ctxLine(sub, true));
						redims.add(row);
					}
				} else if (ctx instanceof VisualBasic6Parser.ICS_S_MembersCallContext) {
					emitMembersAccess((VisualBasic6Parser.ICS_S_MembersCallContext) ctx,
							procName, seenUi, uiAccess);
				} else if (ctx instanceof VisualBasic6Parser.ICS_B_MemberProcedureCallContext
						|| ctx instanceof VisualBasic6Parser.ECS_MemberProcedureCallContext) {
					// statement-position member procedure calls (`obj.Method`,
					// `obj.Method args`, `Call obj.Method args`; g4 iCS_B_/
					// ecsMemberProcedureCall). Receiver stays raw (`a.b` chain
					// or empty for dot-prefixed With members).
					final String receiver;
					final String member;
					if (ctx instanceof VisualBasic6Parser.ICS_B_MemberProcedureCallContext) {
						final VisualBasic6Parser.ICS_B_MemberProcedureCallContext icsB =
								(VisualBasic6Parser.ICS_B_MemberProcedureCallContext) ctx;
						receiver = icsB.implicitCallStmt_InStmt() == null
								? "" : sanitizeDisplay(icsB.implicitCallStmt_InStmt().getText());
						member = icsB.ambiguousIdentifier() == null
								? "" : icsB.ambiguousIdentifier().getText();
					} else {
						final VisualBasic6Parser.ECS_MemberProcedureCallContext ecs =
								(VisualBasic6Parser.ECS_MemberProcedureCallContext) ctx;
						receiver = ecs.ambiguousIdentifier() == null
								? "" : ecs.ambiguousIdentifier().getText();
						member = ecs.implicitCallStmt_InStmt() == null
								? "" : sanitizeDisplay(ecs.implicitCallStmt_InStmt().getText());
					}
					String memberName = member;
					final int paren = memberName.indexOf('(');
					if (paren >= 0) {
						memberName = memberName.substring(0, paren);
					}
					final int lastDot = memberName.lastIndexOf('.');
					memberName = lastDot >= 0 ? memberName.substring(lastDot + 1) : memberName;
					memberName = memberName.trim();
					if (!memberName.isEmpty()) {
						emitUiAccess(procName, receiver, memberName,
								assignmentSide(ctx), receiver.isEmpty(),
								ctxLine(ctx, true), seenUi, uiAccess);
					}
				} else if (ctx instanceof VisualBasic6Parser.ICS_S_VariableOrProcedureCallContext
						&& ctx.getParent() instanceof VisualBasic6Parser.ImplicitCallStmt_InStmtContext) {
					// bare atomic reference: a state anchor ONLY when the name
					// matches a program-wide module-level variable/constant
					final String name = ctx.getText();
					if (stateNames.contains(name.toLowerCase(Locale.ROOT))) {
						emitUiAccess(procName, name, "", assignmentSide(ctx),
								false, ctxLine(ctx, true), seenUi, uiAccess);
					}
				}
			}
			for (int i = node.getChildCount() - 1; i >= 0; i--) {
				stack.add(node.getChild(i));
			}
		}
	}

	private static void emitInstantiation(
			final ParserRuleContext typeCtx,
			final String procName,
			final JsonArray out) {
		if (typeCtx == null) {
			return;
		}
		final String name = sanitizeDisplay(typeCtx.getText());
		if (name.isEmpty()) {
			return;
		}
		final JsonObject row = new JsonObject();
		row.addProperty("proc", procName);
		row.addProperty("name", name);
		row.addProperty("line", ctxLine(typeCtx, true));
		row.addProperty("call_type", "NEW");
		out.add(row);
	}

	/** `a.b.c` chain: receiver = first atomic part, member = last member name. */
	private static void emitMembersAccess(
			final VisualBasic6Parser.ICS_S_MembersCallContext ctx,
			final String procName,
			final java.util.Set<String> seenUi,
			final JsonArray out) {
		final List<VisualBasic6Parser.ICS_S_MemberCallContext> members = ctx.iCS_S_MemberCall();
		if (members.isEmpty()) {
			return; // dictionary-only chain — covered by the calls plane
		}
		String receiver = "";
		final ParseTree first = ctx.getChildCount() > 0 ? ctx.getChild(0) : null;
		if (first instanceof VisualBasic6Parser.ICS_S_VariableOrProcedureCallContext
				|| first instanceof VisualBasic6Parser.ICS_S_ProcedureOrArrayCallContext) {
			receiver = first.getText();
		}
		final VisualBasic6Parser.ICS_S_MemberCallContext last = members.get(members.size() - 1);
		final String member = memberNameOf(last);
		if (member.isEmpty()) {
			return;
		}
		emitUiAccess(procName, receiver, member, assignmentSide(ctx),
				receiver.isEmpty(), ctxLine(ctx, true), seenUi, out);
	}

	/** Inner identifier of a member link (".Text" → "Text", args stripped). */
	private static String memberNameOf(final VisualBasic6Parser.ICS_S_MemberCallContext member) {
		if (member.iCS_S_VariableOrProcedureCall() != null) {
			return member.iCS_S_VariableOrProcedureCall().getText();
		}
		if (member.iCS_S_ProcedureOrArrayCall() != null
				&& member.iCS_S_ProcedureOrArrayCall().ambiguousIdentifier() != null) {
			return member.iCS_S_ProcedureOrArrayCall().ambiguousIdentifier().getText();
		}
		return "";
	}

	/**
	 * write when the ctx is the assignment TARGET of an enclosing Let/Set
	 * statement (`x.y = 1`, `Set x = ...`), read otherwise.
	 */
	private static String assignmentSide(final ParserRuleContext ctx) {
		ParserRuleContext current = ctx;
		ParserRuleContext parent = ctx.getParent();
		while (parent instanceof ParserRuleContext) {
			if (parent instanceof VisualBasic6Parser.LetStmtContext) {
				return current == ((VisualBasic6Parser.LetStmtContext) parent).implicitCallStmt_InStmt()
						? "write" : "read";
			}
			if (parent instanceof VisualBasic6Parser.SetStmtContext) {
				return current == ((VisualBasic6Parser.SetStmtContext) parent).implicitCallStmt_InStmt()
						? "write" : "read";
			}
			current = parent;
			parent = parent.getParent();
		}
		return "read";
	}

	private static void emitUiAccess(
			final String procName,
			final String receiverRaw,
			final String member,
			final String access,
			final boolean viaWith,
			final int line,
			final java.util.Set<String> seenUi,
			final JsonArray out) {
		final String dedupKey = line + "|" + access + "|"
				+ receiverRaw.toLowerCase(Locale.ROOT) + "|" + member.toLowerCase(Locale.ROOT);
		if (!seenUi.add(dedupKey)) {
			return;
		}
		final JsonObject row = new JsonObject();
		row.addProperty("proc", procName);
		row.addProperty("receiver_raw", sanitizeDisplay(receiverRaw));
		row.addProperty("member", member);
		row.addProperty("access", access);
		row.addProperty("via_with", viaWith);
		row.addProperty("line", line);
		out.add(row);
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
		// spike S1 (plan 260924): `Global` maps to the DISTINCT VisibilityEnum
		// value GLOBAL (ScopeImpl:2403), it does not normalize to PUBLIC — the
		// flag must accept both or module-level `Global x` rows lose is_global
		final boolean isPublic = variable.getVisibility() == VisibilityEnum.PUBLIC
				|| variable.getVisibility() == VisibilityEnum.GLOBAL;
		// spike S2 (plan 260924): procedure.getVariables() holds EVERY
		// variableStmt of the procedure scope (Dim AND Static) — the
		// STATIC()/WITHEVENTS() tokens on the enclosing VariableStmtContext
		// are the only reliable discriminators
		final VisualBasic6Parser.VariableStmtContext varStmt = enclosingVariableStmt(variable);
		final boolean isStatic = varStmt != null && varStmt.STATIC() != null;
		final boolean withEvents = varStmt != null && varStmt.WITHEVENTS() != null;
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
		json.addProperty("is_static", isStatic);
		json.addProperty("with_events", withEvents);
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

	/** The `Dim|Static|visibility [WithEvents] ...` statement owning a variable. */
	private static VisualBasic6Parser.VariableStmtContext enclosingVariableStmt(
			final io.proleap.vb6.asg.metamodel.Variable variable) {
		ParserRuleContext ctx = variable.getCtx();
		while (ctx != null) {
			if (ctx instanceof VisualBasic6Parser.VariableStmtContext) {
				return (VisualBasic6Parser.VariableStmtContext) ctx;
			}
			ctx = ctx.getParent();
		}
		return null;
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
	// hydrated planes (plan 260917-1628): enums / constants / events /
	// declares / controls — shapes mirror the vb_common.py dataclasses
	// ------------------------------------------------------------------

	/** EnumDef mirror: bare-name symbol_id (`<Name>@<rel>`, red-team F2). */
	private static JsonArray enumsJson(
			final Module module,
			final List<String> lines,
			final String relPath,
			final CommentIndex comments) {
		final JsonArray enums = new JsonArray();
		final List<Enumeration> sorted = new ArrayList<>(module.getEnumerations().values());
		sorted.sort(java.util.Comparator.comparing(Enumeration::getName,
				String.CASE_INSENSITIVE_ORDER));
		for (final Enumeration enumeration : sorted) {
			final String name = enumeration.getName() == null ? "" : enumeration.getName();
			final int startLine = ctxLine(enumeration.getCtx(), true);
			final int endLine = Math.max(startLine, ctxLine(enumeration.getCtx(), false));
			final String code = sliceLines(lines, startLine, endLine);
			final String comment = comments.forDeclaration(startLine);

			final List<EnumerationConstant> members =
					new ArrayList<>(enumeration.getEnumerationConstants().values());
			members.sort(java.util.Comparator.comparingInt(EnumerationConstant::getPosition));
			final JsonArray memberRows = new JsonArray();
			for (final EnumerationConstant member : members) {
				final JsonArray pair = new JsonArray();
				pair.add(member.getName() == null ? "" : member.getName());
				String value = "";
				if (member.getCtx() != null && member.getCtx().valueStmt() != null) {
					value = member.getCtx().valueStmt().getText();
				}
				pair.add(value);
				memberRows.add(pair);
			}

			final JsonObject json = new JsonObject();
			json.addProperty("symbol_id", name + "@" + relPath);
			json.addProperty("qualified_name", name);
			json.addProperty("name", name);
			json.add("namespace_name", com.google.gson.JsonNull.INSTANCE);
			json.add("class_name", com.google.gson.JsonNull.INSTANCE);
			json.addProperty("file_path", relPath);
			json.addProperty("start_line", startLine);
			json.addProperty("end_line", endLine);
			json.add("members", memberRows);
			json.addProperty("code", code);
			json.addProperty("comment", comment);
			json.addProperty("summary", comment);
			json.addProperty("note", buildNote(code, comment, ""));
			enums.add(json);
		}
		return enums;
	}

	/** ConstantDef mirror: module-level `Const` declarations. */
	private static JsonArray constantsJson(
			final Module module,
			final List<String> lines,
			final String relPath,
			final CommentIndex comments) {
		final JsonArray constants = new JsonArray();
		final List<Constant> sorted = new ArrayList<>(module.getConstants());
		sorted.sort(java.util.Comparator.comparing(Constant::getName,
				String.CASE_INSENSITIVE_ORDER));
		for (final Constant constant : sorted) {
			final String name = constant.getName() == null ? "" : constant.getName();
			final int line = ctxLine(constant.getCtx(), true);
			final String code = sliceLines(lines, line, line);
			final String comment = comments.forDeclaration(line);
			String value = "";
			String typeName = "";
			if (constant.getCtx() != null) {
				if (constant.getCtx().valueStmt() != null) {
					value = constant.getCtx().valueStmt().getText();
				}
				if (constant.getCtx().asTypeClause() != null) {
					typeName = stripAsPrefix(constant.getCtx().asTypeClause().getText());
				}
			}

			final JsonObject json = new JsonObject();
			json.addProperty("symbol_id", name + "@" + relPath);
			json.addProperty("qualified_name", name);
			json.addProperty("name", name);
			json.addProperty("value", value);
			json.addProperty("type_name", typeName);
			json.add("class_name", com.google.gson.JsonNull.INSTANCE);
			json.add("namespace_name", com.google.gson.JsonNull.INSTANCE);
			json.addProperty("file_path", relPath);
			json.addProperty("line_number", line);
			json.addProperty("code", code);
			json.addProperty("comment", comment);
			json.addProperty("summary", comment);
			json.addProperty("note", buildNote(code, comment, ""));
			constants.add(json);
		}
		return constants;
	}

	/**
	 * EventDef mirror. Events have no Module-level ASG getter (research §4):
	 * walk the module parse tree for EventStmtContext, resolving through the
	 * registry when available (spike 1.1 verified the ctx walk).
	 */
	private static JsonArray eventsJson(
			final Module module,
			final List<String> lines,
			final String relPath,
			final CommentIndex comments) {
		final JsonArray events = new JsonArray();
		for (final VisualBasic6Parser.EventStmtContext ctx : collectCtxs(
				module.getCtx(), VisualBasic6Parser.EventStmtContext.class)) {
			final String name = ctx.ambiguousIdentifier() == null
					? "" : ctx.ambiguousIdentifier().getText();
			final int line = ctxLine(ctx, true);
			final String code = sliceLines(lines, line, line);
			final String comment = comments.forDeclaration(line);
			final String parameters = rawInnerArgList(ctx.argList());
			final boolean isPrivate = ctx.visibility() != null
					&& "Private".equalsIgnoreCase(ctx.visibility().getText());

			final JsonObject json = new JsonObject();
			json.addProperty("symbol_id", name + "@" + relPath);
			json.addProperty("qualified_name", name);
			json.addProperty("name", name);
			json.add("class_name", com.google.gson.JsonNull.INSTANCE);
			json.add("namespace_name", com.google.gson.JsonNull.INSTANCE);
			json.addProperty("file_path", relPath);
			json.addProperty("start_line", line);
			json.addProperty("end_line", line);
			json.addProperty("parameters", parameters);
			json.addProperty("is_private", isPrivate);
			json.addProperty("code", code);
			json.addProperty("comment", comment);
			json.addProperty("summary", comment);
			json.addProperty("note", buildNote(code, comment, ""));
			events.add(json);
		}
		return events;
	}

	/**
	 * Declare rows: VB6-ONLY plane (regex engine emits nothing for Declare —
	 * vb_common.py has no Declare matcher). LIB/ALIAS/return live on the
	 * DeclareStmtContext (spike 1.1 verified).
	 */
	private static JsonArray declaresJson(
			final Module module,
			final String moduleName,
			final List<String> lines,
			final String relPath,
			final CommentIndex comments) {
		final JsonArray declares = new JsonArray();
		for (final VisualBasic6Parser.DeclareStmtContext ctx : collectCtxs(
				module.getCtx(), VisualBasic6Parser.DeclareStmtContext.class)) {
			final String name = ctx.ambiguousIdentifier() == null
					? "" : ctx.ambiguousIdentifier().getText();
			final int line = ctxLine(ctx, true);
			final String code = sliceLines(lines, line, line);
			final String comment = comments.forDeclaration(line);
			final String lib = ctx.STRINGLITERAL(0) == null
					? "" : unquote(ctx.STRINGLITERAL(0).getText());
			final String alias = ctx.ALIAS() != null && ctx.STRINGLITERAL(1) != null
					? unquote(ctx.STRINGLITERAL(1).getText()) : "";
			final String returnType = ctx.asTypeClause() == null
					? "" : stripAsPrefix(ctx.asTypeClause().getText());
			final boolean isPrivate = ctx.visibility() != null
					&& "Private".equalsIgnoreCase(ctx.visibility().getText());
			final String procKind = ctx.FUNCTION() != null ? "function" : "sub";

			final JsonObject json = new JsonObject();
			json.addProperty("symbol_id", name + "@" + relPath);
			json.addProperty("qualified_name", moduleName + "." + name);
			json.addProperty("name", name);
			json.addProperty("proc_kind", procKind);
			json.addProperty("lib", lib);
			json.addProperty("alias", alias);
			json.addProperty("return_type", returnType);
			json.addProperty("is_private", isPrivate);
			json.addProperty("module_name", moduleName);
			json.addProperty("file_path", relPath);
			json.addProperty("line_number", line);
			json.addProperty("code", code);
			json.addProperty("comment", comment);
			json.addProperty("summary", comment);
			json.addProperty("note", buildNote(code, comment, ""));
			declares.add(json);
		}
		return declares;
	}

	/**
	 * controls[] from the .frm/.ctl/.pag designer block (phase 02). Controls
	 * have no ASG model — pure parse-tree walk of controlProperties (g4:97).
	 */
	private static JsonArray controlsJson(final Module module) {
		final JsonArray controls = new JsonArray();
		final VisualBasic6Parser.ControlPropertiesContext root = module.getCtx().controlProperties();
		if (root == null) {
			return controls;
		}
		collectControls(root, "", controls);
		return controls;
	}

	private static void collectControls(
			final VisualBasic6Parser.ControlPropertiesContext ctx,
			final String parentName,
			final JsonArray out) {
		final String type = ctx.cp_ControlType() == null ? "" : ctx.cp_ControlType().getText();
		String name = ctx.cp_ControlIdentifier() == null ? "" : ctx.cp_ControlIdentifier().getText();
		String index = "";
		final java.util.regex.Matcher indexMatch = CONTROL_INDEX_PATTERN.matcher(name);
		if (indexMatch.matches()) {
			name = indexMatch.group(1);
			index = indexMatch.group(2);
		}

		final JsonObject control = new JsonObject();
		control.addProperty("name", name);
		control.addProperty("type", type);
		control.addProperty("parent", parentName);
		control.addProperty("index", index);
		control.addProperty("line", ctxLine(ctx, true));
		final JsonObject properties = new JsonObject();
		control.add("properties", properties);
		out.add(control); // parents list before children
		if (ctx.cp_Properties() != null) {
			for (final VisualBasic6Parser.Cp_PropertiesContext prop : ctx.cp_Properties()) {
				if (prop.cp_SingleProperty() != null) {
					final VisualBasic6Parser.Cp_SinglePropertyContext single = prop.cp_SingleProperty();
					if (single.implicitCallStmt_InStmt() == null || single.cp_PropertyValue() == null) {
						continue;
					}
					final String key = single.implicitCallStmt_InStmt().getText();
					if (!CONTROL_PROPERTY_KEYS.contains(key.toLowerCase(Locale.ROOT))) {
						continue;
					}
					properties.addProperty(key, unquote(single.cp_PropertyValue().getText()));
				} else if (prop.controlProperties() != null) {
					// nested control (Begin VB.TextBox inside Begin VB.Frame)
					collectControls(prop.controlProperties(), name, out);
				}
				// cp_NestedProperty (BEGINPROPERTY font blocks): kept out —
				// Tab(n).Control(m) stays raw by design (plan 2.2)
			}
		}
	}

	private static final java.util.Set<String> CONTROL_PROPERTY_KEYS = new java.util.HashSet<>(
			java.util.Arrays.asList("caption", "text", "name", "index", "tabindex"));

	private static final Pattern CONTROL_INDEX_PATTERN = Pattern.compile("^(.+)\\((\\d+)\\)$");

	/** Attribute map (lowercased key → literal value; ASG literal is unquoted). */
	private static JsonObject moduleAttributesJson(final Module module, final Program program) {
		final JsonObject attrs = new JsonObject();
		final VisualBasic6Parser.ModuleAttributesContext mctx = module.getCtx().moduleAttributes();
		if (mctx == null) {
			return attrs;
		}
		final ASGElementRegistryLike registry = registryOf(program);
		for (final VisualBasic6Parser.AttributeStmtContext actx : mctx.attributeStmt()) {
			if (actx.implicitCallStmt_InStmt() == null) {
				continue;
			}
			final String key = actx.implicitCallStmt_InStmt().getText().toLowerCase(Locale.ROOT);
			String value = "";
			final Object element = registry.lookup(actx);
			if (element instanceof Attribute
					&& ((Attribute) element).getLiteral() != null
					&& ((Attribute) element).getLiteral().getValue() != null) {
				value = ((Attribute) element).getLiteral().getValue();
			} else if (actx.literal(0) != null) {
				value = unquote(actx.literal(0).getText());
			}
			attrs.addProperty(key, value);
		}
		return attrs;
	}

	// ------------------------------------------------------------------
	// comments (phase 04): hidden-channel COMMENT tokens keyed by line
	// ------------------------------------------------------------------

	/**
	 * Line-keyed index of COMMENT hidden-channel tokens from the module's own
	 * token stream. "Adjacent" = same line (trailing) or contiguous lines
	 * directly above the declaration (blank/code lines stop the run).
	 */
	static final class CommentIndex {
		private final Map<Integer, List<String>> byLine = new HashMap<>();

		static CommentIndex of(final Module module) {
			final CommentIndex index = new CommentIndex();
			final CommonTokenStream tokens = module.getTokens();
			if (tokens == null) {
				return index;
			}
			try {
				tokens.fill();
			} catch (final Throwable ignored) {
				return index;
			}
			for (final Token token : tokens.getTokens()) {
				if (token.getChannel() != Token.HIDDEN_CHANNEL
						|| token.getType() != VisualBasic6Lexer.COMMENT) {
					continue;
				}
				final String text = stripCommentMarker(token.getText());
				if (text.isEmpty()) {
					continue;
				}
				index.byLine.computeIfAbsent(token.getLine(), key -> new ArrayList<>()).add(text);
			}
			return index;
		}

		String forDeclaration(final int startLine) {
			final List<Integer> above = new ArrayList<>();
			int line = startLine - 1;
			while (byLine.containsKey(line)) {
				above.add(line);
				line--;
			}
			java.util.Collections.reverse(above);
			final List<String> parts = new ArrayList<>();
			for (final int commentLine : above) {
				parts.addAll(byLine.get(commentLine));
			}
			if (byLine.containsKey(startLine)) {
				parts.addAll(byLine.get(startLine));
			}
			return String.join("\n", parts);
		}
	}

	private static String stripCommentMarker(final String text) {
		if (text == null) {
			return "";
		}
		String cleaned = text.trim();
		if (cleaned.startsWith("'")) {
			cleaned = cleaned.substring(1).trim();
		} else if (cleaned.toLowerCase(Locale.ROOT).startsWith("rem ")) {
			cleaned = cleaned.substring(4).trim();
		} else if (cleaned.equalsIgnoreCase("rem")) {
			cleaned = "";
		}
		return cleaned;
	}

	/** Mirrors python _build_note so embedding text stays comparable. */
	private static String buildNote(final String code, final String comment, final String summary) {
		final StringBuilder builder = new StringBuilder();
		if (summary != null && !summary.isEmpty()) {
			builder.append("Summary:\n").append(summary);
		}
		if (comment != null && !comment.isEmpty()) {
			if (builder.length() > 0) {
				builder.append("\n\n");
			}
			builder.append("Comment:\n").append(comment);
		}
		if (code != null && !code.isEmpty()) {
			if (builder.length() > 0) {
				builder.append("\n\n");
			}
			builder.append("Code:\n").append(code);
		}
		return builder.toString();
	}

	/** First-level contexts of the given type anywhere under {@code root}. */
	private static <T extends ParserRuleContext> List<T> collectCtxs(
			final ParserRuleContext root,
			final Class<T> type) {
		final List<T> found = new ArrayList<>();
		if (root == null) {
			return found;
		}
		final List<ParseTree> stack = new ArrayList<>();
		stack.add(root);
		while (!stack.isEmpty()) {
			final ParseTree node = stack.remove(stack.size() - 1);
			if (type.isInstance(node)) {
				found.add(type.cast(node));
				continue; // declarations do not nest
			}
			for (int i = 0; i < node.getChildCount(); i++) {
				stack.add(node.getChild(i));
			}
		}
		return found;
	}

	/** Raw arg-list text inside the parens, whitespace-normalized. */
	private static String rawInnerArgList(final VisualBasic6Parser.ArgListContext ctx) {
		if (ctx == null || ctx.LPAREN() == null || ctx.RPAREN() == null
				|| ctx.LPAREN().getSymbol() == null || ctx.RPAREN().getSymbol() == null) {
			return "";
		}
		final int start = ctx.LPAREN().getSymbol().getStopIndex() + 1;
		final int stop = ctx.RPAREN().getSymbol().getStartIndex() - 1;
		if (stop < start) {
			return "";
		}
		try {
			final String raw = ctx.getStart().getInputStream().getText(new Interval(start, stop));
			return raw.replace("\r", " ").replace("\n", " ")
					.replaceAll("_\\s+", " ").replaceAll("\\s+", " ").trim();
		} catch (final Throwable ignored) {
			return "";
		}
	}

	private static String unquote(final String text) {
		if (text == null || text.length() < 2) {
			return text == null ? "" : text;
		}
		if (text.startsWith("\"") && text.endsWith("\"")) {
			return text.substring(1, text.length() - 1);
		}
		return text;
	}

	private static String stripAsPrefix(final String text) {
		final String cleaned = (text == null ? "" : text.trim());
		if (cleaned.toLowerCase(Locale.ROOT).startsWith("as ")) {
			return cleaned.substring(3).trim();
		}
		return cleaned;
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
		// scan the WHOLE content (plan 260917-1628 AD-02 / red-team F11): a
		// real .frm designer block routinely exceeds the old 60-line window,
		// which made the module-name fallback kick in and lost the module.
		final String[] lines = content.split("\n", -1);
		for (final String line : lines) {
			final Matcher matcher = VB_NAME_PATTERN.matcher(line);
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
