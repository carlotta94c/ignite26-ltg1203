from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Annotated

from agent_framework import (
    Agent,
    ChatContext,
    ChatMiddleware,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationContext,
    tool,
)
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from openai import APIError, APIStatusError, OpenAI
from openai.types.responses import ResponseCodeInterpreterToolCall, ResponseOutputItemDoneEvent
from pydantic import Field

AGENT_NAME = "csv-chart-analyst-hosted"
MODEL_NAME = "gpt-5.4-nano"
ROOT = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)
ARTIFACT_CONTAINER_KEY = "caldova_code_interpreter_container"


class CodeInterpreterArtifacts(ChatMiddleware):
    async def process(
        self, context: ChatContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        def remember_execution(raw: object) -> None:
            if isinstance(raw, ResponseCodeInterpreterToolCall) and raw.status == "completed":
                if context.session is None:
                    raise RuntimeError("Code Interpreter results require an agent session.")
                context.session.state[ARTIFACT_CONTAINER_KEY] = raw.container_id

        def remember(contents: Sequence[Content]) -> None:
            for content in contents:
                if content.type == "code_interpreter_tool_result":
                    remember_execution(content.raw_representation)

        def capture_update(update: ChatResponseUpdate) -> ChatResponseUpdate:
            # The SDK parses the added (in-progress) item, but not its completed snapshot.
            if isinstance(update.raw_representation, ResponseOutputItemDoneEvent):
                remember_execution(update.raw_representation.item)
            remember(update.contents)
            return update

        if context.stream:
            context.stream_transform_hooks.append(capture_update)
        await call_next()
        if isinstance(context.result, ChatResponse):
            for message in context.result.messages:
                remember(message.contents)


def build_agent(
    client: FoundryChatClient, files_client: OpenAI, demo_file_id: str
) -> Agent:
    @tool(approval_mode="never_require")
    def publish_output(
        filename: Annotated[
            str, Field(description="Exact generated output filename, not a file ID.", min_length=1)
        ],
        ctx: FunctionInvocationContext,
    ) -> str:
        """Publish an existing output after Code Interpreter has actually generated it."""
        container_id = ctx.session.state.get(ARTIFACT_CONTAINER_KEY) if ctx.session else None
        if not isinstance(container_id, str) or not container_id:
            message = (
                "No completed Code Interpreter execution is recorded for this session. "
                "Run Code Interpreter to inspect the CSV and generate the requested file "
                "before calling publish_output. Do not invent container or file IDs."
            )
            logger.warning(message)
            raise ValueError(message)
        output_name = Path(filename.replace("\\", "/")).name
        if output_name in {"", ".", ".."}:
            logger.warning("Invalid output filename: %r", filename)
            raise ValueError("Provide the exact generated output filename.")
        try:
            matches = [
                item for item in files_client.containers.files.list(container_id=container_id)
                if item.source == "assistant"
                and Path(item.path.replace("\\", "/")).name == output_name
            ]
            if len(matches) != 1:
                message = (
                    f"Expected one generated output named {output_name!r}; found {len(matches)}. "
                    "Generate a uniquely named file with Code Interpreter, then publish that filename. "
                    "Uploaded source files cannot be published as generated outputs."
                )
                logger.warning(message)
                raise ValueError(message)
            content = files_client.containers.files.content.retrieve(
                matches[0].id, container_id=container_id
            )
            uploaded = files_client.files.create(
                file=(output_name, content.read()), purpose="assistants"
            )
        except APIError as error:
            logger.exception("Foundry output publishing failed for %s", output_name)
            status = f"HTTP {error.status_code}" if isinstance(error, APIStatusError) else "connection error"
            raise ValueError(
                f"Foundry output publishing failed ({status}). Check the server log for details. "
                "If the container expired, regenerate the file with Code Interpreter; "
                "for access errors, check the existing project's file permissions."
            ) from error
        logger.info("Published output %s as Foundry file %s", output_name, uploaded.id)
        return f"File: {output_name}\nDownload ID: {uploaded.id}"

    original_instructions = (ROOT / "instructions.txt").read_text(encoding="utf-8")
    runtime_instructions = f"""

Hosted runtime adapter:
The Code Interpreter tool replaces the Microsoft Copilot harness shell and filesystem.
Use its isolated Python workspace for inspecting CSVs, calculations, and generating charts;
do not claim to have run a local shell. The bundled regional-sales.csv is attached to Code
Interpreter as Foundry file {demo_file_id}. Use it when the user has not supplied another CSV.
For user-supplied CSVs, use the actual attached files instead of silently substituting the demo.
Inspect the workspace to find file paths; never invent their contents.
The runtime-provided output directory is /mnt/data. Keep the source CSV unchanged.
You must actually run Code Interpreter to inspect the CSV and generate a file before trying
to publish it. Do not claim that a chart exists or that data was analyzed without tool evidence.
After generating an output, call publish_output with only the exact filename, for example
monthly_revenue_by_category.png. The runtime resolves the real container and file IDs from
this session's completed Code Interpreter execution; never invent or supply those IDs.
The publisher does not create charts and cannot substitute for Code Interpreter.
Preserve native file citations
when available, and always include the exact File: and Download ID: returned by publish_output.
Never present a sandbox path, a container file ID, or the source CSV ID as a downloadable
Foundry output file ID. If publishing fails, report the error rather than inventing a link.
"""
    return Agent(
        client=client,
        name=AGENT_NAME,
        instructions=original_instructions + runtime_instructions,
        tools=[
            FoundryChatClient.get_code_interpreter_tool(file_ids=[demo_file_id]),
            publish_output,
        ],
        default_options={"store": False},
        middleware=[CodeInterpreterArtifacts()],
    )


def main() -> None:
    load_dotenv(ROOT / ".env")
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip()
    if not endpoint:
        raise ValueError("Set FOUNDRY_PROJECT_ENDPOINT in the agent's .env before starting.")
    model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", MODEL_NAME).strip()
    if not model:
        raise ValueError("AZURE_AI_MODEL_DEPLOYMENT_NAME must not be empty.")

    with DefaultAzureCredential() as credential:
        with AIProjectClient(endpoint=endpoint, credential=credential) as project:
            with project.get_openai_client() as files_client:
                with (ROOT / "sample-data" / "regional-sales.csv").open("rb") as source:
                    demo = files_client.files.create(file=source, purpose="assistants")
                logger.info("Staged bundled CSV as Foundry file %s", demo.id)
                client = FoundryChatClient(
                    project_endpoint=endpoint,
                    model=model,
                    credential=credential,
                    function_invocation_configuration={"include_detailed_errors": True},
                )
                agent = build_agent(client, files_client, demo.id)
                ResponsesHostServer(agent).run(
                    host=os.environ.get("HOST", "0.0.0.0"),
                    port=int(os.environ.get("PORT", "8088")),
                )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
