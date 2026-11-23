# Connecting a model: what to paste for each provider

Settings, Models and providers, Connection. Pick the provider (or leave "Detect from the address" and paste the address), paste the key, press Test connection, pick a model, Save. In the terminal, `/login` (then "Another provider") or `meshpool login` asks the same questions.

A full address pasted from a provider's docs works too: Meshpool cuts off `/chat/completions`, `/models` or `/messages` and, for Azure, reads the deployment name and `api-version` out of it.

| Provider | Address to paste | Key | Model box |
|---|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `sk-...` | pick from the list |
| Anthropic | `https://api.anthropic.com` | `sk-ant-...` | pick from the list |
| OpenRouter | `https://openrouter.ai/api/v1` | `sk-or-...` | pick from the list |
| Groq | `https://api.groq.com/openai/v1` | `gsk_...` | pick from the list |
| Together AI | `https://api.together.xyz/v1` | Together key | pick from the list |
| Fireworks | `https://api.fireworks.ai/inference/v1` | `fw_...` | pick from the list |
| Mistral | `https://api.mistral.ai/v1` | Mistral key | pick from the list |
| xAI | `https://api.x.ai/v1` | `xai-...` | pick from the list |
| DeepSeek | `https://api.deepseek.com` | `sk-...` | pick from the list |
| Perplexity | `https://api.perplexity.ai` | `pplx-...` | type a model (for example `sonar`): no list, no tool calling |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | `AIza...` (sent as Bearer) | pick from the list |
| Amazon Bedrock | `https://bedrock-runtime.REGION.amazonaws.com/openai/v1` | a Bedrock API key (not an AWS access key) | pick from the list |
| GitHub Models | `https://models.inference.ai.azure.com` | a GitHub token with the models scope | pick from the list |
| NVIDIA NIM | `https://integrate.api.nvidia.com/v1` | `nvapi-...` | pick from the list |
| Cerebras, SambaNova | `https://api.cerebras.ai/v1`, `https://api.sambanova.ai/v1` | provider key | pick from the list |
| Ollama, LM Studio | `http://localhost:11434/v1`, `http://localhost:1234/v1` | none (leave empty) | pick from the list |
| vLLM, LiteLLM proxy, your own gateway | its address, usually ending `/v1` | whatever it expects as a Bearer key | list if it has one, else type |
| Azure OpenAI | `https://NAME.openai.azure.com` | Key 1 or Key 2 | the deployment name |
| Azure OpenAI v1 route | `https://NAME.openai.azure.com/openai/v1` | Key 1 or Key 2 | the deployment name |
| Azure AI Foundry | `https://NAME.services.ai.azure.com` (OpenAI route) or `.../models` | resource key | the deployment name |
| Claude on Azure Foundry | `https://NAME.services.ai.azure.com/anthropic` | resource key | the deployment name |

## Azure walkthrough

1. In the Azure portal open your Azure OpenAI or Foundry resource, then Keys and Endpoint. Copy the endpoint (`https://NAME.openai.azure.com`) and Key 1.
2. In Azure AI Foundry, Deployments, copy the name of the deployment you created. Azure runs deployments you name yourself, so this is the "model" Meshpool needs. It is not always the model's own name.
3. In Meshpool paste the endpoint. The page recognises Azure and shows Deployment name and API version. Paste the deployment name. Leave the API version alone (default `2024-10-21`) unless Azure says it is not supported.
4. Paste the key and press Test connection. Azure has no list of your deployments to read, so the test is a one-token chat call to your deployment. Without a deployment name the test says so rather than calling the key bad.
5. Save. You can also paste the whole address from the portal's code sample, for example `https://NAME.openai.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2024-10-21`: the deployment and version are picked out of it.

Meshpool sends the key in Azure's `api-key` header, never as a Bearer token, and never to any host but the address you saved. The `/openai/v1` address needs no api-version.

## Keys in your environment

Meshpool offers these under "Use an existing login", each only for its own host, after you confirm: `OPENROUTER_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY` / `GOOGLE_API_KEY`, `MISTRAL_API_KEY`, `XAI_API_KEY`, `TOGETHER_API_KEY`, `FIREWORKS_API_KEY`, `DEEPSEEK_API_KEY`, `PERPLEXITY_API_KEY`, `CEREBRAS_API_KEY`, `SAMBANOVA_API_KEY`, `NVIDIA_API_KEY`, `ANTHROPIC_API_KEY` (with `ANTHROPIC_BASE_URL` if you set one).

Used automatically when no Meshpool key is saved, and only as a pair with their own address: `OPENAI_API_KEY` with `OPENAI_BASE_URL`, then `AZURE_OPENAI_API_KEY` with `AZURE_OPENAI_ENDPOINT` (plus `AZURE_OPENAI_API_VERSION` and `AZURE_OPENAI_DEPLOYMENT`, which becomes the model).

## When something fails

Errors are shown in plain words: a wrong key (and a key that looks like another provider's), a deployment that does not exist on the resource, an api-version Azure does not support, a rate limit or quota, a firewall rule on the Azure resource.

## Which agents work with which endpoint

Meshpool's own agents work with every provider above. Pi, Hermes and OpenCode only send a Bearer key, so for Azure OpenAI classic and Foundry Meshpool starts a small relay on 127.0.0.1 that adds the `api-key` header and the deployment path; the agent never sees your real key. Azure's v1 address and all Bearer providers are used directly. Codex needs the OpenAI Responses API: it is configured for Azure OpenAI addresses (with the api-version) but not for the Foundry `/models` route or Claude on Azure, and the settings page says so. See docs/plan/0.0.13-keys.md for what is verified.
