from __future__ import annotations

import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

os.environ["OTEL_SDK_DISABLED"] = "true"

from agent_framework import (
    AgentContext,
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
from azure.core.credentials import TokenCredential
import httpx
from openai import NotFoundError
from openai.types.responses import Response, ResponseCodeInterpreterToolCall, ResponseOutputItemDoneEvent
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
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output_dir = Path(temporary.name) / "outputs"
        output_patch = patch.object(main, "OUTPUT_DIR", self.output_dir)
        output_patch.start()
        self.addCleanup(output_patch.stop)
        self.files = Mock()
        self.files.containers.files.content.retrieve.return_value.read.return_value = b"PNG"
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
        self.assertTrue(self.agent.default_options["store"])

    async def test_publish_output_returns_actual_foundry_file_id(self) -> None:
        self.files.containers.files.content.retrieve.return_value.read.return_value = b"PNG"
        self.files.files.create.return_value.id = "file-output"
        result = await self.invoke_publish("revenue.png")
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0].text,
            f"File: revenue.png\nDownload ID: file-output\nSaved to: {self.output_dir / 'revenue.png'}",
        )
        self.assertEqual((self.output_dir / "revenue.png").read_bytes(), b"PNG")
        self.files.containers.files.content.retrieve.assert_called_once_with(
            "cfile-generated", container_id="cntr-test"
        )
        self.files.files.create.assert_called_once_with(
            file=("revenue.png", b"PNG"), purpose="assistants"
        )

    async def test_publisher_saves_only_the_generated_basename(self) -> None:
        for filename in ("../../revenue.png", r"..\..\revenue.png"):
            with self.subTest(filename=filename):
                await self.invoke_publish(filename)
                self.assertEqual((self.output_dir / "revenue.png").read_bytes(), b"PNG")
                self.assertFalse((self.output_dir.parent / "revenue.png").exists())

    async def test_local_save_failure_reports_the_published_file_id(self) -> None:
        self.files.files.create.return_value.id = "file-output"
        with patch.object(Path, "write_bytes", side_effect=PermissionError("read-only")):
            with self.assertRaisesRegex(ValueError, "Foundry file file-output.*could not save"):
                await self.invoke_publish("revenue.png")

    async def test_publish_continues_response_with_interpreter_evidence(self) -> None:
        self.files.containers.files.content.retrieve.return_value.read.return_value = b"PNG"
        self.files.files.create.return_value.id = "file-output"
        provider_responses = [
            Response.model_validate({
                "id": "resp-calculated", "object": "response", "created_at": 0,
                "status": "completed", "model": main.MODEL_NAME,
                "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
                "output": [
                    {
                        "id": "ci-calculated", "type": "code_interpreter_call",
                        "status": "completed", "container_id": "cntr-calculated",
                        "code": "print('Total revenue: 123')",
                        "outputs": [{"type": "logs", "logs": "Total revenue: 123"}],
                    },
                    {
                        "id": "fc-publish", "type": "function_call",
                        "call_id": "call-publish", "name": "publish_output",
                        "arguments": '{"filename": "revenue.png"}',
                        "status": "completed",
                    },
                ],
            }),
            Response.model_validate({
                "id": "resp-final", "object": "response", "created_at": 0,
                "status": "completed", "model": main.MODEL_NAME,
                "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
                "output": [{
                    "id": "msg-final", "type": "message", "role": "assistant",
                    "status": "completed", "content": [{
                        "type": "output_text", "text": "Total revenue: 123. Chart published.",
                        "annotations": [],
                    }],
                }],
            }),
        ]
        raw_responses = [
            Mock(headers={}, parse=Mock(return_value=response))
            for response in provider_responses
        ]
        create = AsyncMock(side_effect=raw_responses)
        with patch.object(
            self.agent.client.client.responses.with_raw_response, "create", create
        ):
            result = await self.agent.run(
                "Calculate revenue and publish the chart", session=AgentSession()
            )

        self.assertIn("Total revenue: 123", result.text)
        self.assertEqual(create.await_count, 2)
        followup = create.await_args_list[1].kwargs
        self.assertEqual(followup.get("previous_response_id"), "resp-calculated")
        self.assertTrue(any(
            item.get("type") == "function_call_output"
            and item.get("call_id") == "call-publish"
            and "file-output" in item.get("output", "")
            for item in followup["input"]
        ))
        self.files.files.create.assert_called_once()

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

    async def check_model_loop(self, stream: bool, hosted: bool = False) -> None:
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
            [Content.from_text("The chart uses the generated output.")],
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

        with patch.object(
            self.agent.client, "_inner_get_response", side_effect=raw_response
        ) as provider:
            if hosted:
                with TestClient(main.InspectorResponsesHostServer(self.agent)) as http:
                    first = http.post("/responses", json={
                        "input": "Generate and publish revenue.png",
                        "stream": True,
                        "store": True,
                    })
                    self.assertEqual(first.status_code, 200, first.text)
                    events = [
                        json.loads(line[6:]) for line in first.text.splitlines()
                        if line.startswith("data: ") and line[6:] != "[DONE]"
                    ]
                    self.assertEqual(events[-1]["type"], "response.completed", first.text)
                    items = [
                        event["item"] for event in events
                        if event["type"] == "response.output_item.done"
                    ]
                    calls = [item for item in items if item["type"] == "function_call"]
                    self.assertEqual(
                        [item["name"] for item in calls], ["code_interpreter", "publish_output"]
                    )
                    self.assertEqual(calls[0]["call_id"], execution.id)
                    self.assertEqual(json.loads(calls[0]["arguments"]), {
                        "code": execution.code, "container_id": execution.container_id
                    })
                    result = next(
                        item for item in items
                        if item["type"] == "function_call_output"
                        and item["call_id"] == execution.id
                    )
                    self.assertEqual(json.loads(result["output"]), execution.model_dump())
                    self.assertEqual(result["status"], "completed")
                    second = http.post("/responses", json={
                        "input": "Explain the chart.",
                        "previous_response_id": events[-1]["response"]["id"],
                        "stream": False,
                        "store": False,
                    })
                    self.assertEqual(second.status_code, 200, second.text)
                    self.assertEqual(second.json()["status"], "completed", second.text)
                self.assertEqual(provider.call_count, 3)
            elif stream:
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
            for call in provider.call_args_list:
                for message in call.kwargs["messages"]:
                    for content in message.contents:
                        self.assertFalse(
                            content.type == "function_call" and content.name == "code_interpreter"
                        )
                        self.assertFalse(
                            content.type == "function_result" and content.call_id == execution.id
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

    async def test_hosted_cards_preserve_order_and_do_not_replay_into_model(self) -> None:
        await self.check_model_loop(stream=True, hosted=True)

    async def test_display_result_is_filtered_even_when_call_was_trimmed_from_history(self) -> None:
        self.session.state[main.DISPLAY_CALL_IDS_KEY] = ["ci-trimmed"]
        original = Message(role="tool", contents=[
            Content.from_function_result(call_id="ci-trimmed", result="display-only output"),
            Content.from_function_result(call_id="call-publish", result="File: revenue.png"),
        ])
        user = Message(role="user", contents=[Content.from_text("Explain the result.")])
        context = AgentContext(
            agent=self.agent, messages=[original, user], session=self.session
        )
        call_next = AsyncMock()
        await main.InspectorHistoryFilter().process(context, call_next)
        call_next.assert_awaited_once()
        self.assertEqual(len(original.contents), 2)
        self.assertEqual(
            [content.call_id for content in context.messages[0].contents], ["call-publish"]
        )
        self.assertIs(context.messages[1], user)

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
            patch.object(main, "InspectorResponsesHostServer") as server,
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
            with TestClient(main.InspectorResponsesHostServer(agent)) as http:
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
            with TestClient(main.InspectorResponsesHostServer(agent)) as http:
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

    def test_interpreter_chunks_and_duplicate_results_produce_one_card(self) -> None:
        events = self.interpreter_events("completed")
        calls = [
            event["item"] for event in events
            if event["type"] == "response.output_item.added"
            and event["item"]["type"] == "function_call"
        ]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["call_id"], "ci-display")
        results = [
            event["item"] for event in events
            if event["type"] == "response.output_item.done"
            and event["item"]["type"] == "function_call_output"
        ]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "completed")
        self.assertEqual(
            json.loads(results[0]["output"])["outputs"],
            [{"type": "logs", "logs": "Caldova\n"}],
        )
        arguments = next(
            event["arguments"] for event in events
            if event["type"] == "response.function_call_arguments.done"
        )
        self.assertEqual(json.loads(arguments)["code"], 'print("Caldova")\n')
        self.assertEqual(events[-1]["type"], "response.completed")
        self.assertEqual(
            [event["sequence_number"] for event in events],
            sorted({event["sequence_number"] for event in events}),
        )

    def test_unsuccessful_interpreter_calls_are_error_results(self) -> None:
        for status in ("failed", "incomplete", None):
            with self.subTest(status=status):
                events = self.interpreter_events(status)
                results = [
                    event["item"] for event in events
                    if event["type"] == "response.output_item.done"
                    and event["item"]["type"] == "function_call_output"
                ]
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]["status"], "incomplete")
                self.assertEqual(
                    json.loads(results[0]["output"])["status"], status or "incomplete"
                )
                final = events[-1]["response"]["output"]
                self.assertEqual(
                    next(item for item in final if item["type"] == "function_call_output")["status"],
                    "incomplete",
                )

    def interpreter_events(self, status: str | None) -> list[dict]:
        agent = main.build_agent(offline_client(), Mock(), "file-demo")
        execution = ResponseCodeInterpreterToolCall(
            id="ci-display", type="code_interpreter_call", status="in_progress",
            code=None, container_id="cntr-display", outputs=[],
        )

        async def offline_updates():
            yield AgentResponseUpdate(
                contents=[Content.from_code_interpreter_tool_result(
                    call_id=execution.id, raw_representation=execution
                )], role="assistant",
            )
            for fragment in ('print("', 'Caldova")\n'):
                yield AgentResponseUpdate(
                    contents=[Content.from_code_interpreter_tool_call(
                        call_id=execution.id, inputs=[Content.from_text(fragment)]
                    )], role="assistant",
                )
            if status is not None:
                completed = ResponseCodeInterpreterToolCall.model_validate({
                    **execution.model_dump(),
                    "status": status,
                    "code": 'print("Caldova")\n',
                    "outputs": [{"type": "logs", "logs": "Caldova\n"}],
                })
                for _ in range(2):
                    yield AgentResponseUpdate(
                        contents=[Content.from_code_interpreter_tool_result(
                            call_id=execution.id, raw_representation=completed
                        )], role="assistant",
                    )
            yield AgentResponseUpdate(
                contents=[Content.from_text("Analysis finished.")], role="assistant",
                message_id="msg-display", response_id="resp-display",
            )

        with patch.object(agent, "run", return_value=offline_updates()):
            with TestClient(main.InspectorResponsesHostServer(agent)) as http:
                response = http.post("/responses", json={
                    "input": "Analyze revenue.", "stream": True, "store": False,
                })
                self.assertEqual(response.status_code, 200, response.text)
        return [
            json.loads(line[6:]) for line in response.text.splitlines()
            if line.startswith("data: ") and line[6:] != "[DONE]"
        ]


if __name__ == "__main__":
    unittest.main()
