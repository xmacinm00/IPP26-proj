#!/usr/bin/env node
/**
 * An integration testing script for the SOL26 interpreter.
 *
 * IPP: You can implement the entire tool in this file if you wish, but it is recommended to split
 *      the code into multiple files and modules as you see fit.
 *
 *      Below, you have some code to get you started with the CLI argument parsing and logging setup,
 *      but you are **free to modify it** in whatever way you like.
 *
 * Author: Ondřej Ondryáš <iondryas@fit.vut.cz>
 *
 * AI usage notice: The author used OpenAI Codex to create the implementation of this
 *                  module based on its Python counterpart.
 */

import { existsSync, lstatSync, readdirSync, readFileSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { parseArgs } from "node:util";
import { spawn } from "node:child_process";
import { tmpdir } from "node:os";

import {
  CategoryReport,
  TestCaseDefinition,
  TestCaseReport,
  TestCaseType,
  TestReport,
  TestResult,
  UnexecutedReason,
  UnexecutedReasonCode,
} from "./models.js";

import { pino } from "pino";

const logger = pino({
  transport: {
    target: "pino-pretty",
    options: {
      colorize: true,
      destination: 2,
    },
  },
});

interface CliArguments {
  tests_dir: string;
  recursive: boolean;
  output: string | null;
  dry_run: boolean;
  include: string[] | null;
  include_category: string[] | null;
  include_test: string[] | null;
  exclude: string[] | null;
  exclude_category: string[] | null;
  exclude_test: string[] | null;
  verbose: number;
  regex_filters: boolean;
  compiler_path: string | null;
  interpreter_path: string | null;
}

function writeResult(resultReport: TestReport, outputFile: string | null): void {
  /**
   * Writes the final report to the specified output file or standard output if no file is provided.
   */
  const resultJson = JSON.stringify(resultReport, null, 2);
  if (outputFile !== null) {
    writeFileSync(outputFile, resultJson, "utf8");
    return;
  }

  console.log(resultJson);
}

const DOUBLE_LETTER_SHORT_OPTION_NORMALIZATION = new Map<string, string>([
  ["-ic", "--include-category"],
  ["-it", "--include-test"],
  ["-ec", "--exclude-category"],
  ["-et", "--exclude-test"],
]);

const HELP_TEXT = [
  "Usage:",
  "  tester [options] tests_dir",
  "",
  "Positional arguments:",
  "  tests_dir                 Path to a directory with the test cases in the SOLtest format.",
  "",
  "Options:",
  "  -h, --help                Show this help message and exit.",
  "  -r, --recursive           Recursively search for test cases in subdirectories of the provided directory.",
  "  -o, --output <path>       The output file to write the test results to. If not provided, results will be printed to standard output.",
  "  --dry-run                 Perform a dry run: discover the test cases but don't actually execute them.",
  "  -i, --include <value>     Include only test cases with the specified name or category. Can be used multiple times to specify multiple criteria.Can be combined with -ic and -it.",
  "  -ic, --include-category <value>",
  "                            Include only test cases with the specified category. Can be used multiple times to specify multiple accepted categories. Can be combined with -it and -i.",
  "  -it, --include-test <value>",
  "                            Include only test cases with the specified name. Can be used multiple times to specify multiple accepted names. Can be combined with -ic and -i.",
  "  -e, --exclude <value>     Exclude test cases with the specified name or category. Can be used multiple times to specify multiple criteria.Can be combined with -ic and -it.",
  "  -ec, --exclude-category <value>",
  "                            Exclude test cases with the specified category. Can be used multiple times to specify multiple accepted categories. Can be combined with -it and -i.",
  "  -et, --exclude-test <value>",
  "                            Exclude test cases with the specified name. Can be used multiple times to specify multiple accepted names. Can be combined with -ic and -i.",
  "  -g                        When used, the filters specified with -i[ct]/-e[ct] will be interpreted as regular expressions instead of literal strings.",
  "  -v, --verbose             Enable verbose logging output (using once = INFO level, using twice = DEBUG level).",
];

const PARSE_OPTIONS = {
  help: { type: "boolean", short: "h", default: false },
  recursive: { type: "boolean", short: "r", default: false },
  output: { type: "string", short: "o" },
  "dry-run": { type: "boolean", default: false },
  include: { type: "string", short: "i", multiple: true },
  "include-category": { type: "string", multiple: true },
  "include-test": { type: "string", multiple: true },
  exclude: { type: "string", short: "e", multiple: true },
  "exclude-category": { type: "string", multiple: true },
  "exclude-test": { type: "string", multiple: true },
  "regex-filters": { type: "boolean", short: "g", default: false },
  verbose: { type: "boolean", short: "v", multiple: true },
  compiler: { type: "string" },
  interpreter: { type: "string" },
} as const;

function normalizeArgv(argv: string[]): string[] {
  return argv.map((arg) => DOUBLE_LETTER_SHORT_OPTION_NORMALIZATION.get(arg) ?? arg);
}

function printHelp(): void {
  console.log(HELP_TEXT.join("\n"));
}

function listOrNull(values: string[] | undefined): string[] | null {
  if (values === undefined || values.length === 0) {
    return null;
  }

  return values;
}

function parseCliArgumentsRaw(argv: string[]) {
  return parseArgs({
    args: normalizeArgv(argv),
    options: PARSE_OPTIONS,
    allowPositionals: true,
    strict: true,
  } as const);
}

function parseArguments(): CliArguments {
  /**
   * Parses the command-line arguments and performs basic validation a sanitization.
   */
  let parsed: ReturnType<typeof parseCliArgumentsRaw>;

  try {
    parsed = parseCliArgumentsRaw(process.argv.slice(2));
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    console.error(message);
    process.exit(2);
  }

  const parsedValues = parsed.values;

  if (parsedValues["help"]) {
    printHelp();
    process.exit(0);
  }

  if (parsed.positionals.length !== 1 || parsed.positionals[0] === undefined) {
    console.error("Exactly one positional argument (tests_dir) is required.");
    process.exit(2);
  }

  const args: CliArguments = {
    tests_dir: resolve(parsed.positionals[0]),
    recursive: parsedValues["recursive"],
    output: parsedValues["output"] ?? null,
    dry_run: parsedValues["dry-run"],
    include: listOrNull(parsedValues["include"]),
    include_category: listOrNull(parsedValues["include-category"]),
    include_test: listOrNull(parsedValues["include-test"]),
    exclude: listOrNull(parsedValues["exclude"]),
    exclude_category: listOrNull(parsedValues["exclude-category"]),
    exclude_test: listOrNull(parsedValues["exclude-test"]),
    verbose: parsedValues["verbose"]?.length ?? 0,
    regex_filters: parsedValues["regex-filters"],
    compiler_path: parsedValues["compiler"] ?? process.env["SOL2XML_PATH"] ?? null,
    interpreter_path: parsedValues["interpreter"] ?? process.env["SOL26_INTERPRETER_PATH"] ?? null,
  };

  // Check source directory
  if (!existsSync(args.tests_dir) || !lstatSync(args.tests_dir).isDirectory()) {
    console.error("The provided path is not a directory.");
    process.exit(1);
  }

  // Warn if the output file already exists
  if (args.output !== null) {
    const outputParent = dirname(args.output);
    if (!existsSync(outputParent)) {
      console.error("The parent directory of the output file does not exist.");
      process.exit(1);
    }

    if (existsSync(args.output)) {
      logger.warn("The output file will be overwritten: %s", args.output);
    }
  }

  return args;
}

function discoverTestFiles(testsDir: string, recursive: boolean): string[] {
  const discovered: string[] = [];

  function walk(currentDir: string): void {
    const entries = readdirSync(currentDir, { withFileTypes: true });

    for (const entry of entries) {
      const entryPath = join(currentDir, entry.name);

      if (entry.isDirectory()) {
        if (recursive) {
          walk(entryPath);
        }
        continue;
      }

      if (entry.isFile() && entry.name.endsWith(".test")) {
        discovered.push(entryPath);
      }
    }
  }

  walk(testsDir);
  discovered.sort();
  return discovered;
}

interface ParsedTestFile {
  description: string | null;
  category: string;
  points: number;
  source: string;
  parserExitCodes: number[];
  interpreterExitCodes: number[];
}

interface LoadTestsResult {
  discoveredTestCases: TestCaseDefinition[];
  unexecuted: Record<string, UnexecutedReason>;
}

interface FilterSets {
  includeNames: Set<string>;
  includeCategories: Set<string>;
  excludeNames: Set<string>;
  excludeCategories: Set<string>;
}

interface ProcessRunResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  spawnError: string | null;
}

interface CompilerExecutionResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  spawnError: string | null;
  xmlOutput: string | null;
}

interface ExecutionPreparationResult {
  selectedForExecution: TestCaseDefinition[];
  unexecuted: Record<string, UnexecutedReason>;
}

interface InterpreterExecutionResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  spawnError: string | null;
}

interface DiffExecutionResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  spawnError: string | null;
}

interface ExecutionOutcome {
  report: TestCaseReport | null;
  unexecuted: UnexecutedReason | null;
}

function trimmedValues(values: string[] | null): string[] {
  if (values === null) {
    return [];
  }

  return values.map((value) => value.trim()).filter((value) => value.length > 0);
}

function buildFilterSets(args: CliArguments): FilterSets {
  const includeNames = new Set<string>();
  const includeCategories = new Set<string>();
  const excludeNames = new Set<string>();
  const excludeCategories = new Set<string>();

  for (const value of trimmedValues(args.include)) {
    includeNames.add(value);
    includeCategories.add(value);
  }

  for (const value of trimmedValues(args.include_test)) {
    includeNames.add(value);
  }

  for (const value of trimmedValues(args.include_category)) {
    includeCategories.add(value);
  }

  for (const value of trimmedValues(args.exclude)) {
    excludeNames.add(value);
    excludeCategories.add(value);
  }

  for (const value of trimmedValues(args.exclude_test)) {
    excludeNames.add(value);
  }

  for (const value of trimmedValues(args.exclude_category)) {
    excludeCategories.add(value);
  }

  return {
    includeNames,
    includeCategories,
    excludeNames,
    excludeCategories,
  };
}

function matchesInclude(testCase: TestCaseDefinition, filters: FilterSets): boolean {
  const hasIncludeFilters =
      filters.includeNames.size > 0 || filters.includeCategories.size > 0;

  if (!hasIncludeFilters) {
    return true;
  }

  return (
      filters.includeNames.has(testCase.name) ||
      filters.includeCategories.has(testCase.category)
  );
}

function matchesExclude(testCase: TestCaseDefinition, filters: FilterSets): boolean {
  return (
      filters.excludeNames.has(testCase.name) ||
      filters.excludeCategories.has(testCase.category)
  );
}

function isSelectedByFilters(testCase: TestCaseDefinition, filters: FilterSets): boolean {
  return matchesInclude(testCase, filters) && !matchesExclude(testCase, filters);
}

function applyFilters(
    discoveredTestCases: TestCaseDefinition[],
    existingUnexecuted: Record<string, UnexecutedReason>,
    args: CliArguments
): Record<string, UnexecutedReason> {
  const filters = buildFilterSets(args);
  const unexecuted: Record<string, UnexecutedReason> = { ...existingUnexecuted };

  for (const testCase of discoveredTestCases) {
    if (!matchesInclude(testCase, filters) || matchesExclude(testCase, filters)) {
      unexecuted[testCase.name] = new UnexecutedReason(
          UnexecutedReasonCode.FILTERED_OUT,
          "Test case was filtered out by include/exclude rules."
      );
    }
  }

  return unexecuted;
}

function prepareExecutionSet(
    discoveredTestCases: TestCaseDefinition[],
    existingUnexecuted: Record<string, UnexecutedReason>,
    args: CliArguments
): ExecutionPreparationResult {
  const filters = buildFilterSets(args);
  const selectedForExecution: TestCaseDefinition[] = [];
  const unexecuted: Record<string, UnexecutedReason> = { ...existingUnexecuted };

  for (const testCase of discoveredTestCases) {
    if (unexecuted[testCase.name] !== undefined) {
      continue;
    }

    if (!isSelectedByFilters(testCase, filters)) {
      continue;
    }

    selectedForExecution.push(testCase);
  }

  return { selectedForExecution, unexecuted };
}

function runProcess(command: string, args: string[], stdin: string | null = null): Promise<ProcessRunResult> {
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let settled = false;

    try {
      const child = spawn(command, args, {
        stdio: "pipe",
      });

      child.stdout.on("data", (chunk: Buffer | string) => {
        stdout += chunk.toString();
      });

      child.stderr.on("data", (chunk: Buffer | string) => {
        stderr += chunk.toString();
      });

      child.on("error", (error: Error) => {
        if (settled) {
          return;
        }

        settled = true;
        resolve({
          exitCode: null,
          stdout,
          stderr,
          spawnError: error.message,
        });
      });

      child.on("close", (code: number | null) => {
        if (settled) {
          return;
        }

        settled = true;
        resolve({
          exitCode: code,
          stdout,
          stderr,
          spawnError: null,
        });
      });

      if (stdin !== null) {
        child.stdin.write(stdin);
      }

      child.stdin.end();
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);
      resolve({
        exitCode: null,
        stdout,
        stderr,
        spawnError: message,
      });
    }
  });
}

async function runCompiler(
    compilerPath: string,
    testCase: TestCaseDefinition
): Promise<CompilerExecutionResult> {
  const parsed = parseTestFile(testCase.test_source_path);
  const compilerInput = parsed.source;

  const result = await runProcess("python", [compilerPath], compilerInput);

  return {
    exitCode: result.exitCode,
    stdout: result.stdout,
    stderr: result.stderr,
    spawnError: result.spawnError,
    xmlOutput: result.exitCode === 0 ? result.stdout : null,
  };
}

async function runInterpreter(
    interpreterPath: string,
    xmlInput: string,
    stdinInput: string | null
): Promise<InterpreterExecutionResult> {
  const tempDir = mkdtempSync(join(tmpdir(), "sol26-tester-"));
  const xmlPath = join(tempDir, "program.xml");
  const inputPath = join(tempDir, "input.txt");

  try {
    writeFileSync(xmlPath, xmlInput, "utf8");

    const args = ["run", "python", interpreterPath, "--source", xmlPath];

    if (stdinInput !== null) {
      writeFileSync(inputPath, stdinInput, "utf8");
      args.push("--input", inputPath);
    }

    const result = await runProcess("uv", args);

    return {
      exitCode: result.exitCode,
      stdout: result.stdout,
      stderr: result.stderr,
      spawnError: result.spawnError,
    };
  } finally {
    rmSync(tempDir, { recursive: true, force: true });
  }
}

function loadOptionalStdin(testCase: TestCaseDefinition): string | null {
  if (testCase.stdin_file === null) {
    return null;
  }

  return readFileSync(testCase.stdin_file, "utf8");
}

async function executeTestCase(
    testCase: TestCaseDefinition,
    args: CliArguments
): Promise<ExecutionOutcome> {
  let parserExitCode: number | null = null;
  let parserStdout: string | null = null;
  let parserStderr: string | null = null;
  let interpreterExitCode: number | null = null;
  let interpreterStdout: string | null = null;
  let interpreterStderr: string | null = null;
  let diffOutput: string | null = null;

  if (testCase.test_type === TestCaseType.PARSE_ONLY) {
    if (args.compiler_path === null) {
      return {
        report: null,
        unexecuted: new UnexecutedReason(
            UnexecutedReasonCode.CANNOT_EXECUTE,
            "Compiler path is not configured."
        ),
      };
    }

    const compilerResult = await runCompiler(args.compiler_path, testCase);

    if (compilerResult.spawnError !== null) {
      return {
        report: null,
        unexecuted: new UnexecutedReason(
            UnexecutedReasonCode.CANNOT_EXECUTE,
            `Failed to execute compiler: ${compilerResult.spawnError}`
        ),
      };
    }

    parserExitCode = compilerResult.exitCode;
    parserStdout = compilerResult.stdout;
    parserStderr = compilerResult.stderr;

    const result = exitCodeMatches(parserExitCode, testCase.expected_parser_exit_codes)
        ? TestResult.PASSED
        : TestResult.UNEXPECTED_PARSER_EXIT_CODE;

    return {
      report: new TestCaseReport(
          result,
          parserExitCode,
          null,
          parserStdout,
          parserStderr,
          null,
          null,
          null
      ),
      unexecuted: null,
    };
  }

  let xmlInput: string;

  if (testCase.test_type === TestCaseType.EXECUTE_ONLY) {
    const parsed = parseTestFile(testCase.test_source_path);
    xmlInput = parsed.source;
  } else {
    if (args.compiler_path === null) {
      return {
        report: null,
        unexecuted: new UnexecutedReason(
            UnexecutedReasonCode.CANNOT_EXECUTE,
            "Compiler path is not configured."
        ),
      };
    }

    const compilerResult = await runCompiler(args.compiler_path, testCase);

    if (compilerResult.spawnError !== null) {
      return {
        report: null,
        unexecuted: new UnexecutedReason(
            UnexecutedReasonCode.CANNOT_EXECUTE,
            `Failed to execute compiler: ${compilerResult.spawnError}`
        ),
      };
    }

    parserExitCode = compilerResult.exitCode;
    parserStdout = compilerResult.stdout;
    parserStderr = compilerResult.stderr;

    if (!exitCodeMatches(parserExitCode, testCase.expected_parser_exit_codes)) {
      return {
        report: new TestCaseReport(
            TestResult.UNEXPECTED_PARSER_EXIT_CODE,
            parserExitCode,
            null,
            parserStdout,
            parserStderr,
            null,
            null,
            null
        ),
        unexecuted: null,
      };
    }

    if (compilerResult.exitCode !== 0 || compilerResult.xmlOutput === null) {
      return {
        report: new TestCaseReport(
            TestResult.PASSED,
            parserExitCode,
            null,
            parserStdout,
            parserStderr,
            null,
            null,
            null
        ),
        unexecuted: null,
      };
    }

    xmlInput = compilerResult.xmlOutput;
  }

  if (args.interpreter_path === null) {
    return {
      report: null,
      unexecuted: new UnexecutedReason(
          UnexecutedReasonCode.CANNOT_EXECUTE,
          "Interpreter path is not configured."
      ),
    };
  }

  const stdinInput = loadOptionalStdin(testCase);
  const interpreterResult = await runInterpreter(args.interpreter_path, xmlInput, stdinInput);

  if (interpreterResult.spawnError !== null) {
    return {
      report: null,
      unexecuted: new UnexecutedReason(
          UnexecutedReasonCode.CANNOT_EXECUTE,
          `Failed to execute interpreter: ${interpreterResult.spawnError}`
      ),
    };
  }

  interpreterExitCode = interpreterResult.exitCode;
  interpreterStdout = interpreterResult.stdout;
  interpreterStderr = interpreterResult.stderr;

  if (!exitCodeMatches(interpreterExitCode, testCase.expected_interpreter_exit_codes)) {
    return {
      report: new TestCaseReport(
          TestResult.UNEXPECTED_INTERPRETER_EXIT_CODE,
          parserExitCode,
          interpreterExitCode,
          parserStdout,
          parserStderr,
          interpreterStdout,
          interpreterStderr,
          null
      ),
      unexecuted: null,
    };
  }

  if (testCase.expected_stdout_file !== null && interpreterExitCode === 0) {
    const diffResult = await runDiff(testCase.expected_stdout_file, interpreterStdout);

    if (diffResult.spawnError !== null) {
      return {
        report: null,
        unexecuted: new UnexecutedReason(
            UnexecutedReasonCode.CANNOT_EXECUTE,
            `Failed to execute diff: ${diffResult.spawnError}`
        ),
      };
    }

    if (diffResult.exitCode !== 0) {
      diffOutput = diffResult.stdout.length > 0 ? diffResult.stdout : diffResult.stderr;

      return {
        report: new TestCaseReport(
            TestResult.INTERPRETER_RESULT_DIFFERS,
            parserExitCode,
            interpreterExitCode,
            parserStdout,
            parserStderr,
            interpreterStdout,
            interpreterStderr,
            diffOutput
        ),
        unexecuted: null,
      };
    }
  }

  return {
    report: new TestCaseReport(
        TestResult.PASSED,
        parserExitCode,
        interpreterExitCode,
        parserStdout,
        parserStderr,
        interpreterStdout,
        interpreterStderr,
        diffOutput
    ),
    unexecuted: null,
  };
}

function exitCodeMatches(actualExitCode: number | null, expectedExitCodes: number[] | null): boolean {
  if (actualExitCode === null || expectedExitCodes === null) {
    return false;
  }

  return expectedExitCodes.includes(actualExitCode);
}

async function runDiff(expectedOutputPath: string, actualOutput: string): Promise<DiffExecutionResult> {
  const tempDir = mkdtempSync(join(tmpdir(), "sol26-diff-"));
  const actualPath = join(tempDir, "actual.out");

  try {
    writeFileSync(actualPath, actualOutput, "utf8");
    const result = await runProcess("diff", [expectedOutputPath, actualPath]);

    return {
      exitCode: result.exitCode,
      stdout: result.stdout,
      stderr: result.stderr,
      spawnError: result.spawnError,
    };
  } finally {
    rmSync(tempDir, { recursive: true, force: true });
  }
}

function addExecutedResult(
    results: Record<string, CategoryReport>,
    testCase: TestCaseDefinition,
    testCaseReport: TestCaseReport
): void {
  const existingCategory = results[testCase.category];
  const passedPoints = testCaseReport.result === TestResult.PASSED ? testCase.points : 0;

  if (existingCategory === undefined) {
    results[testCase.category] = new CategoryReport(
        testCase.points,
        passedPoints,
        { [testCase.name]: testCaseReport }
    );
    return;
  }

  results[testCase.category] = new CategoryReport(
      existingCategory.total_points + testCase.points,
      existingCategory.passed_points + passedPoints,
      {
        ...existingCategory.test_results,
        [testCase.name]: testCaseReport,
      }
  );
}

async function executeSelectedTests(
    executableTests: TestCaseDefinition[],
    args: CliArguments
): Promise<{
  results: Record<string, CategoryReport>;
  unexecuted: Record<string, UnexecutedReason>;
}> {
  const results: Record<string, CategoryReport> = {};
  const unexecuted: Record<string, UnexecutedReason> = {};

  for (const testCase of executableTests) {
    const outcome = await executeTestCase(testCase, args);

    if (outcome.unexecuted !== null) {
      unexecuted[testCase.name] = outcome.unexecuted;
      continue;
    }

    if (outcome.report !== null) {
      addExecutedResult(results, testCase, outcome.report);
    }
  }

  return { results, unexecuted };
}

function applyDryRun(
    selectedForExecution: TestCaseDefinition[],
    existingUnexecuted: Record<string, UnexecutedReason>
): Record<string, UnexecutedReason> {
  const unexecuted: Record<string, UnexecutedReason> = { ...existingUnexecuted };

  for (const testCase of selectedForExecution) {
    unexecuted[testCase.name] = new UnexecutedReason(
        UnexecutedReasonCode.OTHER,
        "Execution skipped because --dry-run was used."
    );
  }

  return unexecuted;
}

function parseIntegerField(rawValue: string, fieldName: string): number {
  const trimmed = rawValue.trim();
  if (!/^-?\d+$/.test(trimmed)) {
    throw new Error(`Invalid integer in ${fieldName}: ${rawValue}`);
  }

  return Number.parseInt(trimmed, 10);
}

function parseTestFile(testFilePath: string): ParsedTestFile {
  const content = readFileSync(testFilePath, "utf8");
  const lines = content.split(/\r?\n/);

  const separatorIndex = lines.findIndex((line) => line.trim() === "");
  if (separatorIndex < 0) {
    throw new Error("Missing empty line separating metadata from source.");
  }

  const metadataLines = lines.slice(0, separatorIndex);
  const sourceLines = lines.slice(separatorIndex + 1);
  const source = sourceLines.join("\n");

  let description: string | null = null;
  let hasDescription = false;
  let category: string | null = null;
  let points: number | null = null;
  const parserExitCodes: number[] = [];
  const interpreterExitCodes: number[] = [];

  for (const rawLine of metadataLines) {
    const line = rawLine.trim();

    if (line.length === 0) {
      continue;
    }

    if (line.startsWith("***")) {
      if (hasDescription) {
        throw new Error("Duplicate description (***).");
      }

      hasDescription = true;
      description = line.slice(3).trim() || null;
      continue;
    }

    if (line.startsWith("+++")) {
      if (category !== null) {
        throw new Error("Duplicate category (+++).");
      }

      category = line.slice(3).trim();
      continue;
    }

    if (line.startsWith(">>>")) {
      if (points !== null) {
        throw new Error("Duplicate points (>>>).");
      }

      points = parseIntegerField(line.slice(3), ">>> points");
      continue;
    }

    if (line.startsWith("!C!")) {
      parserExitCodes.push(parseIntegerField(line.slice(3), "!C! exit code"));
      continue;
    }

    if (line.startsWith("!I!")) {
      interpreterExitCodes.push(parseIntegerField(line.slice(3), "!I! exit code"));
      continue;
    }

    throw new Error(`Unknown metadata line: ${rawLine}`);
  }

  if (category === null || category.trim() === "") {
    throw new Error("Missing required category (+++).");
  }

  if (points === null) {
    throw new Error("Missing required points (>>>).");
  }

  if (source.trim() === "") {
    throw new Error("Missing source code body.");
  }

  return {
    description,
    category,
    points,
    source,
    parserExitCodes,
    interpreterExitCodes,
  };
}

function optionalSidecarPath(testFilePath: string, extension: ".in" | ".out"): string | null {
  const sidecarPath = testFilePath.replace(/\.test$/u, extension);
  return existsSync(sidecarPath) ? sidecarPath : null;
}

function withSidecars(testCase: TestCaseDefinition): TestCaseDefinition {
  const stdinFile = optionalSidecarPath(testCase.test_source_path, ".in");
  const expectedStdoutFile = optionalSidecarPath(testCase.test_source_path, ".out");

  return new TestCaseDefinition({
    name: testCase.name,
    test_type: testCase.test_type,
    description: testCase.description,
    category: testCase.category,
    points: testCase.points,
    test_source_path: testCase.test_source_path,
    stdin_file: stdinFile,
    expected_stdout_file: expectedStdoutFile,
    expected_parser_exit_codes: testCase.expected_parser_exit_codes,
    expected_interpreter_exit_codes: testCase.expected_interpreter_exit_codes,
  });
}

function determineTestType(parsed: ParsedTestFile, testFilePath: string): TestCaseType {
  const hasParser = parsed.parserExitCodes.length > 0;
  const hasInterpreter = parsed.interpreterExitCodes.length > 0;
  const lowerPath = testFilePath.toLowerCase();

  const looksLikeXmlSource =
      lowerPath.endsWith(".xml.test") ||
      parsed.source.trimStart().startsWith("<?xml") ||
      parsed.source.trimStart().startsWith("<program");

  if (looksLikeXmlSource) {
    if (hasParser) {
      throw new Error("XML test must not declare compiler exit codes.");
    }

    if (!hasInterpreter) {
      throw new Error("Cannot determine test type: XML test is missing interpreter exit codes.");
    }

    return TestCaseType.EXECUTE_ONLY;
  }

  if (hasParser && hasInterpreter) {
    if (parsed.parserExitCodes.length !== 1 || parsed.parserExitCodes[0] !== 0) {
      throw new Error(
          "Cannot determine test type: combined test requires the only compiler exit code to be 0."
      );
    }

    return TestCaseType.COMBINED;
  }

  if (hasParser) {
    return TestCaseType.PARSE_ONLY;
  }

  if (hasInterpreter) {
    throw new Error(
        "Cannot determine test type: non-XML test with only interpreter exit codes is ambiguous."
    );
  }

  throw new Error("Cannot determine test type from test definition.");
}

function loadDiscoveredTests(testsDir: string, recursive: boolean): LoadTestsResult {
  const testFiles = discoverTestFiles(testsDir, recursive);
  const discoveredTestCases: TestCaseDefinition[] = [];
  const unexecuted: Record<string, UnexecutedReason> = {};

  for (const testFilePath of testFiles) {
    const testName = basename(testFilePath, ".test");

    try {
      const parsed = parseTestFile(testFilePath);
      const testType = determineTestType(parsed, testFilePath);

      const testCase = new TestCaseDefinition({
        name: testName,
        test_type: testType,
        description: parsed.description,
        category: parsed.category,
        points: parsed.points,
        test_source_path: testFilePath,
        expected_parser_exit_codes:
            parsed.parserExitCodes.length > 0 ? parsed.parserExitCodes : null,
        expected_interpreter_exit_codes:
            parsed.interpreterExitCodes.length > 0 ? parsed.interpreterExitCodes : null,
      });

      discoveredTestCases.push(withSidecars(testCase));
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);

      const reasonCode = message.startsWith("Cannot determine test type")
          ? UnexecutedReasonCode.CANNOT_DETERMINE_TYPE
          : message.includes("XML test must not declare compiler exit codes")
              ? UnexecutedReasonCode.CANNOT_DETERMINE_TYPE
              : UnexecutedReasonCode.MALFORMED_TEST_CASE_FILE;

      unexecuted[testName] = new UnexecutedReason(reasonCode, message);
    }
  }

  return { discoveredTestCases, unexecuted };
}

async function main(): Promise<void> {
  /**
   * The main entry point for the SOL26 integration testing script.
   * It parses command-line arguments and executes the testing process.
   */

  // Set up logging
  // IPP: You do not have to use logging - but it is the recommended practice.
  //      See https://getpino.io/#/docs/api for more information.
  logger.level = "warn";

  // Parse the CLI arguments
  const args = parseArguments();

  // Enable debug or info logging if the verbose flag was set twice or once
  if (args.verbose >= 2) {
    logger.level = "debug";
  } else if (args.verbose === 1) {
    logger.level = "info";
  }

  const loadResult = loadDiscoveredTests(args.tests_dir, args.recursive);
  const unexecutedAfterFiltering = applyFilters(
      loadResult.discoveredTestCases,
      loadResult.unexecuted,
      args
  );

  const executionPreparation = prepareExecutionSet(
      loadResult.discoveredTestCases,
      unexecutedAfterFiltering,
      args
  );

  const executed = args.dry_run
      ? { results: {}, unexecuted: {} }
      : await executeSelectedTests(executionPreparation.selectedForExecution, args);

  const mergedUnexecuted: Record<string, UnexecutedReason> = {
    ...executionPreparation.unexecuted,
    ...executed.unexecuted,
  };

  const unexecuted = args.dry_run
      ? applyDryRun(executionPreparation.selectedForExecution, mergedUnexecuted)
      : mergedUnexecuted;

  const report = new TestReport({
    discovered_test_cases: loadResult.discoveredTestCases,
    unexecuted,
    results: args.dry_run ? null : executed.results,
  });

  writeResult(report, args.output);
}

void main();