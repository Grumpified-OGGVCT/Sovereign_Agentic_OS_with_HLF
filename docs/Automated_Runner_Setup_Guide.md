# Exhaustive Setup Guide: Automated Runners & Multi-Provider Agents

This guide details exactly how to deploy the Sovereign Agentic OS in a headless, automated continuous integration/continuous deployment (CI/CD) environment (specifically GitHub Actions), utilizing the multi-provider APIs you have configured in your GitHub Environment Secrets.

## Prerequisites
1. A GitHub Repository containing the Sovereign Agentic OS.
2. An Environment set up in the repository (e.g., `production` or `autonomous-agents`).
3. The Secrets as listed in your screenshot added to that Environment.

## Step 1: The Workflow File
We have created `.github/workflows/autonomous-runner.yml`. 
This file defines:
- **Triggers**: It runs automatically every night at 3 AM UTC (`cron`) OR manually via the Actions tab (`workflow_dispatch`), allowing you to manually feed it HLF intents to process.
- **Environment Context**: It explicitly targets the environment where your secrets live.
- **Secret Injection**: It maps `${{ secrets.OPENROUTER_API }}` to standard environment variables `OPENROUTER_API` securely at runtime. The secrets are never printed to the logs.

## Step 2: How the OS Uses These Keys
Currently, the Sovereign OS defaults to local Ollama endpoints to save costs. However, in an automated GitHub Action runner, local GPU may not be available. 
This is why the Cloud Provider APIs are essential. 

In `config/settings.json`, ensure that your router logic is aware of these cloud fallbacks. OpenRouter provides an OpenAI-compatible endpoint.

When the GitHub Action runs:
1. It spins up a temporary Ubuntu VM.
2. It brings up Redis via Docker for the Agent Service Bus.
3. It syncs the Python environment using `uv`.
4. It reads the `DEPLOYMENT_TIER: "forge"` to allow higher gas limits for automated processing.
5. `dream_state.py` or the `MoMA Router` will automatically detect the presence of `OPENROUTER_API` (or others) and route inference requests to the cloud provider instead of attempting to hit `localhost:11434`.

### Using OpenRouter / Ollama Cloud natively
If you are strictly using standard OpenAPI/Ollama libs internally, set the environment variables in the workflow to redirect the base URL:

```yaml
env:
  # Route standard OpenAI library calls to OpenRouter
  OPENAI_BASE_URL: "https://openrouter.ai/api/v1"
  OPENAI_API_KEY: ${{ secrets.OPENROUTER_API }}
  
  # Or use standard Ollama python library against a cloud endpoint
  OLLAMA_HOST: "https://your-ollama-cloud-endpoint.com"
  OLLAMA_API_KEY: ${{ secrets.OLLAMA_API_KEY }}
```

## Step 3: Integrating Future Models (DeepSeek, Gemini, Grok)
Right now, you have the keys staged in GitHub Secrets. For the AI agents to natively use them, the `MoMA Router` (`agents/gateway/router.py`) needs explicit logic to parse these specific provider libraries (like google-generativeai for Gemini or anthropic for Opus).

Currently, **OpenRouter** and **Ollama (Cloud)** are the most seamless because they conform to standard API schemas (OpenAI and Ollama base APIs).

### Action Items for You
1. **Push the workflow**: Commit `.github/workflows/autonomous-runner.yml` to your repository.
2. **Assign Environment**: Make sure the environment name in the yml file (`environment: production`) exactly matches the name of the Environment where you placed the secrets in GitHub Settings.
3. **Trigger it**: Go to the Actions tab in GitHub, select "Autonomous Agent Runner", and run it manually with a test intent like `[HLF-v3] Δ analyze /system/health Ω`.

## MCP and Antigravity Interaction
While Antigravity operates locally on your machine via the MCP Server (`run.bat` option 3), you can use it to craft these workflows. Antigravity does **not** run inside the GitHub Action. Instead, Antigravity acts as your locally run co-pilot that configures the OS, writes the workflows, and pushes them to GitHub. The GitHub Actions runner then executes the OS autonomously using the Cloud Provider keys.
