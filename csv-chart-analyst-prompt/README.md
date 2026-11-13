# ignite26-ltg1203 - CSV Chart Analyst

This standalone repository contains the
[CSV Chart Analyst sample from microsoft-foundry/foundry-samples](https://github.com/microsoft-foundry/foundry-samples/tree/46de18c3b4f1bd861646fa063f29c3c8a039b942/samples/cli/prompt-agents/microsoft-copilot-harness/csv-chart-analyst).
The original MIT license is included in [LICENSE](../LICENSE). No checkout of the samples repository
is needed: the client, dependency manifest, deployment manifest, role-assignment hooks, infrastructure
provider placeholder, and example CSV are all included here.

A Microsoft Copilot harness Prompt Agent sample for a complete native file workflow: upload a CSV,
attach it to a request, analyze it in the harness workspace, generate a PNG, upload the output to
Foundry Files, and download it through the client.

The demo scenario is **Caldova**, a pharmaceutical company selling drugs and cosmetics.
[regional-sales.csv](sample-data/regional-sales.csv) includes all 40 supplied catalog products
across Sun Protection, Cold and Flu, Cosmetics, and Vitamins, with January-June 2026 sales in
North, South, East, and West (960 monthly region-product records).
Product IDs, names, categories, and unit prices match the supplied catalog. Units sold and unit
costs are synthetic demo assumptions, not actual Caldova performance. Revenue is `units * unit_price`
and cost is `units * unit_cost`; all monetary values use one unspecified demo currency because the
catalog does not identify a currency. The original `month,region,product,revenue,cost,units` columns
are retained, with `product_id,category,unit_price,unit_cost` added for product-level analysis.
The Agent focuses on commercial insights and charts, not medical advice or product efficacy claims.

One `azd up` creates:

- a Microsoft Foundry project and `gpt-5.4-nano` deployment;
- one Prompt Agent using `harness.type: microsoft_copilot_preview`;
- native file-read, file-write, and shell capabilities with all other harness built-ins disabled;
  and
- a post-deploy role assignment granting the Agent identity **Foundry User** at project scope.

## Prerequisites

- Azure Developer CLI (`azd`) 1.36.0 or later with the Microsoft Foundry extension pack. The sample
  requires at least `azure.ai.agents` 1.0.0-beta.21 and `azure.ai.projects` 1.0.0-beta.14.
- Azure CLI authenticated to the target tenant.
- Python 3.9 or later.
- PowerShell 7 (`pwsh`) on Windows, or a POSIX shell on macOS/Linux, for the post-deploy hook.
- An Azure subscription with Copilot harness preview access and sufficient quota for the
  `gpt-5.4-nano` GlobalStandard deployment (capacity 50) in the selected region.
- Permission to create Foundry resources, deploy Agent versions, and assign **Foundry User** at
  project scope. The signed-in Azure CLI user also needs access to the project's Files and Agent APIs.

The client dependencies are pinned in [requirements.txt](requirements.txt). Chart generation runs
in the managed Azure harness, not on your computer; the agent installs a plotting package there if
needed. Docker, Node.js, a local model, and a local plotting package are not required.

Install [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd)
and [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) before deployment.
For an existing `azd` installation older than 1.36.0, run `azd update`. Install the Foundry extension
pack with `azd extension install microsoft.foundry`, then check `azd extension list` against the
minimum versions above. If the installed extensions are older, run
`azd extension update azure.ai.agents` and `azd extension update azure.ai.projects`.

**Before running `azd up`, you must set `AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS=true`**
in the shell or CI job environment, as shown below. This also applies to `azd package`.
Without it, deployment fails with `preview feature "agents.managed_harness" is disabled`.
Repeat the shell assignment in each new terminal unless you have persisted this environment variable.

## Windows quick start

From this solution folder (`csv-chart-analyst-prompt`) in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

az login
azd auth login
azd env new rg-csv-chart-analyst-dev --subscription <subscription-id> --location canadacentral
$env:AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS = "true"
azd up --no-prompt

.\.venv\Scripts\python.exe chat.py .\sample-data\regional-sales.csv `
  --prompt "Analyze Caldova's synthetic sales, identify the leading region and product category by revenue, calculate gross profit, and create a grouped bar chart of monthly revenue by category as a downloadable PNG."
```

Replace `<subscription-id>` before running the command. Both `az` and `azd` must be signed in to
the target tenant. Using the virtual environment's interpreter directly avoids changing PowerShell
execution policy. Omit `--prompt` to chat interactively; PNG files are saved in `outputs/`.

Deployment creates billable Azure resources. This is not an offline/local agent. Local credentials,
`.azure/` deployment state, virtual environments, and downloaded outputs are excluded from Git.
Never commit credentials or private CSV data.

## Deploy

From this folder in Bash (for PowerShell, use the Windows quick start above):

```bash
az login
azd auth login
azd env new rg-csv-chart-analyst-dev --subscription <subscription-id> --location canadacentral
export AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS=true
azd up --no-prompt
```

Canada Central is the sample default. Replace `canadacentral` with another supported Foundry region
when needed.

The `postdeploy` hook retrieves the created Agent's `instance_identity.principal_id` and assigns
**Foundry User** on the Foundry project. It doesn't assign the role to the project managed identity.
The role can take several minutes to propagate; retry from a new conversation if output upload
initially returns HTTP 403.

If deployment completes but the hook fails, fix the reported issue and rerun only the hook.
On Windows, use `pwsh -File .\scripts\assign-agent-role.ps1`. The hook checks for an existing
role assignment before creating one, so rerunning it does not create duplicate assignments.

## Run the complete workflow

Create a virtual environment and install the client dependencies:

**PowerShell (Windows):**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

This uses the virtual environment directly without activation or execution-policy changes.
If `.venv` already exists, skip the creation command.

**Bash (macOS/Linux):**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Start an interactive conversation:

**PowerShell (Windows):**

```powershell
.\.venv\Scripts\python.exe chat.py .\sample-data\regional-sales.csv
```

**Bash (macOS/Linux):**

```bash
python chat.py sample-data/regional-sales.csv
```

Try:

```text
Summarize Caldova's synthetic sales and identify the leading region and product category by revenue.
Compare monthly revenue for Cold and Flu and Sun Protection from January through June.
Which cosmetics products have the highest gross profit? Calculate gross margin using total revenue and cost.
Create a grouped bar chart of monthly revenue by category. Return it as a downloadable PNG file.
```

Generated files are downloaded to `outputs/`.

While analysis runs, the client prints the Foundry response ID, status changes, and an elapsed-time
update every 15 seconds while polling (network requests can delay updates). It prints the final
answer and downloads only after the response completes; these updates are not streamed model text.
The client stops polling after 15 minutes and reports the response ID and last status. This stops
local waiting, not the remote analysis. Avoid immediately starting duplicate requests.

For a repeatable non-interactive test:

**PowerShell (Windows):**

```powershell
.\.venv\Scripts\python.exe chat.py .\sample-data\regional-sales.csv `
  --prompt "Analyze Caldova's synthetic sales, identify the leading region and product category by revenue, calculate gross profit, and create a grouped bar chart of monthly revenue by category as a downloadable PNG."
```

**Bash (macOS/Linux):**

```bash
python chat.py sample-data/regional-sales.csv \
  --prompt "Analyze Caldova's synthetic sales, identify the leading region and product category by revenue, calculate gross profit, and create a grouped bar chart of monthly revenue by category as a downloadable PNG."
```

The client uploads the CSV to the project Files API and includes its file ID as an `input_file` in
the Responses request. The harness stages the attachment before the model starts. The Agent uses
shell and Python to inspect the CSV. If the plotting library is missing, it installs the minimum
package with pip, writes the PNG to the runtime-provided output directory, and uploads it to Foundry
Files.

The client prefers a project `file_citation` annotation. It also supports builds that return the
uploaded filename and Foundry file ID as `File` and `Download ID` text. It downloads either form
through the project Files API, not the Code Interpreter Containers API.

## Data and security behavior

- The CSV is uploaded with purpose `assistants` to the project-scoped Files API.
- The CSV is attached to each turn in the same conversation. The runtime deduplicates the staged
  file and keeps it available for follow-up analysis.
- Native web and subagent tools are disabled. Shell is enabled, so disabling the separate filesystem
  controls isn't a strict security boundary.
- The output upload runs as the Agent identity and requires **Foundry User** at project scope.
- Uploaded input and generated output files are not deleted automatically. Clean them up according
  to your data-retention requirements.
- The sample doesn't configure or invoke Code Interpreter.

## Supported surfaces

| Step | Included client |
| --- | --- |
| Upload CSV to Foundry Files | Yes |
| Stage `input_file` in the harness workspace | Yes |
| Analyze across turns | Yes |
| Install a missing plotting package | Yes |
| Generate and upload PNG | Yes |
| Download PNG from Foundry Files | Yes |

Portal attachment filtering and generated-file UI behavior can differ by rollout. This sample uses
the included client and does not require the Portal file UI.

The client sends `Foundry-Features: MicrosoftCopilot=V1Preview` for the Copilot harness
preview endpoint. Review the preview API contract before adapting this client for production.

The client reads `FOUNDRY_PROJECT_ENDPOINT` from the current `azd` environment. To use an already
deployed compatible `csv-chart-analyst` agent instead, pass
`--project-endpoint https://<account>.services.ai.azure.com/api/projects/<project>` or set
`FOUNDRY_PROJECT_ENDPOINT` in the process environment. Azure CLI authentication is still required.

## Tear down

Delete the complete isolated environment after testing:

```bash
azd down --purge --force --no-prompt
```

The client doesn't delete individual input or output files. Purging the isolated project removes its
Foundry Files; delete local files under `outputs/` separately when they are no longer needed.
