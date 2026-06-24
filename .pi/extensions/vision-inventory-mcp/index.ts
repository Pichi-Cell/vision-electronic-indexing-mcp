import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { StringEnum } from "@earendil-works/pi-ai";
import { Type } from "typebox";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";

const SERVER_FILE = "vision_inventory_mcp.py";
const PYTHON_COMMAND = process.env.PI_VISION_INVENTORY_PYTHON || "python3";
const MAX_RESULT_CHARS = 50_000;

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

  constructor(private readonly cwd: string) {}

  async start(): Promise<void> {
    if (this.initialized) return;
    if (this.startPromise) return this.startPromise;

    this.startPromise = this.startInternal();
    return this.startPromise;
  }

  private async startInternal(): Promise<void> {
    const serverPath = join(this.cwd, SERVER_FILE);
    if (!existsSync(serverPath)) {
      throw new Error(`Cannot find ${SERVER_FILE} in ${this.cwd}`);
    }

    this.proc = spawn(PYTHON_COMMAND, [serverPath], {
      cwd: this.cwd,
      env: process.env,
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

async function runBatchWorkflow(cwd: string, argsLine: string): Promise<string> {
  const args = splitCommandArgs(argsLine);
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

  const scriptPath = join(cwd, "scripts", "inventory_folder_to_csv.py");
  if (!existsSync(scriptPath)) {
    throw new Error(`Cannot find batch workflow script: ${scriptPath}`);
  }

  return new Promise((resolve, reject) => {
    const proc = spawn(PYTHON_COMMAND, [scriptPath, ...args], {
      cwd,
      env: process.env,
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
  let client: McpStdioClient | undefined;

  function getClient(cwd: string): McpStdioClient {
    if (!client) client = new McpStdioClient(cwd);
    return client;
  }

  async function callVisionTool(ctx: { cwd: string }, name: string, args: Record<string, unknown>) {
    const mcp = getClient(ctx.cwd);
    const raw = await mcp.callTool(name, args);
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
      max_side: Type.Optional(Type.Integer({ description: "Maximum resized image side before submission.", default: 4000 })),
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
      max_side: Type.Optional(Type.Integer({ description: "Maximum resized image side before submission.", default: 4000 })),
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

  pi.registerCommand("vision-inventory-bom", {
    description: "Run the folder-to-CSV Vision Inventory BOM workflow",
    handler: async (args, ctx) => {
      ctx.ui.setStatus("vision-inventory", "Running BOM workflow...");
      try {
        const output = await runBatchWorkflow(ctx.cwd, args || "");
        ctx.ui.notify(output.slice(-4000), "info");
      } catch (error) {
        ctx.ui.notify(error instanceof Error ? error.message.slice(-4000) : String(error), "error");
      } finally {
        ctx.ui.setStatus("vision-inventory", "");
      }
    },
  });

  pi.on("session_shutdown", async () => {
    await client?.stop();
    client = undefined;
  });
}
