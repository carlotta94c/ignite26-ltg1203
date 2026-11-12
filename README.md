# Caldova CSV Chart Analyst

Two independent Microsoft Foundry solutions for the same commercial-analysis scenario:

| Solution | Runtime | Entry point |
| --- | --- | --- |
| [Prompt agent](csv-chart-analyst-prompt/README.md) | Foundry-managed Microsoft Copilot harness | [azure.yaml](csv-chart-analyst-prompt/azure.yaml), [chat.py](csv-chart-analyst-prompt/chat.py) |
| [Hosted agent](csv-chart-analysis-hosted/README.md) | Microsoft Agent Framework, Responses protocol | [azure.yaml](csv-chart-analysis-hosted/azure.yaml), [main.py](csv-chart-analysis-hosted/src/csv-chart-analyst-hosted/main.py) |

The original prompt solution is preserved, including local deployment state and downloaded outputs
(both Git-ignored). Run its existing commands **from `csv-chart-analyst-prompt`**, not the repository root.
The existing root `.venv` is not relocated; recreate a solution-local environment as documented.

The hosted solution is named **csv-chart-analyst-hosted**, targets the existing
**csv-chart-analyst-dev** project, and uses the existing **gpt-5.4-nano** deployment.
It preserves the original instructions verbatim and includes a byte-identical copy of the
synthetic January-June 2026 Caldova CSV. No provisioning or deployment has been performed.

Press **F5** using the hosted-agent configuration after local setup to open Foundry Toolkit Agent
Inspector. Local inference and file operations use the existing Azure project; they are not offline.

The [MIT license](LICENSE) covers the original Microsoft Foundry sample and this adaptation.
