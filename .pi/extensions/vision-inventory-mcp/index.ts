import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { StringEnum } from "@earendil-works/pi-ai";
import { Type } from "typebox";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync } from "node:fs";
import { chmod, mkdir, readFile, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SERVER_FILE = "vision_inventory_mcp.py";
const PYTHON_COMMAND = process.env.PI_VISION_INVENTORY_PYTHON || "python3";
const MAX_RESULT_CHARS = 50_000;
const EXTENSION_DIR = dirname(fileURLToPath(import.meta.url));
const CONFIG_DIR = join(homedir(), ".pi", "agent", "vision-inventory");
const CREDENTIALS_FILE = join(CONFIG_DIR, "credentials.json");

type VisionCredentials = {
  cloudflareAccountId?: string;
  cloudflareAuthToken?: string;
  workersAiModel?: string;
};

type JsonRpcResponse = {
  jsonrpc: "2.0";
  id: number;
  result?: unknown;
  error?: { code?: number; message?: string; data?: unknown };
};

type PendingRequest = {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
};

class McpStdioClient {
  private proc: ChildProcessWithoutNullStreams | undefined;
  private nextId = 1;
  private buffer = "";
  private pending = new Map<number, PendingRequest>();
  private initialized = false;
  private startPromise: Promise<void> | undefined;

  constructor(private readonly packageRoot: string) {}

  async start(): Promise<void> {
    if (this.initialized) return;
    if (this.startPromise) return this.startPromise;

    this.startPromise = this.startInternal();
    return this.startPromise;
  }

  private async startInternal(): Promise<void> {
    const serverPath = join(this.packageRoot, SERVER_FILE);
    if (!existsSync(serverPath)) {
      throw new Error(`Cannot find ${SERVER_FILE} in ${this.packageRoot}`);
    }

    this.proc = spawn(PYTHON_COMMAND, [serverPath], {
      cwd: this.packageRoot,
      env: await buildPythonEnv(),
      stdio: ["pipe", "pipe", "pipe"],
    });

    this.proc.stdout.setEncoding("utf8");
    this.proc.stderr.setEncoding("utf8");

    this.proc.stdout.on("data", (chunk: string) => this.onStdout(chunk));
    this.proc.stderr.on("data", (chunk: string) => {
      // MCP servers should not write protocol data to stderr. Keep it visible in `details` on failures.
      this.lastStderr = (this.lastStderr + chunk).slice(-10_000);
    });
    this.proc.on("error", (error) => {
      const err = new Error(`Failed to start Vision Inventory MCP server with ${PYTHON_COMMAND}: ${error.message}`);
      for (const pending of this.pending.values()) pending.reject(err);
      this.pending.clear();
      this.initialized = false;
      this.startPromise = undefined;
      this.proc = undefined;
    });
    this.proc.on("exit", (code, signal) => {
      const err = new Error(`Vision Inventory MCP server exited with code ${code ?? "null"}${signal ? ` signal ${signal}` : ""}`);
      for (const pending of this.pending.values()) pending.reject(err);
      this.pending.clear();
      this.initialized = false;
      this.startPromise = undefined;
      this.proc = undefined;
    });

    await this.request("initialize", {
      protocolVersion: "2024-11-05",
      capabilities: {},
      clientInfo: { name: "pi-vision-inventory-extension", version: "1.0.0" },
    });
    this.notify("notifications/initialized", {});
    this.initialized = true;
  }

  private lastStderr = "";

  getStderr(): string {
    return this.lastStderr;
  }

  async stop(): Promise<void> {
    const proc = this.proc;
    if (!proc) return;
    this.proc = undefined;
    this.initialized = false;
    this.startPromise = undefined;
    proc.kill("SIGTERM");
  }

  async callTool(name: string, args: Record<string, unknown>): Promise<unknown> {
    await this.start();
    return this.request("tools/call", { name, arguments: args });
  }

  private request(method: string, params: unknown): Promise<unknown> {
    if (!this.proc) throw new Error("MCP server is not running");
    const id = this.nextId++;
    const message = { jsonrpc: "2.0", id, method, params };
    const payload = JSON.stringify(message) + "\n";

    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.proc!.stdin.write(payload, "utf8", (error) => {
        if (error) {
          this.pending.delete(id);
          reject(error);
        }
      });
    });
  }

  private notify(method: string, params: unknown): void {
    if (!this.proc) return;
    this.proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", method, params }) + "\n", "utf8");
  }

  private onStdout(chunk: string): void {
    this.buffer += chunk;
    while (true) {
      const newline = this.buffer.indexOf("\n");
      if (newline < 0) return;
      const line = this.buffer.slice(0, newline).trim();
      this.buffer = this.buffer.slice(newline + 1);
      if (!line) continue;

      let message: JsonRpcResponse;
      try {
        message = JSON.parse(line) as JsonRpcResponse;
      } catch {
        continue;
      }

      if (typeof message.id !== "number") continue;
      const pending = this.pending.get(message.id);
      if (!pending) continue;
      this.pending.delete(message.id);

      if (message.error) {
        pending.reject(new Error(message.error.message ?? `MCP error ${message.error.code ?? "unknown"}`));
      } else {
        pending.resolve(message.result);
      }
    }
  }
}

function findPackageRoot(startDir: string, fallback: string): string {
  let current = startDir;
  while (true) {
    if (existsSync(join(current, SERVER_FILE)) && existsSync(join(current, "scripts", "inventory_folder_to_csv.py"))) {
      return current;
    }
    const parent = dirname(current);
    if (parent === current) return fallback;
    current = parent;
  }
}

function resolveUserPath(cwd: string, value: unknown): unknown {
  if (typeof value !== "string" || value.length === 0) return value;
  return isAbsolute(value) ? value : resolve(cwd, value);
}

async function loadCredentials(): Promise<VisionCredentials> {
  try {
    return JSON.parse(await readFile(CREDENTIALS_FILE, "utf8")) as VisionCredentials;
  } catch {
    return {};
  }
}

async function saveCredentials(credentials: VisionCredentials): Promise<void> {
  await mkdir(CONFIG_DIR, { recursive: true });
  await writeFile(CREDENTIALS_FILE, JSON.stringify(credentials, null, 2), "utf8");
  try {
    await chmod(CREDENTIALS_FILE, 0o600);
  } catch {
    // Best effort on platforms/filesystems that do not support chmod.
  }
}

async function buildPythonEnv(): Promise<NodeJS.ProcessEnv> {
  const credentials = await loadCredentials();
  return {
    ...process.env,
    CLOUDFLARE_ACCOUNT_ID: process.env.CLOUDFLARE_ACCOUNT_ID || credentials.cloudflareAccountId || "",
    CLOUDFLARE_AUTH_TOKEN: process.env.CLOUDFLARE_AUTH_TOKEN || process.env.CLOUDFLARE_API_TOKEN || credentials.cloudflareAuthToken || "",
    CLOUDFLARE_API_TOKEN: process.env.CLOUDFLARE_API_TOKEN || process.env.CLOUDFLARE_AUTH_TOKEN || credentials.cloudflareAuthToken || "",
    WORKERS_AI_MODEL: process.env.WORKERS_AI_MODEL || credentials.workersAiModel || "@cf/meta/llama-4-scout-17b-16e-instruct",
  };
}

function extractMcpPayload(result: unknown): unknown {
  const maybe = result as { content?: Array<{ type?: string; text?: string }>; structuredContent?: unknown } | null;
  if (maybe && typeof maybe === "object") {
    if (maybe.structuredContent !== undefined) return maybe.structuredContent;
    const text = maybe.content?.find((item) => item.type === "text" && typeof item.text === "string")?.text;
    if (text !== undefined) {
      try {
        return JSON.parse(text);
      } catch {
        return text;
      }
    }
  }
  return result;
}

function resultText(payload: unknown): string {
  const text = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
  if (text.length <= MAX_RESULT_CHARS) return text;
  return `${text.slice(0, MAX_RESULT_CHARS)}\n\n[truncated to ${MAX_RESULT_CHARS} characters]`;
}

function splitCommandArgs(input: string): string[] {
  const args: string[] = [];
  let current = "";
  let quote: '"' | "'" | undefined;
  let escaping = false;

  for (const char of input) {
    if (escaping) {
      current += char;
      escaping = false;
      continue;
    }
    if (char === "\\") {
      escaping = true;
      continue;
    }
    if (quote) {
      if (char === quote) quote = undefined;
      else current += char;
      continue;
    }
    if (char === '"' || char === "'") {
      quote = char;
      continue;
    }
    if (/\s/.test(char)) {
      if (current) {
        args.push(current);
        current = "";
      }
      continue;
    }
    current += char;
  }

  if (escaping) current += "\\";
  if (current) args.push(current);
  return args;
}

function normalizeWorkflowArgs(userCwd: string, args: string[]): string[] {
  if (args.length < 2) return args;
  return [resolveUserPath(userCwd, args[0]) as string, resolveUserPath(userCwd, args[1]) as string, ...args.slice(2)];
}

async function runBatchWorkflow(packageRoot: string, userCwd: string, argsLine: string): Promise<string> {
  const args = normalizeWorkflowArgs(userCwd, splitCommandArgs(argsLine));
  if (args.length < 2) {
    return [
      "Usage:",
      "  /vision-inventory-bom <image_folder> <output_dir> [options]",
      "",
      "Examples:",
      "  /vision-inventory-bom ./photos ./output",
      "  /vision-inventory-bom ./photos ./output --recursive",
      "  /vision-inventory-bom ./photos ./output --skip-vision",
      "",
      "Options are forwarded to scripts/inventory_folder_to_csv.py.",
    ].join("\n");
  }

  const scriptPath = join(packageRoot, "scripts", "inventory_folder_to_csv.py");
  if (!existsSync(scriptPath)) {
    throw new Error(`Cannot find batch workflow script: ${scriptPath}`);
  }

  const env = await buildPythonEnv();

  return new Promise((resolve, reject) => {
    const proc = spawn(PYTHON_COMMAND, [scriptPath, ...args], {
      cwd: packageRoot,
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    proc.stdout.setEncoding("utf8");
    proc.stderr.setEncoding("utf8");
    proc.stdout.on("data", (chunk: string) => { stdout += chunk; });
    proc.stderr.on("data", (chunk: string) => { stderr += chunk; });
    proc.on("error", reject);
    proc.on("exit", (code) => {
      const output = `${stdout}${stderr ? `\nSTDERR:\n${stderr}` : ""}`.trim();
      if (code === 0) resolve(output || "Vision inventory BOM workflow completed.");
      else reject(new Error(`Workflow exited with code ${code}.\n${output}`));
    });
  });
}

export default function (pi: ExtensionAPI) {
  const packageRoot = findPackageRoot(EXTENSION_DIR, process.cwd());
  let client: McpStdioClient | undefined;

  function getClient(): McpStdioClient {
    if (!client) client = new McpStdioClient(packageRoot);
    return client;
  }

  async function callVisionTool(ctx: { cwd: string }, name: string, args: Record<string, unknown>) {
    const normalizedArgs = { ...args };
    if (name === "process_image") normalizedArgs.image_path = resolveUserPath(ctx.cwd, normalizedArgs.image_path);
    if (name === "process_image_folder") normalizedArgs.folder_path = resolveUserPath(ctx.cwd, normalizedArgs.folder_path);
    if (name === "save_inventory") normalizedArgs.output_path = resolveUserPath(ctx.cwd, normalizedArgs.output_path);

    const mcp = getClient();
    const raw = await mcp.callTool(name, normalizedArgs);
    const payload = extractMcpPayload(raw);
    return {
      content: [{ type: "text" as const, text: resultText(payload) }],
      details: { tool: name, result: payload, stderr: mcp.getStderr() },
    };
  }

  pi.registerTool({
    name: "vision_inventory_process_image",
    label: "Vision Inventory: Process Image",
    description: "Analyze one electronics/PCB image with the local Vision Inventory MCP server and return structured visible inventory JSON.",
    promptSnippet: "Analyze one electronics/PCB image into structured visual inventory JSON.",
    promptGuidelines: [
      "Use vision_inventory_process_image when the user asks to inventory or inspect a single electronics/PCB image in this repository.",
      "vision_inventory_process_image only extracts visible markings; do separate lookup/validation after it returns if the user asks for enrichment.",
    ],
    parameters: Type.Object({
      image_path: Type.String({ description: "Path to the image file, relative to the project root or absolute." }),
      max_side: Type.Optional(Type.Integer({ description: "Maximum resized image side before submission. Use 0 for full resolution.", default: 0 })),
      jpeg_quality: Type.Optional(Type.Integer({ description: "JPEG conversion quality from 1 to 100.", default: 96 })),
      custom_prompt: Type.Optional(Type.String({ description: "Optional custom analysis prompt." })),
    }),
    async execute(_id, params, _signal, onUpdate, ctx) {
      onUpdate?.({ content: [{ type: "text", text: "Starting Vision Inventory MCP server and processing image..." }], details: {} });
      return callVisionTool(ctx, "process_image", params as Record<string, unknown>);
    },
  });

  pi.registerTool({
    name: "vision_inventory_process_folder",
    label: "Vision Inventory: Process Folder",
    description: "Analyze all supported electronics/PCB images in a folder with the local Vision Inventory MCP server.",
    promptSnippet: "Batch-analyze a folder of electronics/PCB images into structured inventory JSON.",
    promptGuidelines: [
      "Use vision_inventory_process_folder when the user asks to inventory a folder or batch of electronics/PCB images.",
    ],
    parameters: Type.Object({
      folder_path: Type.String({ description: "Folder path, relative to the project root or absolute." }),
      recursive: Type.Optional(Type.Boolean({ description: "Whether to scan subfolders.", default: false })),
      max_side: Type.Optional(Type.Integer({ description: "Maximum resized image side before submission. Use 0 for full resolution.", default: 0 })),
      jpeg_quality: Type.Optional(Type.Integer({ description: "JPEG conversion quality from 1 to 100.", default: 96 })),
      limit: Type.Optional(Type.Integer({ description: "Optional maximum number of images to process." })),
    }),
    async execute(_id, params, _signal, onUpdate, ctx) {
      onUpdate?.({ content: [{ type: "text", text: "Starting Vision Inventory MCP server and processing folder..." }], details: {} });
      return callVisionTool(ctx, "process_image_folder", params as Record<string, unknown>);
    },
  });

  pi.registerTool({
    name: "vision_inventory_save",
    label: "Vision Inventory: Save",
    description: "Save Vision Inventory results to JSON or CSV using the local MCP server.",
    promptSnippet: "Save Vision Inventory output as JSON or CSV.",
    promptGuidelines: [
      "Use vision_inventory_save to save results returned by vision_inventory_process_image or vision_inventory_process_folder.",
    ],
    parameters: Type.Object({
      inventory: Type.Any({ description: "Inventory object returned by a Vision Inventory tool." }),
      output_path: Type.String({ description: "Output file path, relative to the project root or absolute." }),
      format: Type.Optional(StringEnum(["json", "csv"] as const, { description: "Output format.", default: "json" })),
    }),
    async execute(_id, params, _signal, onUpdate, ctx) {
      onUpdate?.({ content: [{ type: "text", text: "Saving Vision Inventory output..." }], details: {} });
      return callVisionTool(ctx, "save_inventory", params as Record<string, unknown>);
    },
  });

  pi.registerCommand("vision-inventory-restart", {
    description: "Restart the Vision Inventory MCP bridge process",
    handler: async (_args, ctx) => {
      await client?.stop();
      client = undefined;
      ctx.ui.notify("Vision Inventory MCP bridge restarted", "info");
    },
  });

  async function runSetup(ctx: { ui: any; hasUI: boolean }, forceCredentials = false): Promise<void> {
    const existing = await loadCredentials();
    const hasEffectiveAccountId = Boolean(process.env.CLOUDFLARE_ACCOUNT_ID || existing.cloudflareAccountId);
    const hasEffectiveToken = Boolean(process.env.CLOUDFLARE_AUTH_TOKEN || process.env.CLOUDFLARE_API_TOKEN || existing.cloudflareAuthToken);
    if ((!hasEffectiveAccountId || !hasEffectiveToken || forceCredentials) && ctx.hasUI) {
      ctx.ui.notify("Vision Inventory stores Cloudflare credentials in ~/.pi/agent/vision-inventory/credentials.json with chmod 600 when supported. Use /vision-inventory-credentials to change them.", "info");
      const cloudflareAccountId = await ctx.ui.input("Cloudflare account ID", existing.cloudflareAccountId || "");
      const cloudflareAuthToken = await ctx.ui.input("Cloudflare Workers AI token (leave blank for default)", existing.cloudflareAuthToken ? "<keep existing; paste new token to replace>" : "");
      const workersAiModel = await ctx.ui.input("Workers AI model", existing.workersAiModel || "@cf/meta/llama-4-scout-17b-16e-instruct");
      await saveCredentials({
        cloudflareAccountId: cloudflareAccountId || existing.cloudflareAccountId,
        cloudflareAuthToken: cloudflareAuthToken && !cloudflareAuthToken.startsWith("<keep existing") ? cloudflareAuthToken : existing.cloudflareAuthToken,
        workersAiModel: workersAiModel || existing.workersAiModel || "@cf/meta/llama-4-scout-17b-16e-instruct",
      });
    } else if ((!hasEffectiveAccountId || !hasEffectiveToken) && !ctx.hasUI) {
      ctx.ui.notify("Missing Cloudflare credentials. Set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_AUTH_TOKEN/CLOUDFLARE_API_TOKEN, or run /vision-inventory-setup in interactive Pi.", "error");
    }

    const deps = await pi.exec(PYTHON_COMMAND, ["-c", "import mcp, requests, PIL, dotenv; print('ok')"], { timeout: 10_000 });
    if (deps.code !== 0) {
      if (!ctx.hasUI) {
        ctx.ui.notify(`Missing Python dependencies. Run: ${PYTHON_COMMAND} -m pip install -r ${join(packageRoot, "requirements.txt")}`, "error");
        return;
      }
      const install = await ctx.ui.confirm("Install Python dependencies?", `${PYTHON_COMMAND} -m pip install -r ${join(packageRoot, "requirements.txt")}`);
      if (install) {
        const result = await pi.exec(PYTHON_COMMAND, ["-m", "pip", "install", "-r", join(packageRoot, "requirements.txt")], { timeout: 120_000 });
        if (result.code !== 0) throw new Error(result.stderr || result.stdout || "pip install failed");
      }
    }

    const commands = pi.getCommands().map((command) => command.name.toLowerCase());
    const tools = pi.getAllTools().map((tool) => tool.name.toLowerCase());
    const hasWebDependency = [...commands, ...tools].some((name) => name.includes("search") || name.includes("brave") || name.includes("browser") || name.includes("web"));
    if (!hasWebDependency) {
      ctx.ui.notify("Agent datasheet enrichment requires a web-search/browser Pi tool or skill. This package intentionally does not bundle one; install/enable your preferred search dependency before using /vision-inventory-agent-bom.", "error");
    }

    ctx.ui.notify(`Vision Inventory setup checked. Package root: ${packageRoot}`, "info");
  }

  pi.registerCommand("vision-inventory-setup", {
    description: "Configure Vision Inventory credentials and check Python/web-search dependencies",
    handler: async (args, ctx) => {
      try {
        await runSetup(ctx, args.includes("--reset") || args.includes("--credentials"));
      } catch (error) {
        ctx.ui.notify(error instanceof Error ? error.message : String(error), "error");
      }
    },
  });

  pi.registerCommand("vision-inventory-credentials", {
    description: "Change stored Cloudflare credentials for Vision Inventory",
    handler: async (_args, ctx) => {
      try {
        await runSetup(ctx, true);
        await client?.stop();
        client = undefined;
      } catch (error) {
        ctx.ui.notify(error instanceof Error ? error.message : String(error), "error");
      }
    },
  });

  pi.registerCommand("vision-inventory-bom", {
    description: "Run the folder-to-CSV Vision Inventory BOM workflow",
    handler: async (args, ctx) => {
      ctx.ui.setStatus("vision-inventory", "Running BOM workflow...");
      try {
        const output = await runBatchWorkflow(packageRoot, ctx.cwd, args || "");
        ctx.ui.notify(output.slice(-4000), "info");
      } catch (error) {
        ctx.ui.notify(error instanceof Error ? error.message.slice(-4000) : String(error), "error");
      } finally {
        ctx.ui.setStatus("vision-inventory", "");
      }
    },
  });

  pi.registerCommand("vision-inventory-agent-bom", {
    description: "Run the full Vision Inventory workflow with agent-assisted datasheet enrichment",
    handler: async (args, ctx) => {
      const parsed = splitCommandArgs(args || "");
      if (parsed.length < 2) {
        ctx.ui.notify("Usage: /vision-inventory-agent-bom <image_folder> <output_dir> [options]", "error");
        return;
      }

      await runSetup(ctx, false);
      const normalizedArgs = normalizeWorkflowArgs(ctx.cwd, parsed).map((arg) => JSON.stringify(arg)).join(" ");
      const outputDir = normalizeWorkflowArgs(ctx.cwd, parsed)[1];
      const prompt = `Run the complete Vision Electronic Indexing workflow as an agent.\n\nPackage root containing the bundled Python workflow: ${packageRoot}\nCommand arguments, already resolved relative to the user's cwd: ${normalizedArgs}\nOutput directory: ${outputDir}\n\nImportant external agent dependency: datasheet enrichment requires a web-search/browser Pi tool or skill. This package intentionally does not bundle a web-search dependency. If no search/browser tool is available, stop after generating parts_to_lookup.json and tell the user which dependency is missing.\n\nDo these steps end-to-end:\n1. Run: ${PYTHON_COMMAND} ${join(packageRoot, "scripts", "inventory_folder_to_csv.py")} ${normalizedArgs}\n2. Read ${outputDir}/parts_to_lookup.json.\n3. For every part, web-search for a datasheet. Prefer official manufacturer pages/PDFs.\n4. Write ${outputDir}/datasheet_cache.json using ${outputDir}/datasheet_cache.template.json as the exact shape.\n5. Rerun: ${PYTHON_COMMAND} ${join(packageRoot, "scripts", "inventory_folder_to_csv.py")} ${normalizedArgs} --skip-vision\n6. Read ${outputDir}/inventory.csv and ${outputDir}/inventory_evidence.csv.\n7. Summarize final BOM rows and call out every uncertainty.\n\nRules:\n- Do not invent datasheets, manufacturers, or descriptions.\n- If an exact candidate part has no official datasheet but search results strongly indicate a likely OCR correction, keep the original candidate as the datasheet_cache key and set normalized_part to the official datasheet part number. Example: key SN74AS283N may normalize to SN74LS283N when official TI results match the family/function/package and the image could plausibly confuse A with 4/LS.\n- Only set verified=true for an OCR correction when official source evidence and visual/package context make the correction highly likely; otherwise set verified=false and explain in notes.\n- Include OCR correction notes such as: \"SN74AS283N appears to be OCR for SN74LS283N; verified against TI datasheet.\"\n- Set verified=false if the part or datasheet match is uncertain.\n- Keep descriptions short, like: \"74ls (4 bit) adder low power schottky ttl 5v DIP\".\n- Preserve raw JSON and evidence files.\n- Do not expose Cloudflare credentials.\n- If a command fails because credentials or Python dependencies are missing, tell the user to run /vision-inventory-setup or /vision-inventory-credentials.`;

      await ctx.sendUserMessage(prompt);
    },
  });

  pi.on("session_shutdown", async () => {
    await client?.stop();
    client = undefined;
  });
}
