type SandboxRequest = {
  type: "execute";
  id: string;
  code: string;
  limits: {
    stdout_max_bytes: number;
    stderr_max_bytes: number;
  };
};

async function readStdinLine(): Promise<string> {
  const reader = Deno.stdin.readable.getReader();
  const chunks: Uint8Array[] = [];
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const newlineIndex = value.indexOf(10);
    if (newlineIndex >= 0) {
      chunks.push(value.slice(0, newlineIndex));
      break;
    }
    chunks.push(value);
  }
  const total = chunks.reduce((size, chunk) => size + chunk.length, 0);
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }
  return new TextDecoder().decode(merged).trimEnd();
}

function truncateText(text: string, maxBytes: number): { text: string; truncated: boolean } {
  const encoded = new TextEncoder().encode(text);
  if (encoded.byteLength <= maxBytes) {
    return { text, truncated: false };
  }
  const truncated = encoded.slice(0, maxBytes);
  return {
    text: new TextDecoder().decode(truncated) + "\n... [truncated]",
    truncated: true,
  };
}

const pyodideRoot = Deno.args[0];
if (!pyodideRoot) {
  throw new Error("Missing pyodide root argument");
}

const workerId = crypto.randomUUID();
const { createRequire } = await import("node:module");
// Pyodide 0.29.x still touches a few Node-oriented globals during wasm bootstrap.
// deno-lint-ignore no-explicit-any
(globalThis as any).require = createRequire(import.meta.url);
// deno-lint-ignore no-explicit-any
(globalThis as any).__dirname = pyodideRoot;
// deno-lint-ignore no-explicit-any
(globalThis as any).module = { exports: {} };

const pyodideModuleUrl = new URL(`file://${pyodideRoot}/pyodide.mjs`).href;
const { loadPyodide } = await import(pyodideModuleUrl);
const pyodide = await loadPyodide({ indexURL: `${pyodideRoot}/` });
const originalConsoleLog = console.log;
try {
  console.log = () => undefined;
  await pyodide.loadPackage(["numpy", "pandas"]);
} finally {
  console.log = originalConsoleLog;
}

console.log(JSON.stringify({
  type: "ready",
  worker_id: workerId,
  metadata: {
    python_runtime: "pyodide",
    sandbox_runtime: "deno_prewarmed_worker",
    pyodide_version: pyodide.version,
  },
}));

const rawRequest = await readStdinLine();
if (!rawRequest) {
  Deno.exit(0);
}
const request = JSON.parse(rawRequest) as SandboxRequest;
const startedAt = Date.now();

try {
  pyodide.globals.set("request_json", JSON.stringify(request));
  const wrapper = `
import io
import json
import math
import statistics
from contextlib import redirect_stderr, redirect_stdout

import numpy as np
import pandas as pd

request = json.loads(request_json)
code = request["code"]
limits = request["limits"]
blocked_imports = {
    "os",
    "sys",
    "subprocess",
    "socket",
    "pathlib",
    "shutil",
    "tempfile",
    "ctypes",
    "importlib",
    "builtins",
    "multiprocessing",
}
safe_builtins = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "Exception": Exception,
    "filter": filter,
    "float": float,
    "hasattr": hasattr,
    "int": int,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "pow": pow,
    "print": print,
    "range": range,
    "reversed": reversed,
    "repr": repr,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "ArithmeticError": ArithmeticError,
    "zip": zip,
}

real_import = __import__

def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".", 1)[0]
    if root in blocked_imports:
        raise ImportError(f"Import not allowed: {name}")
    return real_import(name, globals, locals, fromlist, level)

safe_builtins["__import__"] = safe_import
exec_globals = {
    "__builtins__": safe_builtins,
    "math": math,
    "statistics": statistics,
    "np": np,
    "pd": pd,
}
stdout_buffer = io.StringIO()
stderr_buffer = io.StringIO()

def clip_list(items, max_items):
    data = list(items)
    truncated = len(data) > max_items
    return data[:max_items], truncated

response = {
    "success": False,
    "stdout": "",
    "stderr": "",
    "error": None,
    "timed_out": False,
    "truncated": False,
    "metadata": {
        "python_runtime": "pyodide",
        "sandbox_runtime": "deno_prewarmed_worker",
    },
}

try:
    with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
        exec(code, exec_globals, exec_globals)
    response["success"] = True
except Exception as exc:
    response["error"] = f"{type(exc).__name__}: {exc}"
    response["metadata"]["error_type"] = "runtime_error"

stdout_text = stdout_buffer.getvalue()
stderr_text = stderr_buffer.getvalue()
stdout_limited, stdout_truncated = clip_list(stdout_text.encode("utf-8"), limits["stdout_max_bytes"])
stderr_limited, stderr_truncated = clip_list(stderr_text.encode("utf-8"), limits["stderr_max_bytes"])
response["stdout"] = bytes(stdout_limited).decode("utf-8", errors="ignore")
response["stderr"] = bytes(stderr_limited).decode("utf-8", errors="ignore")
response["truncated"] = response["truncated"] or stdout_truncated or stderr_truncated
response["metadata"]["output_bytes"] = len(stdout_text.encode("utf-8")) + len(stderr_text.encode("utf-8"))
_sandbox_response_json = json.dumps(response, ensure_ascii=False)
`;

  await pyodide.runPythonAsync(wrapper);
  const payload = pyodide.globals.get("_sandbox_response_json");
  const response = JSON.parse(String(payload));
  const stdoutInfo = truncateText(String(response.stdout ?? ""), request.limits.stdout_max_bytes);
  const stderrInfo = truncateText(String(response.stderr ?? ""), request.limits.stderr_max_bytes);
  response.stdout = stdoutInfo.text;
  response.stderr = stderrInfo.text;
  response.truncated = Boolean(response.truncated || stdoutInfo.truncated || stderrInfo.truncated);
  response.execution_time_ms = Date.now() - startedAt;
  response.metadata = response.metadata ?? {};
  response.metadata.pyodide_version = pyodide.version;
  response.metadata.worker_id = workerId;
  console.log(JSON.stringify({ type: "result", id: request.id, ...response }));
} catch (error) {
  console.log(JSON.stringify({
    type: "result",
    id: request.id,
    success: false,
    stdout: "",
    stderr: "",
    error: `Sandbox execution failed: ${error instanceof Error ? error.message : String(error)}`,
    execution_time_ms: Date.now() - startedAt,
    timed_out: false,
    truncated: false,
    metadata: {
      error_type: "runtime_error",
      python_runtime: "pyodide",
      sandbox_runtime: "deno_prewarmed_worker",
      pyodide_version: pyodide.version,
      worker_id: workerId,
    },
  }));
  Deno.exit(1);
}

Deno.exit(0);
