"""Application services for the Claude-compatible API."""

from __future__ import annotations

import asyncio
import traceback
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger

from config.settings import Settings
from core.anthropic import get_token_count, get_user_facing_error_message
from core.anthropic.sse import ANTHROPIC_SSE_RESPONSE_HEADERS
from core.quench import QuenchRegistry
from core.quench import get_registry as get_quench_registry
from providers.base import BaseProvider
from providers.exceptions import (
    APIError,
    AuthenticationError,
    InvalidRequestError,
    OverloadedError,
    ProviderError,
    RateLimitError,
)

from .model_router import ModelRouter
from .models.anthropic import MessagesRequest, TokenCountRequest
from .models.responses import TokenCountResponse
from .optimization_handlers import try_optimizations
from .web_tools.egress import WebFetchEgressPolicy
from .web_tools.request import (
    is_web_server_tool_request,
    openai_chat_upstream_server_tool_error,
)
from .web_tools.streaming import stream_web_server_tool_response

TokenCounter = Callable[[list[Any], str | list[Any] | None, list[Any] | None], int]

ProviderGetter = Callable[[str], BaseProvider]

# Providers that use ``/chat/completions`` + Anthropic-to-OpenAI conversion (not native Messages).
_OPENAI_CHAT_UPSTREAM_IDS = frozenset({"nvidia_nim", "deepseek"})

# HTTP status codes that signal a transient, per-model failure: try the next
# chain entry. 401/403 = auth/permission/quota, 429 = rate limit, 5xx = server,
# 529 = Anthropic-style overloaded. 400 (bad request) is excluded; the request
# itself is broken, so retrying with a different model would not help.
_CHAIN_RETRYABLE_API_STATUS = frozenset({401, 403, 429, 500, 502, 503, 504, 529})


def anthropic_sse_streaming_response(
    body: AsyncIterator[str],
) -> StreamingResponse:
    """Return a :class:`StreamingResponse` for Anthropic-style SSE streams."""
    return StreamingResponse(
        body,
        media_type="text/event-stream",
        headers=ANTHROPIC_SSE_RESPONSE_HEADERS,
    )


def _http_status_for_unexpected_service_exception(_exc: BaseException) -> int:
    """HTTP status for uncaught non-provider failures (stable client contract)."""
    return 500


def _log_unexpected_service_exception(
    settings: Settings,
    exc: BaseException,
    *,
    context: str,
    request_id: str | None = None,
) -> None:
    """Log service-layer failures without echoing exception text unless opted in."""
    if settings.log_api_error_tracebacks:
        if request_id is not None:
            logger.error("{} request_id={}: {}", context, request_id, exc)
        else:
            logger.error("{}: {}", context, exc)
        logger.error(traceback.format_exc())
        return
    if request_id is not None:
        logger.error(
            "{} request_id={} exc_type={}",
            context,
            request_id,
            type(exc).__name__,
        )
    else:
        logger.error("{} exc_type={}", context, type(exc).__name__)


def _require_non_empty_messages(messages: list[Any]) -> None:
    if not messages:
        raise InvalidRequestError("messages cannot be empty")


def _is_message_start_chunk(chunk: str) -> bool:
    """Return True if ``chunk`` is a leading ``message_start`` SSE event.

    The chain pump buffers these instead of forwarding them immediately,
    because providers emit ``message_start`` unconditionally before the
    upstream HTTP call (see ``providers/openai_compat.py`` and
    ``providers/anthropic_messages.py``). An early auth/rate-limit error
    therefore arrives *after* ``message_start`` has been yielded; without
    buffering, the chain pump would treat the response as committed and
    skip fallback. The check is a simple ``startswith`` because both line
    mode and event mode SSE chunks begin with the literal ``event:``
    prefix per the EventSource spec. A future provider that strips this
    prefix would silently bypass the buffer (acceptable degradation: the
    chain pump treats it as a normal response and the legacy graceful-SSE
    path still produces correct output).
    """
    return chunk.startswith("event: message_start")


def _ttl_for_chain_error(err: ProviderError) -> float:
    """How long to quench a model after a chain-retryable error.

    Auth and 401/403 APIErrors are treated as quota/credit exhaustion and
    held longer (1h); repeat attempts will not recover quickly. Rate-limit
    (429) is short (60s) since the upstream window resets fast. 5xx are
    treated as overload (30s).
    """
    if isinstance(err, RateLimitError):
        return QuenchRegistry.TTL_RATE_LIMIT
    if isinstance(err, AuthenticationError):
        return QuenchRegistry.TTL_AUTH
    if isinstance(err, OverloadedError):
        return QuenchRegistry.TTL_OVERLOADED
    if isinstance(err, APIError):
        status = getattr(err, "status_code", 0)
        if status in (401, 403):
            return QuenchRegistry.TTL_AUTH
        if status == 429:
            return QuenchRegistry.TTL_RATE_LIMIT
        if status in (500, 502, 503, 504, 529):
            return QuenchRegistry.TTL_OVERLOADED
    return QuenchRegistry.TTL_API_ERROR


def _is_chain_retryable(err: Exception) -> bool:
    """Decide whether to fall through to the next chain entry on this error.

    Auth (quota/credits exhausted), rate limit, and overload are all
    transient at the per-model level, so we try the next model. Generic
    :class:`APIError` is retryable when its status code is in the
    transient set (e.g. 403 ``PermissionDeniedError`` from OpenAI SDK does
    not subclass ``AuthenticationError`` but means the same thing for
    routing). Invalid-request errors stay surfaced so we don't loop on
    broken input.
    """
    if isinstance(err, (RateLimitError, AuthenticationError, OverloadedError)):
        return True
    if isinstance(err, APIError):
        return getattr(err, "status_code", 0) in _CHAIN_RETRYABLE_API_STATUS
    return False


class ClaudeProxyService:
    """Coordinate request optimization, model routing, token count, and providers."""

    def __init__(
        self,
        settings: Settings,
        provider_getter: ProviderGetter,
        model_router: ModelRouter | None = None,
        token_counter: TokenCounter = get_token_count,
    ):
        self._settings = settings
        self._provider_getter = provider_getter
        self._model_router = model_router or ModelRouter(settings)
        self._token_counter = token_counter

    async def create_message(self, request_data: MessagesRequest) -> object:
        """Create a message response or streaming response."""
        try:
            _require_non_empty_messages(request_data.messages)

            routed = self._model_router.resolve_messages_request(request_data)
            if routed.resolved.provider_id in _OPENAI_CHAT_UPSTREAM_IDS:
                tool_err = openai_chat_upstream_server_tool_error(
                    routed.request,
                    web_tools_enabled=self._settings.enable_web_server_tools,
                )
                if tool_err is not None:
                    raise InvalidRequestError(tool_err)

            if self._settings.enable_web_server_tools and is_web_server_tool_request(
                routed.request
            ):
                input_tokens = self._token_counter(
                    routed.request.messages, routed.request.system, routed.request.tools
                )
                logger.info("Optimization: Handling Anthropic web server tool")
                egress = WebFetchEgressPolicy(
                    allow_private_network_targets=self._settings.web_fetch_allow_private_networks,
                    allowed_schemes=self._settings.web_fetch_allowed_scheme_set(),
                )
                return anthropic_sse_streaming_response(
                    stream_web_server_tool_response(
                        routed.request,
                        input_tokens=input_tokens,
                        web_fetch_egress=egress,
                        verbose_client_errors=self._settings.log_api_error_tracebacks,
                    ),
                )

            optimized = try_optimizations(routed.request, self._settings)
            if optimized is not None:
                return optimized
            logger.debug("No optimization matched, routing to provider")

            chain = self._settings.resolve_model_chain(request_data.model)
            input_tokens = self._token_counter(
                routed.request.messages, routed.request.system, routed.request.tools
            )
            request_id = f"req_{uuid.uuid4().hex[:12]}"

            # Walk the chain pre-flight: the first model that returns a chunk
            # owns the stream. Errors before any chunk get mapped to HTTP
            # status codes, matching pre-chain behavior. The walker runs as
            # one task so all loguru contextvar bindings live in a single
            # async context.
            # Bounded queue gives the producer (chain pump) backpressure when
            # the consumer (HTTP client) is slow, capping memory growth per
            # request. 64 chunks is comfortably more than any single SSE
            # event burst yet small enough to be a hard ceiling under abuse.
            queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=64)
            ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            walker_task = asyncio.create_task(
                self._chain_pump(
                    request_data=request_data,
                    chain=chain,
                    input_tokens=input_tokens,
                    request_id=request_id,
                    queue=queue,
                    ready=ready,
                ),
                name=f"chain-pump:{request_id}",
            )
            try:
                await ready
            except BaseException:
                walker_task.cancel()
                raise
            return anthropic_sse_streaming_response(
                self._drain_queue(queue, walker_task),
            )

        except ProviderError:
            raise
        except HTTPException:
            raise
        except Exception as e:
            _log_unexpected_service_exception(
                self._settings, e, context="CREATE_MESSAGE_ERROR"
            )
            raise HTTPException(
                status_code=_http_status_for_unexpected_service_exception(e),
                detail=get_user_facing_error_message(e),
            ) from e

    async def _chain_pump(
        self,
        *,
        request_data: MessagesRequest,
        chain: list[str],
        input_tokens: int,
        request_id: str,
        queue: asyncio.Queue[str | None],
        ready: asyncio.Future[None],
    ) -> None:
        """Walk the model chain, draining the chosen stream into a queue.

        - Sets ``ready`` (success) once the first chunk is enqueued so the
          route handler can safely open the StreamingResponse.
        - Sets ``ready`` (exception) if the chain is exhausted before any
          chunk arrives so the route handler raises the right HTTP status.
        - Pushes each subsequent chunk onto the queue; terminates with the
          sentinel ``None``.

        Quench-worthy errors (auth/rate-limit/overload) cause the walker to
        skip ahead to the next chain entry, queuing a 60s/1h/30s cooldown on
        the failed model. Single-entry chains skip quench so legitimate
        client retries aren't refused.
        """
        registry = get_quench_registry()
        last_error: Exception | None = None
        attempted: list[str] = []
        first_seen = False

        try:
            for idx, model_ref in enumerate(chain, start=1):
                # ---- Phase 1: skip already-quenched entries ----
                if registry.is_quenched(model_ref):
                    logger.debug(
                        "CHAIN_SKIP: request_id={} model_ref={} quenched ({:.0f}s left)",
                        request_id,
                        model_ref,
                        registry.remaining(model_ref),
                    )
                    continue
                attempted.append(model_ref)

                # ---- Phase 2: build per-attempt request and resolve provider ----
                provider_id = Settings.parse_provider_type(model_ref)
                provider_model = Settings.parse_model_name(model_ref)
                attempt_request = request_data.model_copy(deep=True)
                attempt_request.model = provider_model
                thinking_enabled = self._settings.resolve_thinking(request_data.model)

                try:
                    provider = self._provider_getter(provider_id)
                except Exception as e:
                    # Avoid logging the raw exception text. Provider init
                    # errors can include API keys (e.g. a Pydantic validation
                    # error that echoes field values). Log only the type
                    # name; full detail goes through the gated
                    # _log_unexpected_service_exception helper.
                    logger.warning(
                        "CHAIN_PROVIDER_INIT_FAIL: request_id={} model_ref={} exc_type={}",
                        request_id,
                        model_ref,
                        type(e).__name__,
                    )
                    if self._settings.log_api_error_tracebacks:
                        _log_unexpected_service_exception(
                            self._settings,
                            e,
                            context="CHAIN_PROVIDER_INIT_FAIL",
                            request_id=request_id,
                        )
                    last_error = e
                    continue

                # ---- Phase 3: preflight (request validation, no upstream call) ----
                try:
                    provider.preflight_stream(
                        attempt_request,
                        thinking_enabled=thinking_enabled,
                    )
                except ProviderError as e:
                    last_error = e
                    if _is_chain_retryable(e) and len(chain) > 1:
                        registry.quench(
                            model_ref,
                            _ttl_for_chain_error(e),
                            reason=type(e).__name__,
                        )
                        # ``e.message`` from ProviderError is conventionally a
                        # static, sanitized string set by the provider; we
                        # still log only the type name by default and gate
                        # the message text behind log_api_error_tracebacks
                        # to keep the contract robust against future provider
                        # changes that might smuggle upstream-derived text.
                        logger.warning(
                            "CHAIN_PREFLIGHT_FALLBACK: request_id={} model_ref={} exc_type={}",
                            request_id,
                            model_ref,
                            type(e).__name__,
                        )
                        if self._settings.log_api_error_tracebacks:
                            logger.warning(
                                "CHAIN_PREFLIGHT_FALLBACK_DETAIL: request_id={} message={}",
                                request_id,
                                e.message,
                            )
                        continue
                    if not ready.done():
                        ready.set_exception(e)
                    return

                # ---- Phase 4: open the upstream stream ----
                logger.info(
                    "API_REQUEST: request_id={} model={} chain_pos={}/{} messages={}",
                    request_id,
                    model_ref,
                    idx,
                    len(chain),
                    len(attempt_request.messages),
                )
                if self._settings.log_raw_api_payloads:
                    logger.debug(
                        "FULL_PAYLOAD [{}]: {}",
                        request_id,
                        attempt_request.model_dump(),
                    )

                # The last *live* entry keeps the default (graceful in-stream
                # SSE error) so the client always sees a clean stream end if
                # everything fails. Earlier entries opt into raising so we
                # can fall through transparently. We check remaining entries
                # for live-ness against the quench registry. A chain like
                # [quenched, A, quenched] treats A as the last live entry,
                # not a middle one.
                has_live_successor = any(
                    not registry.is_quenched(m) for m in chain[idx:]
                )
                stream = provider.stream_response(
                    attempt_request,
                    input_tokens=input_tokens,
                    request_id=request_id,
                    thinking_enabled=thinking_enabled,
                    raise_on_upstream_error=has_live_successor,
                )
                # ---- Phase 5: drain the stream with message_start buffering ----
                try:
                    # Buffer the leading ``message_start`` event. Providers
                    # emit it unconditionally before the upstream HTTP call,
                    # so an early 401/429 still arrives AFTER message_start
                    # has been yielded. Defer committing ``first_seen`` until
                    # we see a chunk that proves the upstream is actually
                    # responding. That way a chain-retryable error can
                    # still trigger fallback.
                    pending: list[str] = []
                    async for chunk in stream:
                        if not first_seen:
                            if _is_message_start_chunk(chunk):
                                pending.append(chunk)
                                continue
                            first_seen = True
                            for buffered in pending:
                                await queue.put(buffered)
                            pending.clear()
                            if not ready.done():
                                ready.set_result(None)
                        await queue.put(chunk)
                    if pending and not first_seen:
                        # Stream ended with only message_start (rare; treat
                        # as empty response and fall through).
                        logger.warning(
                            "CHAIN_EMPTY_AFTER_MESSAGE_START: request_id={} model_ref={}",
                            request_id,
                            model_ref,
                        )
                        continue
                    await queue.put(None)
                    return
                except ProviderError as e:
                    last_error = e
                    if first_seen:
                        # Already streaming to client; cannot retry. End the
                        # response cleanly so the client gets the partial
                        # SSE rather than a hang.
                        logger.error(
                            "CHAIN_MIDSTREAM_ERROR: request_id={} model_ref={} exc_type={}",
                            request_id,
                            model_ref,
                            type(e).__name__,
                        )
                        if self._settings.log_api_error_tracebacks:
                            logger.error(
                                "CHAIN_MIDSTREAM_ERROR_DETAIL: request_id={} message={}",
                                request_id,
                                e.message,
                            )
                        await queue.put(None)
                        return
                    if _is_chain_retryable(e) and len(chain) > 1:
                        registry.quench(
                            model_ref,
                            _ttl_for_chain_error(e),
                            reason=type(e).__name__,
                        )
                        logger.warning(
                            "CHAIN_FALLBACK: request_id={} model_ref={} exc_type={}",
                            request_id,
                            model_ref,
                            type(e).__name__,
                        )
                        if self._settings.log_api_error_tracebacks:
                            logger.warning(
                                "CHAIN_FALLBACK_DETAIL: request_id={} message={}",
                                request_id,
                                e.message,
                            )
                        continue
                    if not ready.done():
                        ready.set_exception(e)
                    return
                except Exception as e:
                    last_error = e
                    if not first_seen:
                        if not ready.done():
                            ready.set_exception(e)
                        return
                    _log_unexpected_service_exception(
                        self._settings,
                        e,
                        context="CHAIN_MIDSTREAM_UNEXPECTED",
                        request_id=request_id,
                    )
                    await queue.put(None)
                    return

            logger.error(
                "CHAIN_EXHAUSTED: request_id={} attempted={} last_err={}",
                request_id,
                attempted,
                type(last_error).__name__ if last_error else None,
            )
            if not ready.done():
                if isinstance(last_error, ProviderError):
                    ready.set_exception(last_error)
                elif last_error is not None:
                    ready.set_exception(
                        HTTPException(
                            status_code=503,
                            detail=(
                                f"All {len(chain)} chain entries failed. "
                                f"Last error: {get_user_facing_error_message(last_error)}"
                            ),
                        )
                    )
                else:
                    ready.set_exception(
                        HTTPException(
                            status_code=503,
                            detail="All chain entries are quenched. Try again shortly.",
                        )
                    )
        except BaseException as e:
            if not ready.done():
                ready.set_exception(e)
            await queue.put(None)
            raise

    async def _drain_queue(
        self,
        queue: asyncio.Queue[str | None],
        walker_task: asyncio.Task[None],
    ) -> AsyncGenerator[str]:
        """Yield queued chunks until the chain pump signals end."""
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item
        finally:
            if not walker_task.done():
                walker_task.cancel()

    def count_tokens(self, request_data: TokenCountRequest) -> TokenCountResponse:
        """Count tokens for a request after applying configured model routing."""
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        with logger.contextualize(request_id=request_id):
            try:
                _require_non_empty_messages(request_data.messages)
                routed = self._model_router.resolve_token_count_request(request_data)
                tokens = self._token_counter(
                    routed.request.messages, routed.request.system, routed.request.tools
                )
                logger.info(
                    "COUNT_TOKENS: request_id={} model={} messages={} input_tokens={}",
                    request_id,
                    routed.request.model,
                    len(routed.request.messages),
                    tokens,
                )
                return TokenCountResponse(input_tokens=tokens)
            except ProviderError:
                raise
            except Exception as e:
                _log_unexpected_service_exception(
                    self._settings,
                    e,
                    context="COUNT_TOKENS_ERROR",
                    request_id=request_id,
                )
                raise HTTPException(
                    status_code=_http_status_for_unexpected_service_exception(e),
                    detail=get_user_facing_error_message(e),
                ) from e
