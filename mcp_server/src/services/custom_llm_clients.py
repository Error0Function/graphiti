"""
Custom LLM Clients for DeepSeek and SiliconFlow.
These are defined here because they are not yet available in the core graphiti-core package.
"""

import json
import logging
import typing
from typing import Any

import openai
from graphiti_core.llm_client.client import LLMClient
from graphiti_core.llm_client.config import DEFAULT_MAX_TOKENS, LLMConfig, ModelSize
from graphiti_core.llm_client.errors import RateLimitError
from graphiti_core.prompts.models import Message
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam
from pydantic import BaseModel

logger = logging.getLogger(__name__)



DEFAULT_MODEL = 'deepseek-chat'
MAX_RETRIES = 2


class DeepSeekClient(LLMClient):
    """
    DeepSeekClient is a client class for interacting with DeepSeek's language models.

    It uses Tool Calls with tool_choice='required' for structured output instead of
    'json_schema' response_format, ensuring compatibility with DeepSeek API.
    Supports the 'thinking' parameter for enabling chain-of-thought reasoning on
    deepseek-chat without switching model names.

    Note: DeepSeek's beta strict mode has a known bug that produces malformed JSON
    for tool call arguments. We use the regular endpoint without strict mode instead,
    which reliably produces valid JSON.
    """

    def __init__(
        self,
        config: LLMConfig | None = None,
        cache: bool = False,
        client: typing.Any = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        thinking_enabled: bool = False,
    ):
        if cache:
            raise NotImplementedError('Caching is not implemented for DeepSeek')

        if config is None:
            config = LLMConfig()

        super().__init__(config, cache)
        self.max_tokens = max_tokens
        self.thinking_enabled = thinking_enabled

        if client is None:
            base_url = config.base_url or 'https://api.deepseek.com'
            self.client = AsyncOpenAI(api_key=config.api_key, base_url=base_url)
        else:
            self.client = client

    @property
    def _is_thinking_mode(self) -> bool:
        """Determine if thinking mode is active via explicit flag or model name."""
        model = self.model or DEFAULT_MODEL
        return self.thinking_enabled or 'reasoner' in model

    def _convert_messages_to_openai_format(
        self, messages: list[Message]
    ) -> list[ChatCompletionMessageParam]:
        """Convert internal Message format to OpenAI ChatCompletionMessageParam format."""
        openai_messages: list[ChatCompletionMessageParam] = []
        for m in messages:
            m.content = self._clean_input(m.content)
            if m.role == 'user':
                openai_messages.append({'role': 'user', 'content': m.content})
            elif m.role == 'system':
                openai_messages.append({'role': 'system', 'content': m.content})
            elif m.role == 'assistant':
                openai_messages.append({'role': 'assistant', 'content': m.content})
        return openai_messages

    def _create_tool_definition(self, response_model: type[BaseModel]) -> ChatCompletionToolParam:
        """Create a tool definition from a Pydantic model for use with tool_choice='required'."""
        schema = response_model.model_json_schema()
        return {
            'type': 'function',
            'function': {
                'name': response_model.__name__,
                'description': schema.get('description', 'Extract information'),
                'parameters': schema,
            },
        }

    async def _call_api(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> dict[str, typing.Any]:
        """Make a single DeepSeek API call and return parsed result with token counts."""
        openai_messages = self._convert_messages_to_openai_format(messages)
        model = self.model or DEFAULT_MODEL
        is_thinking = self._is_thinking_mode
        is_reasoner = 'reasoner' in model

        # Clamp max_tokens to model-specific limits.
        # deepseek-chat: max 8192 (even with thinking enabled)
        # deepseek-reasoner: max 32768
        model_max = 32768 if is_reasoner else 8192
        if max_tokens > model_max:
            logger.info(f'Clamping max_tokens from {max_tokens} to {model_max} for {model}')
            max_tokens = model_max

        request_kwargs: dict[str, Any] = {
            'model': model,
            'messages': openai_messages,
            'max_tokens': max_tokens,
        }

        # The 'thinking' parameter controls chain-of-thought reasoning.
        # For deepseek-chat: enables thinking when set to 'enabled' via extra_body.
        # For deepseek-reasoner: thinking is enabled by default, no need to send it.
        if self.thinking_enabled and not is_reasoner:
            request_kwargs['extra_body'] = {'thinking': {'type': 'enabled'}}

        # Temperature, top_p, frequency_penalty, presence_penalty have no effect
        # in thinking mode and logprobs/top_logprobs would trigger errors.
        if not is_thinking:
            if self.temperature is not None:
                request_kwargs['temperature'] = self.temperature

        # Handle structured output.
        # In thinking mode: tool calls don't work reliably (empty responses).
        # Instead, rely on the JSON schema already appended to the prompt by the
        # parent's generate_response(), and parse from content.
        # In non-thinking mode: use tool calls with tool_choice='required'.
        # Note: we do NOT use the beta endpoint's strict mode because it has a
        # known bug that produces malformed JSON (missing closing quotes on keys).
        if response_model:
            if not is_thinking:
                tool = self._create_tool_definition(response_model)
                request_kwargs['tools'] = [tool]
                request_kwargs['tool_choice'] = 'required'
            # In thinking mode, no tools — structured output via content JSON parsing
        else:
            if not is_thinking:
                request_kwargs['response_format'] = {'type': 'json_object'}

        try:
            response = await self.client.chat.completions.create(**request_kwargs)

            result_message = response.choices[0].message

            # Extract token usage from response
            input_tokens = 0
            output_tokens = 0
            if response.usage:
                input_tokens = response.usage.prompt_tokens
                output_tokens = response.usage.completion_tokens

            # Check for tool calls first (priority for structured output)
            if result_message.tool_calls:
                tool_call = result_message.tool_calls[0]
                raw_args = tool_call.function.arguments
                try:
                    result = json.loads(raw_args)
                except json.JSONDecodeError as e:
                    logger.error(f'Malformed JSON from tool call: {e}. Raw: {raw_args[:500]}')
                    raise
                result['__input_tokens'] = input_tokens
                result['__output_tokens'] = output_tokens
                return result

            # Fallback to content parsing
            content = result_message.content or ''

            if not content.strip():
                if response_model:
                    raise ValueError(
                        f'Empty response from DeepSeek (no tool_calls and no content). '
                        f'Expected structured output for {response_model.__name__}.'
                    )
                return {'__input_tokens': input_tokens, '__output_tokens': output_tokens}

            try:
                result = json.loads(content)
            except json.JSONDecodeError as e:
                # Try to find JSON in text (thinking mode may wrap JSON in text)
                start = content.find('{')
                end = content.rfind('}') + 1
                if start >= 0 and end > start:
                    result = json.loads(content[start:end])
                else:
                    raise ValueError(f'Could not parse JSON from response: {content}') from e

            # Validate that structured output has actual data
            if response_model and not any(k for k in result if not k.startswith('__')):
                raise ValueError(
                    f'DeepSeek returned empty JSON for {response_model.__name__}. '
                    f'Content: {content[:200]}'
                )

            result['__input_tokens'] = input_tokens
            result['__output_tokens'] = output_tokens
            return result

        except openai.RateLimitError as e:
            raise RateLimitError from e
        except Exception as e:
            logger.error(f'Error in generating DeepSeek response: {e}')
            raise

    async def _generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        model_size: ModelSize = ModelSize.medium,
    ) -> dict[str, typing.Any]:
        """Generate response with retry logic and error context appending.

        This overrides the abstract _generate_response from LLMClient.
        The parent's generate_response() handles: multilingual instructions, input cleaning,
        tracing spans, caching, and calling this via _generate_response_with_retry.
        We implement our own retry loop here (with error context appending) so that
        tenacity's retry in the parent acts as an additional safety net.
        """
        retry_count = 0
        last_error: Exception | None = None

        while retry_count <= MAX_RETRIES:
            try:
                response = await self._call_api(
                    messages, response_model, max_tokens=max_tokens
                )

                # Strip internal token count keys before returning
                response.pop('__input_tokens', None)
                response.pop('__output_tokens', None)

                return response
            except RateLimitError:
                raise
            except (
                openai.APITimeoutError,
                openai.APIConnectionError,
                openai.InternalServerError,
            ):
                # Let these bubble up to tenacity in the parent
                raise
            except Exception as e:
                last_error = e
                if retry_count >= MAX_RETRIES:
                    logger.error(f'Max retries ({MAX_RETRIES}) exceeded. Last error: {e}')
                    raise

                # Append error context so the model can self-correct on retry
                error_context = (
                    f'The previous response attempt was invalid. '
                    f'Error type: {e.__class__.__name__}. '
                    f'Error details: {str(e)}. '
                    f'Please try again with a valid response, ensuring the output matches '
                    f'the expected format and constraints.'
                )
                messages.append(Message(role='user', content=error_context))
                logger.warning(
                    f'Retrying after application error '
                    f'(attempt {retry_count + 1}/{MAX_RETRIES}): {e}'
                )

            retry_count += 1

        raise last_error or Exception('Max retries exceeded with no specific error')


class SiliconFlowClient(DeepSeekClient):
    """
    SiliconFlowClient reuses DeepSeekClient logic since SiliconFlow provides an
    OpenAI-compatible API with Tool Calls support. If SiliconFlow diverges from
    DeepSeek behavior in the future (e.g. supporting json_schema response_format),
    this class should be updated to override _generate_response accordingly.
    """

    def __init__(
        self,
        config: LLMConfig | None = None,
        cache: bool = False,
        client: typing.Any = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        if config is None:
            config = LLMConfig()

        # Ensure base_url is set for SiliconFlow
        if not config.base_url:
            config.base_url = 'https://api.siliconflow.cn/v1'

        # SiliconFlow does not support thinking mode
        super().__init__(config, cache, client, max_tokens, thinking_enabled=False)
