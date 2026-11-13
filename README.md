# Caldova CSV Chart Analyst

Two independent Microsoft Foundry solutions for the same commercial-analysis scenario:

| Solution | Runtime | Entry point |
| --- | --- | --- |
| [Prompt agent](csv-chart-analyst-prompt/README.md) | Foundry-managed Microsoft Copilot harness | [azure.yaml](csv-chart-analyst-prompt/azure.yaml), [chat.py](csv-chart-analyst-prompt/chat.py) |
| [Hosted agent](csv-chart-analysis-hosted/README.md) | Microsoft Agent Framework, Responses protocol | [azure.yaml](csv-chart-analysis-hosted/azure.yaml), [main.py](csv-chart-analysis-hosted/src/csv-chart-analyst-hosted/main.py) |

## Choose a solution

| | Prompt solution | Hosted solution |
| --- | --- | --- |
| Agent name | `csv-chart-analyst` | `csv-chart-analyst-hosted` |
| Analysis runtime | Managed Copilot harness with shell and filesystem tools | Custom Python agent with Foundry Code Interpreter |
| Model deployment | `gpt-5.4-nano` | Reuses the existing `gpt-5.4-nano` |
| Input workflow | Included client uploads and attaches a CSV | Bundled CSV uploaded at startup; user attachments take precedence |
| Chart output | Included client downloads PNGs to `outputs/` | Publishes PNGs to Foundry Files and returns downloadable file IDs |
| Local development | Python client calls the deployed agent | Run/debug the agent in VS Code Agent Inspector |
| Deployment | Can provision a project and model | Source-code deployment to the existing `csv-chart-analyst-dev` project |

Both preserve the Caldova commercial-analysis instructions and the same synthetic January-June
2026 sales data. They are independent: neither solution imports the other's code or deployment state.
The agents analyze sales, costs, and gross margins; they do not provide medical advice.

## Prompt agent

Follow the [prompt setup and deployment guide](csv-chart-analyst-prompt/README.md) to provision
the managed-harness agent and run the complete CSV-upload-to-PNG-download workflow.
Run its commands **from `csv-chart-analyst-prompt`**, not the repository root.

## Hosted agent

Follow the [hosted setup and deployment guide](csv-chart-analysis-hosted/README.md) for:

1. Python 3.13+ and `uv` setup in the hosted agent's own virtual environment.
2. Local debugging: open the repository root in VS Code, select **Debug Caldova Hosted Agent**,
   and press **F5** to open Foundry Toolkit Agent Inspector.
3. Source-code deployment to the existing Foundry project without Docker or ACR.
4. Status checks, remote invocation, generated-file downloads, and session logs.
5. Troubleshooting `424 session_not_ready` and startup permission failures.

The hosted manifest includes a post-deploy hook that grants its deployed agent identity
**Foundry User** at project scope if needed. The deploying identity must be allowed to assign
that role. The hook runs only during deployment, not F5 debugging.

## Azure usage and cleanup

Neither solution's interactive workflow is offline: inference, Code Interpreter, and file operations
use Azure and can incur charges. The hosted agent uploads its demo CSV whenever a runtime starts.
Uploaded inputs and generated outputs remain in Foundry until explicitly deleted.

Credentials, local deployment state, virtual environments, and downloaded outputs are Git-ignored.
Use solution-local environments; an existing root `.venv` is not used by the hosted debug task.
Never commit credentials or private CSV data.

The hosted solution shares the existing project and model. Do not delete that project or run
environment-wide teardown commands just to stop the hosted agent; other agents can depend on it.

The [MIT license](LICENSE) covers the original Microsoft Foundry sample and this adaptation.
