# Caldova CSV Chart Analyst - hosted agent

Independent hosted solution named **csv-chart-analyst-hosted**, using the existing
**gpt-5.4-nano** deployment in **csv-chart-analyst-dev**. No provisioning, deployment,
model creation, connection creation, or role changes are needed for this local scaffold.
The prompt solution is unchanged and is not imported at runtime.

## Implementation

- [main.py](src/csv-chart-analyst-hosted/main.py) uses Microsoft Agent Framework's
  `FoundryChatClient` and `ResponsesHostServer` (Responses protocol 2.0.0).
- [instructions.txt](src/csv-chart-analyst-hosted/instructions.txt) preserves the prompt
  solution's instructions verbatim.
- [regional-sales.csv](src/csv-chart-analyst-hosted/sample-data/regional-sales.csv) is a
  byte-identical copy of the original synthetic Caldova grounding data.
- The runtime adapter replaces the managed Copilot shell with isolated Foundry Code
  Interpreter. Model-generated Python does not execute on your workstation.
- The bundled CSV is uploaded to the existing project's Files API at startup and attached
  to Code Interpreter. User attachments take precedence over the demo.
- Generated charts are copied from Code Interpreter containers to the project's Files API
  by `publish_output(filename)`, which returns their actual filename and downloadable Foundry file ID.
  The runtime records the real container ID from completed Code Interpreter results in the
  current agent session, including streamed results. The publisher resolves the generated
  file ID through the container's Files API; the model never supplies infrastructure IDs.
  Streaming completion is read from the raw `response.output_item.done` event because
  the installed SDK only parses the initial, in-progress interpreter result.
  Uploaded source files, missing outputs, and ambiguous filenames are rejected explicitly.
  Use the project's `/openai/v1/files/<id>/content` endpoint to download those files.

Local debugging is **not offline**: starting the agent uploads the demo CSV; requests
use Azure inference and Code Interpreter and can incur charges. Uploaded input/output
files remain in the project until explicitly removed. Live troubleshooting has verified
streaming chart generation, publishing, and PNG download against the existing nano
deployment; no infrastructure was provisioned or deployed.

## Local setup (PowerShell, Python 3.13+)

From the repository root:

```powershell
Set-Location .\csv-chart-analysis-hosted\src\csv-chart-analyst-hosted
uv sync --frozen
Copy-Item .env.example .env
```

The agent's own `.venv` is used for debugging, independently of the prompt solution.
The sample environment already points to the selected existing project and nano
deployment. Authenticate with your existing Azure identity if necessary; no secrets
belong in source control. The identity needs permission to invoke the model and use
the project's Files and Code Interpreter APIs. Authentication/authorization failures
are surfaced rather than triggering provisioning or role assignment.

## Agent Inspector

Open the **repository root** in VS Code. Select **Debug Caldova Hosted Agent** and
press **F5**. The root [tasks.json](../.vscode/tasks.json) checks ports 5679 and 8088,
starts this agent with `debugpy`, and opens Foundry Toolkit Agent Inspector inside
VS Code. Breakpoints can be set in `build_agent` and `publish_output`.
Set a startup breakpoint before F5 to inspect initial configuration.

This agent already exposes HTTP `/responses` and `/readiness` endpoints through
`ResponsesHostServer`. Agent Inspector works with that server directly; `agentdev`
is not required. The currently published `agent-dev-cli` version `0.0.1b260427`
requires `agent-framework-core>=1.1.1,<1.3.0`, whereas this project's hosting SDK
requires `agent-framework-core>=1.13.0`. Installing them together is unsupported.
Do not bypass dependency checks or downgrade the working hosting stack just to add
the CLI. The F5 configuration therefore launches the HTTP entrypoint with `debugpy`.

Example request:

> Validate the bundled CSV, summarize revenue and gross margin by region, and create
> a PNG showing monthly revenue by category. Return the downloadable file ID.

For terminal-only local operation:

```powershell
.\.venv\Scripts\python.exe main.py
```

Stop the debug server with **Tasks: Terminate Task** when finished. F5 does not
provision or deploy. Reply **Deploy Agent to Foundry** only when ready for a separate,
explicit deployment workflow. The manifest references the existing project and
does not declare a new model deployment.

### Troubleshooting publishing errors

If Inspector shows `publish_output` receiving `container_id: "assistant"` or a PNG
filename as `file_id`, that is an old tool schema with fabricated IDs, not evidence
that a chart was generated. Stop the existing **Run Caldova hosted agent** task,
press **F5**, and start a fresh Inspector conversation to load the updated schema.
The model should first run Code Interpreter and then call `publish_output` with
only the exact generated filename.

Publishing without a completed Code Interpreter execution now returns an explicit
instruction to generate the file first. Service failures report their HTTP status,
with details in the server terminal. A green tool-call completion indicator does
not mean that a file was successfully published; check the result for the actual
`File:` and `Download ID:` values.

If Code Interpreter ran but publishing reports `No completed Code Interpreter execution`,
restart the task and start a fresh Inspector conversation. The updated runtime handles
the raw streaming completion event; the installed SDK's parsed result alone remains
`in_progress` and previously prevented recording the real container ID.

## Offline validation

From the agent source directory:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use mocked Azure clients; no live inference, file upload, Code Interpreter
session, provisioning, or deployment is performed.
