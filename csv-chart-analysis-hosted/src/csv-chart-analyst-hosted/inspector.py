from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from copy import copy

from agent_framework import AgentContext, AgentMiddleware, Content
from agent_framework_foundry_hosting import ResponsesHostServer
from agent_framework_foundry_hosting._responses import _OutputItemTracker
from agent_framework_foundry_hosting._state_store import FunctionApprovalStore
from azure.ai.agentserver.responses import ResponseContext
from azure.ai.agentserver.responses._id_generator import IdGenerator
from azure.ai.agentserver.responses.aio import ResponseEventStream
from azure.ai.agentserver.responses.models import CreateResponse, OutputItem, ResponseStreamEvent
from azure.ai.agentserver.responses.streaming._builders import OutputItemFunctionCallBuilder
from openai.types.responses import ResponseCodeInterpreterToolCall

logger = logging.getLogger(__name__)
DISPLAY_TOOL_NAME = "code_interpreter"
DISPLAY_CALL_IDS_KEY = "caldova_interpreter_display_calls"


class InspectorHistoryFilter(AgentMiddleware):
    async def process(
        self, context: AgentContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        # The host can replay its displayed output on later turns. These cards are not model tools.
        display_ids = set(
            context.session.state.get(DISPLAY_CALL_IDS_KEY, []) if context.session else []
        )
        display_ids.update(
            content.call_id
            for message in context.messages
            for content in message.contents
            if content.type == "function_call" and content.name == DISPLAY_TOOL_NAME
        )
        if not display_ids:
            await call_next()
            return
        messages = []
        for message in context.messages:
            contents = [
                content for content in message.contents
                if not (
                    content.type in {"function_call", "function_result"}
                    and content.call_id in display_ids
                )
            ]
            if len(contents) == len(message.contents):
                messages.append(message)
            elif contents:
                filtered = copy(message)
                filtered.contents = contents
                messages.append(filtered)
        context.messages = messages
        await call_next()


class _InspectorOutputTracker(_OutputItemTracker):
    def __init__(self, stream: ResponseEventStream, delegate: _OutputItemTracker) -> None:
        super().__init__(stream)
        self._delegate = delegate
        self._calls: dict[str, OutputItemFunctionCallBuilder] = {}
        self._finished: set[str] = set()

    async def handle(
        self,
        content: Content,
        message_id: str | None = None,
        *,
        approval_storage: FunctionApprovalStore | None = None,
    ) -> AsyncGenerator[ResponseStreamEvent]:
        if content.type not in {"code_interpreter_tool_call", "code_interpreter_tool_result"}:
            async for event in self._delegate.handle(
                content, message_id=message_id, approval_storage=approval_storage
            ):
                yield event
            return

        raw = content.raw_representation
        call_id = raw.id if isinstance(raw, ResponseCodeInterpreterToolCall) else content.call_id
        if not call_id:
            logger.error("Cannot display Code Interpreter activity without its actual call ID.")
            raise ValueError("Code Interpreter activity is missing its call ID.")
        if call_id in self._finished:
            return

        if call_id not in self._calls:
            for event in self._delegate.close():
                yield event
            self._calls[call_id] = self._stream.add_output_item_function_call(
                name=DISPLAY_TOOL_NAME, call_id=call_id
            )
            yield self._calls[call_id].emit_added()

        if not isinstance(raw, ResponseCodeInterpreterToolCall) or raw.status in {"in_progress", "interpreting"}:
            return
        if raw.status not in {"completed", "failed", "incomplete"}:
            logger.error("Unsupported Code Interpreter status: %s", raw.status)
            raise ValueError(f"Unsupported Code Interpreter status: {raw.status}")

        arguments = json.dumps({"code": raw.code, "container_id": raw.container_id})
        for event in self._finish(call_id, arguments, raw.model_dump_json(), raw.status == "completed"):
            yield event

    def _finish(
        self, call_id: str, arguments: str, output: str, succeeded: bool
    ) -> list[ResponseStreamEvent]:
        call = self._calls[call_id]
        result: OutputItem = {
            "type": "function_call_output",
            "id": IdGenerator.new_function_call_output_item_id(self._stream.response["id"]),
            "call_id": call_id,
            "output": output,
            "status": "completed" if succeeded else "incomplete",
        }
        builder = self._stream.add_output_item(result["id"])
        self._finished.add(call_id)
        return [
            call.emit_arguments_delta(arguments),
            call.emit_arguments_done(arguments),
            call.emit_done(),
            builder.emit_added({**result, "status": "in_progress"}),
            builder.emit_done(result),
        ]

    def finish_pending(self) -> list[ResponseStreamEvent]:
        events: list[ResponseStreamEvent] = []
        for call_id in self._calls.keys() - self._finished:
            logger.warning("No terminal Code Interpreter event was observed for %s.", call_id)
            events.extend(self._finish(
                call_id, "{}",
                json.dumps({
                    "id": call_id,
                    "status": "incomplete",
                    "error": "No terminal Code Interpreter event was observed.",
                }),
                False,
            ))
        return events


class InspectorResponsesHostServer(ResponsesHostServer):
    async def _handle_inner_agent(
        self,
        request: CreateResponse,
        context: ResponseContext,
        response_event_stream: ResponseEventStream,
        tracker: _OutputItemTracker,
        cancellation_signal: asyncio.Event,
    ) -> AsyncGenerator[ResponseStreamEvent]:
        # Keep the SDK's session, cancellation, and history handling; adapt only its output tracker.
        display = _InspectorOutputTracker(response_event_stream, tracker)
        inner = super()._handle_inner_agent(
            request, context, response_event_stream, display, cancellation_signal
        )
        try:
            async for event in inner:
                yield event
        finally:
            await inner.aclose()
        for event in display.finish_pending():
            yield event
