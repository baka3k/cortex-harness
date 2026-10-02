package io.cortex.vb6.worker;

import java.io.File;
import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.antlr.v4.runtime.BailErrorStrategy;
import org.antlr.v4.runtime.BaseErrorListener;
import org.antlr.v4.runtime.CharStreams;
import org.antlr.v4.runtime.CommonTokenStream;
import org.antlr.v4.runtime.Parser;
import org.antlr.v4.runtime.atn.ATNConfigSet;
import org.antlr.v4.runtime.atn.PredictionMode;
import org.antlr.v4.runtime.dfa.DFA;
import org.antlr.v4.runtime.misc.ParseCancellationException;

import io.proleap.vb6.VisualBasic6Lexer;
import io.proleap.vb6.VisualBasic6Parser;
import io.proleap.vb6.VisualBasic6Parser.StartRuleContext;
import io.proleap.vb6.asg.metamodel.Program;
import io.proleap.vb6.asg.params.VbParserParams;
import io.proleap.vb6.asg.runner.ThrowingErrorListener;
import io.proleap.vb6.asg.runner.impl.VbParserRunnerImpl;
import io.proleap.vb6.asg.visitor.ParserVisitor;
import io.proleap.vb6.asg.visitor.impl.VbModuleVisitorImpl;

/**
 * Worker-side subclass of the vendored {@link VbParserRunnerImpl}
 * (plan 261002-1410): per-file parse progress/timing on stderr and the
 * canonical ANTLR two-stage parse (SLL first, full-LL reparse on bail).
 * The vendor source stays untouched (VENDOR.md) — the visitor tail of
 * {@link #parseCode} mirrors the pinned vendor commit and must be kept in
 * sync with it.
 *
 * All instance state assumes the worker's single-threaded analyze executor;
 * the failure-retry path re-runs {@link #analyzeFiles} on the same instance
 * and its aggregates are reset so the last run wins (R4).
 */
final class WorkerRunner extends VbParserRunnerImpl {

	/**
	 * stderr filter key: every progress line carries it (R1). Three line
	 * kinds: {@code scanning i/N} (pre-pass, emitted by Vb6Worker),
	 * {@code parsing i/N} when a batch parse starts and
	 * {@code parsed i/N ... ms=<ms>} when it ends.
	 */
	static final String PROGRESS_PREFIX = "[vb6][worker]";

	/**
	 * Escape hatch (R5): VB6_WORKER_SLL=0 restores the vendor single-stage LL
	 * parse. Read once at class init.
	 */
	private static final boolean SLL_ENABLED =
			!"0".equals(System.getenv("VB6_WORKER_SLL"));

	private final int totalFiles;

	/** batch file -> reporting rel path, so progress lines match the JSON identity */
	private final Map<File, String> displayPaths;

	private int startedCount;
	private int parsedCount;
	private long parseMsTotal;
	private String slowestFile = "";
	private long slowestMs;
	private long sllFallbackFiles;
	private long sllFencedFiles;
	private boolean lastParseFellBack;
	private final Map<String, Long> parseMsByPath = new LinkedHashMap<>();

	/**
	 * LL ALL(*) on {@code module}'s chain of optional {@code NEWLINE*} sections
	 * is quadratic in a run of comment/blank lines (each comment is hidden, so
	 * the parser sees a raw newline run). Measured 2026-10-02, LL-only:
	 * 160 lines 824ms, 240 lines 1.8s, 400 lines 4.7s, 800 lines timeout;
	 * SampleForm.frm (6003-line comment run) still inside {@code closure_} at
	 * 331 CPU-s. SLL commits the newline run to the earlier rule and the
	 * payload matches LL where both finish (synthetic 240-line run, identical
	 * function line numbers). At or above this run length, never enter LL.
	 */
	static final int LL_COMMENT_RUN_LIMIT = 128;

	WorkerRunner(final int totalFiles, final Map<File, String> displayPaths) {
		this.totalFiles = totalFiles;
		this.displayPaths = displayPaths;
	}

	long parseMsFor(final File batchFile) {
		final Long ms = parseMsByPath.get(batchFile.getAbsolutePath());
		return ms == null ? 0L : ms.longValue();
	}

	long getParseMsTotal() {
		return parseMsTotal;
	}

	String getSlowestFile() {
		return slowestFile;
	}

	long getSlowestMs() {
		return slowestMs;
	}

	long getSllFallbackFiles() {
		return sllFallbackFiles;
	}

	long getSllFencedFiles() {
		return sllFencedFiles;
	}

	/** Progress numbering and aggregates restart per batch run (R4). */
	@Override
	public Program analyzeFiles(final List<File> vbFiles, final VbParserParams params) throws IOException {
		startedCount = 0;
		parsedCount = 0;
		parseMsTotal = 0;
		slowestFile = "";
		slowestMs = 0;
		sllFallbackFiles = 0;
		sllFencedFiles = 0;
		parseMsByPath.clear();
		return super.analyzeFiles(vbFiles, params);
	}

	@Override
	protected void parseFile(final File vbFile, final Program program, final VbParserParams params)
			throws IOException {
		lastParseFellBack = false;
		startedCount++;
		// emitted BEFORE the parse begins: a multi-minute ANTLR prediction on
		// one file must still name the file it is stuck on
		System.err.println(PROGRESS_PREFIX + " parsing " + startedCount + '/' + totalFiles
				+ " file=" + displayName(vbFile));
		final long startedAt = System.nanoTime();
		try {
			super.parseFile(vbFile, program, params);
		} finally {
			final long ms = Math.max(0L, (System.nanoTime() - startedAt) / 1_000_000L);
			recordTiming(vbFile, ms);
			emitProgress(vbFile, ms);
		}
	}

	private void recordTiming(final File vbFile, final long ms) {
		parseMsByPath.put(vbFile.getAbsolutePath(), Long.valueOf(ms));
		parseMsTotal += ms;
		if (ms > slowestMs) {
			slowestMs = ms;
			slowestFile = displayName(vbFile);
		}
	}

	/**
	 * One stderr line per parsed file. {@code ms} is the final anchor so the
	 * adapter can parse the line even when file names contain spaces (H1).
	 */
	private void emitProgress(final File vbFile, final long ms) {
		parsedCount++;
		final StringBuilder line = new StringBuilder();
		line.append(PROGRESS_PREFIX).append(" parsed ")
				.append(parsedCount).append('/').append(totalFiles)
				.append(" file=").append(displayName(vbFile))
				.append(" ms=").append(ms);
		if (lastParseFellBack) {
			line.append(" sll_fallback");
		}
		System.err.println(line);
	}

	private String displayName(final File vbFile) {
		final String mapped = displayPaths.get(vbFile);
		return mapped != null ? mapped : vbFile.getName();
	}

	/**
	 * Two-stage parse (AD-04): SLL + BailErrorStrategy first; on bail, the
	 * file is reparsed from scratch in full-LL mode with vendor-exact error
	 * handling, so results are identical to an LL-only run (M4).
	 * A comment/blank run at or above {@link #LL_COMMENT_RUN_LIMIT} never
	 * enters that LL stage — the newline run is the quadratic cliff.
	 */
	@Override
	protected void parseCode(final String vbCode, final String moduleName, final boolean isClazzModule,
			final boolean isStandardModule, final Program program, final VbParserParams params)
			throws IOException {

		StartRuleContext ctx;
		CommonTokenStream tokens;
		if (longestCommentOrBlankRun(vbCode) >= LL_COMMENT_RUN_LIMIT) {
			// Fence: this file's newline run is the LL cliff. Stay in SLL
			// even when VB6_WORKER_SLL=0, and do not fall back to LL on bail.
			sllFencedFiles++;
			try {
				final ParseStage stage = parseStageSll(vbCode);
				ctx = stage.ctx;
				tokens = stage.tokens;
			} catch (final ParseCancellationException bail) {
				final ParseStage stage = parseStageSllRecover(vbCode);
				ctx = stage.ctx;
				tokens = stage.tokens;
			}
		} else if (SLL_ENABLED) {
			try {
				final ParseStage stage = parseStageSll(vbCode);
				ctx = stage.ctx;
				tokens = stage.tokens;
			} catch (final ParseCancellationException bail) {
				sllFallbackFiles++;
				lastParseFellBack = true;
				final ParseStage stage = parseStageLl(vbCode, params);
				ctx = stage.ctx;
				tokens = stage.tokens;
			}
		} else {
			final ParseStage stage = parseStageLl(vbCode, params);
			ctx = stage.ctx;
			tokens = stage.tokens;
		}

		// --- mirror of vendor VbParserRunnerImpl.parseCode tail (pinned
		//     commit), keep in sync --------------------------------------
		final String declaredModuleName = analyzeDeclaredModuleName(ctx);
		final String effectiveModuleName;
		if (declaredModuleName != null && !declaredModuleName.isEmpty()) {
			effectiveModuleName = declaredModuleName;
		} else {
			effectiveModuleName = moduleName;
		}
		final List<String> lines = splitLines(vbCode);
		final ParserVisitor visitor = new VbModuleVisitorImpl(effectiveModuleName, lines, isClazzModule,
				isStandardModule, tokens, program);
		visitor.visit(ctx);
	}

	/**
	 * Shared SLL recipe (plan 261002-1511 AD-04): SLL prediction +
	 * BailErrorStrategy, plus a hard bail when SLL would escalate to
	 * full-context simulation — that escalation is exactly where ANTLR
	 * prediction blows up into unbounded {@code closure_} recursion
	 * (jstack 2026-10-02). SLL-only decisions are DFA-bounded, so an SLL
	 * pass through this helper stays sub-second and defers problematic
	 * files to the full-LL stage / a syntaxErrors=1 verdict.
	 *
	 * Package-private on purpose: Vb6Worker's pre-pass countSyntaxErrors
	 * shares this recipe so the pre-scan cannot hang either.
	 */
	static VisualBasic6Parser createSllParser(final String vbCode) {
		final VisualBasic6Lexer lexer = new VisualBasic6Lexer(CharStreams.fromString(vbCode));
		lexer.removeErrorListeners();
		final CommonTokenStream tokens = new CommonTokenStream(lexer);
		final VisualBasic6Parser parser = new VisualBasic6Parser(tokens);
		parser.getInterpreter().setPredictionMode(PredictionMode.SLL);
		parser.setErrorHandler(new BailErrorStrategy());
		parser.removeErrorListeners();
		parser.addErrorListener(new BaseErrorListener() {
			@Override
			public void reportAttemptingFullContext(final Parser recognizer, final DFA dfa,
					final int startIndex, final int stopIndex, final java.util.BitSet conflictingAlts,
					final ATNConfigSet configs) {
				throw new ParseCancellationException("sll full-context escalation");
			}
		});
		return parser;
	}

	/**
	 * SLL stage: prediction restricted to SLL and any syntax error bails via
	 * ParseCancellationException. Listeners are removed on lexer AND parser so
	 * the console never sees this stage (R1) — the LL stage owns error output.
	 */
	private ParseStage parseStageSll(final String vbCode) {
		final VisualBasic6Parser parser = createSllParser(vbCode);
		final StartRuleContext ctx = parser.startRule();
		return new ParseStage(ctx, (CommonTokenStream) parser.getInputStream());
	}

	/**
	 * SLL with the default error strategy. Used when a fenced file bails:
	 * syntax errors still yield a tree, and prediction stays off the
	 * full-context {@code closure_} path that LL would enter.
	 */
	private ParseStage parseStageSllRecover(final String vbCode) {
		final VisualBasic6Lexer lexer = new VisualBasic6Lexer(CharStreams.fromString(vbCode));
		lexer.removeErrorListeners();
		final CommonTokenStream tokens = new CommonTokenStream(lexer);
		final VisualBasic6Parser parser = new VisualBasic6Parser(tokens);
		parser.getInterpreter().setPredictionMode(PredictionMode.SLL);
		parser.removeErrorListeners();
		final StartRuleContext ctx = parser.startRule();
		return new ParseStage(ctx, tokens);
	}

	/** LL stage: vendor parity — DefaultErrorStrategy + listener handling. */
	private ParseStage parseStageLl(final String vbCode, final VbParserParams params) throws IOException {
		final VisualBasic6Lexer lexer = new VisualBasic6Lexer(CharStreams.fromString(vbCode));

		if (!params.getIgnoreSyntaxErrors()) {
			// register an error listener, so that preprocessing stops on errors
			lexer.removeErrorListeners();
			lexer.addErrorListener(new ThrowingErrorListener());
		}

		final CommonTokenStream tokens = new CommonTokenStream(lexer);
		final VisualBasic6Parser parser = new VisualBasic6Parser(tokens);

		if (!params.getIgnoreSyntaxErrors()) {
			// register an error listener, so that preprocessing stops on errors
			parser.removeErrorListeners();
			parser.addErrorListener(new ThrowingErrorListener());
		}

		final StartRuleContext ctx = parser.startRule();
		return new ParseStage(ctx, tokens);
	}

	/**
	 * Longest run of blank lines or full-line comments ({@code '} / {@code Rem}).
	 * Those lines are hidden comments plus a default-channel newline, which is
	 * what feeds the quadratic {@code NEWLINE*} decisions in {@code module}.
	 */
	static int longestCommentOrBlankRun(final String vbCode) {
		int run = 0;
		int max = 0;
		int start = 0;
		final int n = vbCode.length();
		for (int i = 0; i <= n; i++) {
			if (i == n || vbCode.charAt(i) == '\n') {
				if (isCommentOrBlankLine(vbCode, start, i)) {
					run++;
					if (run > max) {
						max = run;
					}
				} else {
					run = 0;
				}
				start = i + 1;
			}
		}
		return max;
	}

	private static boolean isCommentOrBlankLine(final String code, final int start, final int end) {
		int stop = end;
		if (stop > start && code.charAt(stop - 1) == '\r') {
			stop--;
		}
		int i = start;
		while (i < stop) {
			final char c = code.charAt(i);
			if (c != ' ' && c != '\t') {
				break;
			}
			i++;
		}
		if (i >= stop) {
			return true;
		}
		if (code.charAt(i) == '\'') {
			return true;
		}
		final int rem = stop - i;
		if (rem >= 3
				&& (code.charAt(i) == 'R' || code.charAt(i) == 'r')
				&& (code.charAt(i + 1) == 'E' || code.charAt(i + 1) == 'e')
				&& (code.charAt(i + 2) == 'M' || code.charAt(i + 2) == 'm')
				&& (rem == 3 || code.charAt(i + 3) == ' ' || code.charAt(i + 3) == '\t')) {
			return true;
		}
		return false;
	}

	private static final class ParseStage {
		final StartRuleContext ctx;
		final CommonTokenStream tokens;

		ParseStage(final StartRuleContext ctx, final CommonTokenStream tokens) {
			this.ctx = ctx;
			this.tokens = tokens;
		}
	}
}
