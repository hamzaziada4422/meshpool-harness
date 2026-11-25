"""A 400 is only "quota used up" when the body says money or an explicit budget; real-world error bodies."""
import pytest

from meshpool_harness.core.client import classify_error as c

CASES = [
    # OpenAI context overflow: shrink and retry
    ("meshpool HTTP 400: {'error': {'message': \"This model's maximum context length is 128000 tokens. However, your "
     "messages resulted in 130000 tokens. Please reduce the length of the messages.\", 'type': 'invalid_request_error', "
     "'code': 'context_length_exceeded'}}", "context"),
    # max_tokens too large: a plain bad request, shown as the server said it
    ("meshpool HTTP 400: {'error': {'message': 'max_tokens is too large: 200000. This model supports at most 16384 "
     "completion tokens, whereas you provided 200000.', 'type': 'invalid_request_error', 'param': 'max_tokens'}}",
     "bad_request"),
    # Azure content filter
    ("HTTP 400: {'error': {'message': \"The response was filtered due to the prompt triggering Azure OpenAI's content "
     "management policy. Please modify your prompt and retry.\", 'param': 'prompt', 'code': 'content_filter', "
     "'status': 400}}", "bad_request"),
    # LiteLLM wrapping an upstream 400
    ("HTTP 400: {'error': {'message': 'litellm.BadRequestError: OpenAIException - Invalid value for messages[3].content. "
     "Received Model Group=gpt-5\\nAvailable Model Group Fallbacks=None', 'type': None, 'code': '400'}}", "bad_request"),
    # DeepSeek invalid_request_error
    ("HTTP 400: {'error': {'message': 'Invalid max_tokens value, the valid range of max_tokens is [1, 8192]', "
     "'type': 'invalid_request_error', 'code': 'invalid_request_error'}}", "bad_request"),
    # words that used to read as money
    ("HTTP 400: {'error': {'message': 'Your request exceeded the model output limit of 8192 tokens; thinking budget "
     "exceeded for this request'}}", "bad_request"),
    ("HTTP 400: `max_tokens` must be greater than `thinking.budget_tokens`", "bad_request"),
    ("HTTP 400: PDF has too many pages: 120 exceeds the maximum of 100 pages", "bad_request"),
    # genuine money problems
    ("HTTP 402: Insufficient Balance", "quota"),
    ("HTTP 429: You exceeded your current quota, please check your plan and billing details. insufficient_quota", "quota"),
    ("HTTP 400: litellm.BadRequestError: ExceededBudget: Budget has been exceeded! Current cost: 5.01, Max budget: 5.0",
     "quota"),
    ("HTTP 400: {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low "
     "to access the Anthropic API.'}}", "quota"),
    ("HTTP 429: Rate limit reached for gpt-5 on tokens per min (TPM): Limit 30000", "rate_limit"),
]


@pytest.mark.parametrize("msg,kind", CASES)
def test_error_kind(msg, kind):
    assert c(msg) == kind
