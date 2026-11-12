from __future__ import annotations

import json
import os
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

os.environ["OTEL_SDK_DISABLED"] = "true"

from agent_framework import (
    AgentResponseUpdate,
    AgentSession,
    ChatContext,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationContext,
    Message,
)
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.core.credentials import TokenCredential
import httpx
from openai import NotFoundError
from openai.types.responses import ResponseCodeInterpreterToolCall, ResponseOutputItemDoneEvent
from starlette.testclient import TestClient

import main

REPOSITORY = main.ROOT.parents[2]


def offline_client() -> FoundryChatClient:
    return FoundryChatClient(
        project_endpoint="https://example.services.ai.azure.com/api/projects/test",
        model=main.MODEL_NAME,
        credential=Mock(spec=TokenCredential),
        function_invocation_configuration={"include_detailed_errors": True},
    )


class PreservationTests(unittest.TestCase):
    def test_instructions_match_original_verbatim(self) -> None:
        source = (REPOSITORY / "csv-chart-analyst-prompt" / "azure.yaml").read_text(
            encoding="utf-8"
        )
        original = textwrap.dedent(
            source.split("    instructions: |\n", 1)[1].split("    tools:", 1)[0]
        )
        self.assertEqual(
            (main.ROOT / "instructions.txt").read_text(encoding="utf-8"), original
        )

    def test_csv_is_byte_identical(self) -> None:
        source = REPOSITORY / "csv-chart-analyst-prompt" / "sample-data" / "regional-sales.csv"
        self.assertEqual(
            (main.ROOT / "sample-data" / "regional-sales.csv").read_bytes(),
            source.read_bytes(),
        )

    def test_debug_paths_model_and_inspector(self) -> None:
        tasks = json.loads((REPOSITORY / ".vscode" / "tasks.json").read_text())
        launch = json.loads((REPOSITORY / ".vscode" / "launch.json").read_text())
        run = next(t for t in tasks["tasks"] if t["label"] == "Run Caldova hosted agent")
        command = run["command"].replace("${workspaceFolder}", str(REPOSITORY))
        self.assertTrue(Path(command).is_file())
        self.assertEqual(
            run["options"]["env"]["AZURE_AI_MODEL_DEPLOYMENT_NAME"], main.MODEL_NAME
        )
        self.assertEqual(run["args"][-1], "main.py")
        self.assertEqual(launch["configurations"][0]["connect"]["port"], 5679)
        inspector = next(i for i in tasks["inputs"] if i["id"] == "openCaldovaInspector")
        self.assertEqual(inspector["command"], "ai-mlstudio.openTestTool")
        self.assertEqual(inspector["args"]["port"], 8088)


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.files = Mock()
        self.agent = main.build_agent(offline_client(), self.files, "file-demo")
        self.publish = next(
            t for t in self.agent.default_options["tools"]
            if getattr(t, "name", None) == "publish_output"
        )
        self.session = AgentSession()
        self.session.state[main.ARTIFACT_CONTAINER_KEY] = "cntr-test"
        self.files.containers.files.list.return_value = [
            SimpleNamespace(id="cfile-generated", path="/mnt/data/revenue.png", source="assistant")
        ]

    async def invoke_publish(self, filename: str, session: AgentSession | None = None):
        arguments = {"filename": filename}
        return await self.publish.invoke(
            arguments=arguments,
            context=FunctionInvocationContext(
                function=self.publish, arguments=arguments, session=session or self.session
            ),
        )

    async def test_publisher_requests_filename_not_provider_ids(self) -> None:
        schema = self.publish.parameters()
        self.assertEqual(set(schema["properties"]), {"filename"})
        self.assertEqual(schema["required"], ["filename"])

    async def test_agent_has_original_instructions_and_grounded_interpreter(self) -> None:
        self.assertEqual(self.agent.name, "csv-chart-analyst-hosted")
        instructions = self.agent.default_options["instructions"]
        self.assertTrue(
            instructions.startswith((main.ROOT / "instructions.txt").read_text())
        )
        interpreter = self.agent.default_options["tools"][0]
        self.assertEqual(interpreter["type"], "code_interpreter")
        self.assertEqual(interpreter["container"]["file_ids"], ["file-demo"])
        self.assertFalse(self.agent.default_options["store"])

    async def test_publish_output_returns_actual_foundry_file_id(self) -> None:
        self.files.containers.files.content.retrieve.return_value.read.return_value = b"PNG"
        self.files.files.create.return_value.id = "file-output"
        result = await self.invoke_publish("revenue.png")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "File: revenue.png\nDownload ID: file-output")
        self.files.containers.files.content.retrieve.assert_called_once_with(
            "cfile-generated", container_id="cntr-test"
        )
        self.files.files.create.assert_called_once_with(
            file=("revenue.png", b"PNG"), purpose="assistants"
        )

    async def test_publish_failure_is_not_reported_as_success(self) -> None:
        self.files.containers.files.list.side_effect = NotFoundError(
            "container expired",
            response=httpx.Response(
                404, request=httpx.Request("GET", "https://example.invalid/containers/cntr-test")
            ),
            body=None,
        )
        with self.assertRaisesRegex(ValueError, "HTTP 404"):
            await self.invoke_publish("revenue.png")
        self.files.files.create.assert_not_called()

    async def test_premature_publish_explains_required_code_execution(self) -> None:
        with self.assertRaisesRegex(ValueError, "Run Code Interpreter"):
            await self.invoke_publish("monthly_revenue_by_category.png", AgentSession())
        self.files.containers.files.list.assert_not_called()

    async def test_premature_publish_returns_actionable_error_to_model(self) -> None:
        responses = iter([
            ChatResponse(messages=[Message(role="assistant", contents=[
                Content.from_function_call(
                    call_id="call-premature", name="publish_output",
                    arguments={"filename": "monthly_revenue_by_category.png"},
                )
            ])]),
            ChatResponse(messages=[Message(
                role="assistant", contents=[Content.from_text("Generation is required.")]
            )]),
        ])
        calls = 0

        async def raw_response(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                history = json.dumps([
                    message.to_dict() for message in kwargs["messages"]
                ], default=str)
                self.assertIn("Run Code Interpreter", history)
                self.assertNotIn('"result": "Error: Function failed."', history)
            return next(responses)

        with patch.object(self.agent.client, "_inner_get_response", side_effect=raw_response):
            await self.agent.run("Publish the chart", session=AgentSession())
        self.assertEqual(calls, 2)
        self.files.containers.files.list.assert_not_called()

    async def test_uploaded_csv_cannot_be_published_as_generated_output(self) -> None:
        self.files.containers.files.list.return_value = [
            SimpleNamespace(id="file-demo", path="/mnt/data/regional-sales.csv", source="user")
        ]
        with self.assertRaisesRegex(ValueError, "Uploaded source files"):
            await self.invoke_publish("regional-sales.csv")
        self.files.files.create.assert_not_called()

    async def test_missing_and_ambiguous_outputs_are_not_guessed(self) -> None:
        for matches in ([], self.files.containers.files.list.return_value * 2):
            with self.subTest(matches=len(matches)):
                self.files.containers.files.list.return_value = matches
                with self.assertRaisesRegex(ValueError, "Expected one generated output"):
                    await self.invoke_publish("revenue.png")
        self.files.files.create.assert_not_called()

    async def test_request_without_container_does_not_reuse_another_session(self) -> None:
        await self.invoke_publish("revenue.png")
        with self.assertRaisesRegex(ValueError, "No completed Code Interpreter"):
            await self.invoke_publish("revenue.png", AgentSession())
        self.files.containers.files.list.assert_called_once_with(container_id="cntr-test")

    async def check_model_loop(self, stream: bool) -> None:
        self.files.files.create.return_value.id = "file-output"
        self.files.containers.files.content.retrieve.return_value.read.return_value = b"PNG"
        execution = ResponseCodeInterpreterToolCall(
            id="ci-test", type="code_interpreter_call", status="completed",
            code="generate_chart()", container_id="cntr-from-service", outputs=[],
        )
        provider_contents = iter([
            [
                Content.from_code_interpreter_tool_result(
                    call_id="ci-test", raw_representation=execution
                ),
                Content.from_function_call(
                    call_id="call-test", name="publish_output", arguments={"filename": "revenue.png"}
                ),
            ],
            [Content.from_text("Chart published.")],
        ])

        def raw_response(**kwargs):
            contents = next(provider_contents)
            if kwargs["stream"]:
                async def updates():
                    if contents[0].type == "code_interpreter_tool_result":
                        yield ChatResponseUpdate(
                            role="assistant",
                            contents=[Content.from_code_interpreter_tool_result(
                                call_id=execution.id,
                                raw_representation=execution.model_copy(
                                    update={"status": "in_progress", "code": None}
                                ),
                            )],
                        )
                        # The installed SDK leaves this completion event's contents empty.
                        yield ChatResponseUpdate(
                            role="assistant",
                            raw_representation=ResponseOutputItemDoneEvent(
                                type="response.output_item.done", output_index=0,
                                sequence_number=3, item=execution,
                            ),
                        )
                        contents_to_emit = contents[1:]
                    else:
                        contents_to_emit = contents
                    yield ChatResponseUpdate(
                        role="assistant", contents=contents_to_emit, response_id="resp-test",
                        message_id="msg-test",
                    )
                return self.agent.client._build_response_stream(updates())

            async def response():
                return ChatResponse(messages=[Message(role="assistant", contents=contents)])
            return response()

        with patch.object(self.agent.client, "_inner_get_response", side_effect=raw_response):
            if stream:
                updates = [
                    update async for update in self.agent.run(
                        "Generate and publish revenue.png", stream=True, session=AgentSession()
                    )
                ]
                self.assertTrue(updates)
            else:
                await self.agent.run(
                    "Generate and publish revenue.png", session=AgentSession()
                )
        self.files.containers.files.list.assert_called_once_with(
            container_id="cntr-from-service"
        )
        self.files.containers.files.content.retrieve.assert_called_once_with(
            "cfile-generated", container_id="cntr-from-service"
        )
        self.files.files.create.assert_called_once_with(
            file=("revenue.png", b"PNG"), purpose="assistants"
        )

    async def test_model_loop_resolves_actual_code_interpreter_ids(self) -> None:
        await self.check_model_loop(stream=False)

    async def test_streaming_model_loop_resolves_actual_code_interpreter_ids(self) -> None:
        await self.check_model_loop(stream=True)

    async def test_unfinished_streamed_execution_is_not_recorded(self) -> None:
        for status in ("in_progress", "failed", "incomplete"):
            with self.subTest(status=status):
                session = AgentSession()
                execution = ResponseCodeInterpreterToolCall(
                    id="ci-unfinished", type="code_interpreter_call", status=status,
                    code="generate_chart()", container_id="cntr-unfinished", outputs=[],
                )
                context = ChatContext(
                    client=self.agent.client, messages=[], options={},
                    session=session, stream=True,
                )

                async def next_response():
                    update = ChatResponseUpdate(
                        role="assistant",
                        raw_representation=ResponseOutputItemDoneEvent(
                            type="response.output_item.done", output_index=0,
                            sequence_number=3, item=execution,
                        ),
                    )
                    for hook in context.stream_transform_hooks:
                        hook(update)

                await main.CodeInterpreterArtifacts().process(context, next_response)
                self.assertNotIn(main.ARTIFACT_CONTAINER_KEY, session.state)

    async def test_failed_interpreter_execution_is_not_recorded(self) -> None:
        session = AgentSession()
        execution = ResponseCodeInterpreterToolCall(
            id="ci-failed", type="code_interpreter_call", status="failed",
            code="bad_code()", container_id="cntr-failed", outputs=[],
        )
        context = ChatContext(
            client=self.agent.client, messages=[], options={}, session=session
        )

        async def next_response():
            context.result = ChatResponse(messages=[Message(
                role="assistant", contents=[Content.from_code_interpreter_tool_result(
                    call_id="ci-failed", raw_representation=execution
                )]
            )])

        await main.CodeInterpreterArtifacts().process(context, next_response)
        self.assertNotIn(main.ARTIFACT_CONTAINER_KEY, session.state)


class StartupTests(unittest.TestCase):
    def test_missing_endpoint_fails_before_network(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch.object(main, "load_dotenv"):
            with patch.object(main, "AIProjectClient") as project:
                with self.assertRaisesRegex(ValueError, "FOUNDRY_PROJECT_ENDPOINT"):
                    main.main()
                project.assert_not_called()

    def test_startup_uses_nano_and_uploads_bundled_csv(self) -> None:
        files = MagicMock()
        files.files.create.return_value.id = "file-demo"
        uploaded_bytes: list[bytes] = []

        def capture_upload(*, file, purpose):
            self.assertEqual(purpose, "assistants")
            uploaded_bytes.append(file.read())
            return Mock(id="file-demo")

        files.files.create.side_effect = capture_upload
        project = MagicMock()
        project.get_openai_client.return_value.__enter__.return_value = files
        credential = MagicMock()
        credential.__enter__.return_value = Mock(spec=TokenCredential)
        with (
            patch.dict(os.environ, {
                "FOUNDRY_PROJECT_ENDPOINT":
                    "https://example.services.ai.azure.com/api/projects/test",
                "HOST": "127.0.0.1",
                "PORT": "8088",
                "OTEL_SDK_DISABLED": "true",
            }, clear=True),
            patch.object(main, "load_dotenv"),
            patch.object(main, "DefaultAzureCredential", return_value=credential),
            patch.object(main, "AIProjectClient") as project_factory,
            patch.object(main, "FoundryChatClient", wraps=FoundryChatClient) as client_factory,
            patch.object(main, "ResponsesHostServer") as server,
        ):
            project_factory.return_value.__enter__.return_value = project
            main.main()
            self.assertEqual(client_factory.call_args.kwargs["model"], "gpt-5.4-nano")
            self.assertTrue(
                client_factory.call_args.kwargs["function_invocation_configuration"][
                    "include_detailed_errors"
                ]
            )
            server.return_value.run.assert_called_once_with(host="127.0.0.1", port=8088)
        self.assertEqual(
            uploaded_bytes, [(main.ROOT / "sample-data" / "regional-sales.csv").read_bytes()]
        )


class ProtocolTests(unittest.TestCase):
    def test_readiness_and_responses_protocol_without_azure(self) -> None:
        agent = main.build_agent(offline_client(), Mock(), "file-demo")

        async def offline_updates():
            yield AgentResponseUpdate(
                contents=[Content.from_text("Offline protocol smoke test.")],
                role="assistant",
                message_id="msg-offline",
                response_id="resp-offline",
            )

        with patch.object(agent, "run", return_value=offline_updates()):
            with TestClient(ResponsesHostServer(agent)) as http:
                self.assertEqual(http.get("/readiness").status_code, 200)
                response = http.post("/responses", json={
                    "input": "Validate the demo and chart monthly revenue.",
                    "stream": False,
                    "store": False,
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("Offline protocol smoke test.", response.text)
                self.assertEqual(response.json()["status"], "completed")

    def test_streaming_responses_for_agent_inspector(self) -> None:
        agent = main.build_agent(offline_client(), Mock(), "file-demo")

        async def offline_updates():
            yield AgentResponseUpdate(
                contents=[Content.from_text("Streaming smoke test.")],
                role="assistant",
                message_id="msg-stream",
                response_id="resp-stream",
            )

        with patch.object(agent, "run", return_value=offline_updates()):
            with TestClient(ResponsesHostServer(agent)) as http:
                response = http.post("/responses", json={
                    "input": "Analyze monthly revenue.",
                    "stream": True,
                    "store": False,
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("text/event-stream", response.headers["content-type"])
                self.assertIn("response.output_text.delta", response.text)
                self.assertIn("Streaming smoke test.", response.text)
                self.assertIn("response.completed", response.text)


if __name__ == "__main__":
    unittest.main()
