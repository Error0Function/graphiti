"""
Custom LLM Clients for DeepSeek and SiliconFlow.
These are defined here because they are not yet available in the core graphiti-core package.
"""

import json
import logging
import typing
from typing import Any, ClassVar

import openai
from graphiti_core.llm_client.client import LLMClient, get_extraction_language_instruction
from graphiti_core.llm_client.config import DEFAULT_MAX_TOKENS, LLMConfig, ModelSize
from graphiti_core.llm_client.errors import RateLimitError, RefusalError
from graphiti_core.prompts.models import Message
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam
from pydantic import BaseModel

logger = logging.getLogger(__name__)

DEFAULT_MODEL = 'deepseek-chat'

class DeepSeekClient(LLMClient):
    """
    DeepSeekClient is a client class for interacting with DeepSeek's language models.
    It handles specific requirements for deepseek-reasoner (thinking mode) and standard chat models.
    Crucially, it uses Tool Calls for structured output instead of 'json_schema' response_format,
    ensuring compatibility with DeepSeek API.
    """

    MAX_RETRIES: ClassVar[int] = 2

    def __init__(
        self,
        config: LLMConfig | None = None,
        cache: bool = False,
        client: typing.Any = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        if cache:
            raise NotImplementedError('Caching is not implemented for DeepSeek')

        if config is None:
            config = LLMConfig()

        super().__init__(config, cache)
        self.max_tokens = max_tokens

        if client is None:
            base_url = config.base_url or 'https://api.deepseek.com'
            self.client = AsyncOpenAI(api_key=config.api_key, base_url=base_url)
        else:
            self.client = client

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
        """Create a tool definition from a Pydantic model."""
        schema = response_model.model_json_schema()
        return {
            'type': 'function',
            'function': {
                'name': response_model.__name__,
                'description': schema.get('description', 'Extract information'),
                'parameters': schema,
            },
        }

    async def _generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        model_size: ModelSize = ModelSize.medium,
    ) -> dict[str, typing.Any]:
        openai_messages = self._convert_messages_to_openai_format(messages)
        model = self.model or DEFAULT_MODEL

        # Check if we are using the reasoning model
        is_reasoning_model = 'reasoner' in model

        request_kwargs: dict[str, Any] = {
            'model': model,
            'messages': openai_messages,
            'max_tokens': max_tokens,
        }

        # Add temperature only if NOT reasoning model
        if not is_reasoning_model:
            request_kwargs['temperature'] = self.temperature

        # Handle structured output
        if response_model:
            # Use tools for structured output extraction (DeepSeek supports this)
            tool = self._create_tool_definition(response_model)
            request_kwargs['tools'] = [tool]
            request_kwargs['tool_choice'] = 'auto'
        else:
            # Default to json_object if no response model is provided
            request_kwargs['response_format'] = {'type': 'json_object'}

        try:
            response = await self.client.chat.completions.create(**request_kwargs)
            
            result_message = response.choices[0].message
            
            # Check for tool calls first (priority for structured output)
            if result_message.tool_calls:
                tool_call = result_message.tool_calls[0]
                return json.loads(tool_call.function.arguments)
            
            # Fallback to content parsing
            content = result_message.content or '{}'
            
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                # Try to find JSON in text
                start = content.find('{')
                end = content.rfind('}') + 1
                if start >= 0 and end > start:
                    return json.loads(content[start:end])
                raise ValueError(f'Could not parse JSON from response: {content}') from e

        except openai.RateLimitError as e:
            raise RateLimitError from e
        except Exception as e:
            logger.error(f'Error in generating DeepSeek response: {e}')
            raise

    async def generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int | None = None,
        model_size: ModelSize = ModelSize.medium,
        group_id: str | None = None,
        prompt_name: str | None = None,
    ) -> dict[str, typing.Any]:
        if max_tokens is None:
            max_tokens = self.max_tokens

        # Add multilingual extraction instructions
        if messages:
            messages[0].content += get_extraction_language_instruction(group_id)
        
        # Wrap entire operation in tracing span
        with self.tracer.start_span('llm.generate') as span:
            attributes = {
                'llm.provider': 'deepseek',
                'model.size': model_size.value,
                'max_tokens': max_tokens,
            }
            if prompt_name:
                attributes['prompt.name'] = prompt_name
            span.add_attributes(attributes)

            retry_count = 0
            last_error = None

            while retry_count <= self.MAX_RETRIES:
                try:
                    response = await self._generate_response(
                        messages, response_model, max_tokens=max_tokens, model_size=model_size
                    )
                    return response
                except (RateLimitError, RefusalError):
                    span.set_status('error', str(last_error))
                    raise
                except (
                    openai.APITimeoutError,
                    openai.APIConnectionError,
                    openai.InternalServerError,
                ):
                    if retry_count >= self.MAX_RETRIES:
                        span.set_status('error', str(last_error))
                        raise
                except Exception as e:
                    last_error = e
                    if retry_count >= self.MAX_RETRIES:
                        logger.error(f'Max retries ({self.MAX_RETRIES}) exceeded. Last error: {e}')
                        span.set_status('error', str(e))
                        span.record_exception(e)
                        raise
                
                retry_count += 1


class SiliconFlowClient(DeepSeekClient):
    """
    SiliconFlowClient uses the same logic as DeepSeekClient since SiliconFlow
    provides an OpenAI-compatible API that supports Tool Calls but may not support
    strict 'json_schema' response_format yet.
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
            
        super().__init__(config, cache, client, max_tokens)
