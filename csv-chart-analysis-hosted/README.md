# Caldova CSV Chart Analyst - hosted agent

Independent hosted solution named **csv-chart-analyst-hosted**, using the existing
**gpt-5.4-nano** deployment in **csv-chart-analyst-dev**. No provisioning, deployment,
model creation, connection creation, or role changes are performed by local setup.
The prompt solution is unchanged and is not imported at runtime.

## Prerequisites

- Python 3.13+ and [uv](https://docs.astral.sh/uv/getting-started/installation/) for local setup.
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli), authenticated to the
  tenant and subscription that contain the existing project.
- Azure Developer CLI (`azd`) 1.37.0+ with `azure.ai.agents` and `azure.ai.projects`
  extensions 1.0.0+ for deployment. Install the extension pack with
  `azd extension install microsoft.foundry`.
- PowerShell 7 (`pwsh`) on Windows, or a POSIX shell on macOS/Linux, for the deployment hook.
- VS Code with Foundry Toolkit and the Python Debugger extension for the included F5 workflow.
- Permission to deploy hosted agents and assign **Foundry User** at project scope.
  The local development identity also needs access to inference, Files, and Code Interpreter.
  The hosted agent uses its own identity, not your local Azure CLI identity.

The [manifest](azure.yaml) uses Python source-code deployment with remote dependency installation.
Docker and Azure Container Registry are not required. The included
[Dockerfile](src/csv-chart-analyst-hosted/Dockerfile) is not used by this deployment path.

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
streaming chart generation, publishing, and PNG download against the existing nano deployment.

## Local setup (PowerShell, Python 3.13+)

From the repository root:

```powershell
Set-Location .\csv-chart-analysis-hosted\src\csv-chart-analyst-hosted
uv sync --frozen
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
}
az login
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

## Deploy to the existing Foundry project

These commands deploy code and can change role assignments through the post-deploy hook.
They are separate from local setup. Do not run `azd up` or `azd provision` for this path:
the project and `gpt-5.4-nano` deployment already exist.

### Configure the azd environment

Start from the repository root in PowerShell. Replace `<subscription-id>` with the
subscription containing `rg-csv-chart-analyst-dev`:

```powershell
Set-Location .\csv-chart-analysis-hosted
$env:AZURE_DEV_USER_AGENT = "microsoft_foundry_skill"

az login
az account set --subscription "<subscription-id>"
azd auth login
azd env list
azd env select csv-chart-analyst-hosted-dev
```

If that local azd environment does not exist, use
`azd env new csv-chart-analyst-hosted-dev --subscription "<subscription-id>"` instead
of `azd env select`. Creating the local environment does not provision Azure resources.
Both CLIs must be signed in to the intended tenant.

From the same solution folder:

```powershell
$subscriptionId = az account show --query id --output tsv
$tenantId = az account show --query tenantId --output tsv
$accountId = az cognitiveservices account show `
    --name cog-aq3tdqdbwqe2u `
    --resource-group rg-csv-chart-analyst-dev `
    --subscription $subscriptionId `
    --query id --output tsv

if ($LASTEXITCODE -ne 0 -or -not $accountId) {
    throw "Existing Foundry account not found. Check the subscription and tenant."
}

$projectId = "$accountId/projects/csv-chart-analyst-dev"

azd env set AZURE_SUBSCRIPTION_ID $subscriptionId
azd env set AZURE_TENANT_ID $tenantId
azd env set AZURE_AI_PROJECT_ID $projectId
azd env set FOUNDRY_PROJECT_ENDPOINT "https://cog-aq3tdqdbwqe2u.services.ai.azure.com/api/projects/csv-chart-analyst-dev"
azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME "gpt-5.4-nano"
```

These resource names are for this sample's existing project. If targeting another project,
also update the `ai-project.endpoint` in [azure.yaml](azure.yaml) to match.
The source directory's local `.env` does not configure azd and is excluded from deployment.
Use `FOUNDRY_PROJECT_ENDPOINT` as shown; the manifest does not substitute
`AZURE_AI_PROJECT_ENDPOINT`.

### Preview, deploy, and test

Run from `csv-chart-analysis-hosted`:

```powershell
# Read-only comparison: does not deploy or execute hooks.
azd deploy csv-chart-analyst-hosted --preview --no-prompt

# Deploys a new hosted-agent version and runs the role-assignment hook.
azd deploy csv-chart-analyst-hosted --no-prompt

azd ai agent show csv-chart-analyst-hosted --output json
```

The CLI packages [the source directory](src/csv-chart-analyst-hosted), excludes
[.agentignore](src/csv-chart-analyst-hosted/.agentignore) entries, and lets Foundry install
dependencies from [requirements.txt](src/csv-chart-analyst-hosted/requirements.txt).
Each deployment creates a new immutable version. A version with status `active` still
needs a successful session startup and invocation to verify runtime readiness.

After allowing time for role propagation, invoke the deployed agent:

```powershell
azd ai agent invoke csv-chart-analyst-hosted `
    "Validate the bundled Caldova CSV, summarize revenue and gross margin by region, and create a PNG showing monthly revenue by category. Return the downloadable file ID." `
    --protocol responses `
    --new-session
```

Expect an analysis response and real `File:` and `Download ID:` values for the PNG.
Invocation uses the remote hosted agent and incurs normal Azure usage charges.
See Microsoft's [source-code deployment guide](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent-code)
for platform details.

### Download a generated chart

Unlike the prompt solution's client, this solution does not automatically save outputs
to a local `outputs/` directory. From the hosted solution folder, replace `<file-id>`
with the returned Foundry file ID:

```powershell
$projectEndpoint = azd env get-value FOUNDRY_PROJECT_ENDPOINT
$token = az account get-access-token --resource https://ai.azure.com --query accessToken --output tsv
if ($LASTEXITCODE -ne 0 -or -not $token) {
    throw "Failed to obtain an Azure access token."
}

Invoke-WebRequest `
    -Uri "$projectEndpoint/openai/v1/files/<file-id>/content" `
    -Headers @{ Authorization = "Bearer $token" } `
    -OutFile (Join-Path $env:TEMP "caldova-chart.png")

Remove-Variable token
```

The caller downloading the file needs project Files access independently of the hosted
agent's role. Treat file IDs and downloaded files according to your data-handling requirements.

### Deployment permissions

The [manifest](azure.yaml) uses the same post-deploy hook pattern as the prompt
solution, with independent [PowerShell](scripts/assign-agent-role.ps1) and
[POSIX shell](scripts/assign-agent-role.sh) scripts. After an explicitly requested
deployment, the hook resolves this hosted agent's identity and assigns **Foundry User**
at the existing project's scope, unless that role is already assigned there or
inherited. It does not create projects or model deployments. The deploying identity
must be allowed to assign that role; failures stop the hook and are reported.

The hook reads `FOUNDRY_PROJECT_ENDPOINT`, `AZURE_AI_PROJECT_ID`, and the generated
`AGENT_CSV_CHART_ANALYST_HOSTED_NAME` from the active azd environment. The manifest
also passes `FOUNDRY_PROJECT_ENDPOINT` to the runtime; the local `.env` is not deployed.
Local F5 debugging does not run the hook.

If deployment succeeds but the hook fails, resolve the reported permissions or configuration
issue and rerun only the hook from the hosted solution folder:

```powershell
$env:AZURE_DEV_USER_AGENT = "microsoft_foundry_skill"
pwsh -File .\scripts\assign-agent-role.ps1
```

On macOS/Linux, use `sh ./scripts/assign-agent-role.sh` from the same folder.
These commands can assign a role; they do not redeploy the agent. The scripts check for
an existing assignment before creating one.

### Startup and readiness troubleshooting

If a deployed session fails with `424 session_not_ready`, check its console logs.
From the hosted solution folder:

```powershell
azd ai agent monitor csv-chart-analyst-hosted --tail 100
```

The command uses the session saved by the last invocation. To inspect the session
reported in an error instead:

```powershell
azd ai agent monitor csv-chart-analyst-hosted --session-id "<session-id>" --tail 100
azd ai agent monitor csv-chart-analyst-hosted --session-id "<session-id>" --type system --tail 100
```

A `403` while uploading the bundled CSV means the hosted identity lacks access:
startup exits before the readiness server starts. After fixing the role assignment,
allow time for RBAC propagation and invoke with a new session. A successful role
assignment does not mean permissions are effective immediately.

If logs report a missing `FOUNDRY_PROJECT_ENDPOINT`, verify that the active azd environment
contains it and that the deployment preview shows a nonempty runtime value, then redeploy.
Changing only the local `.env` cannot fix an already deployed version.
For other failures, use the actual startup traceback rather than assuming every `424`
is a permissions error.

## Data retention and cleanup

The bundled CSV contains synthetic Caldova commercial data, not patient data. Model-generated
analysis code runs in Foundry Code Interpreter, not on your workstation.
Uploaded inputs and published outputs are not deleted automatically. Stop local debugging
with **Tasks: Terminate Task**; this does not remove Azure files or a deployed agent.

This solution reuses a project and model that other agents can share. Do not run
`azd down --purge` or delete the project as hosted-agent cleanup. Remove only the intended
hosted-agent versions and project files after checking that no other workflow needs them.

## Offline validation

From the agent source directory:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use mocked Azure clients; no live inference, file upload, Code Interpreter
session, provisioning, or deployment is performed.
