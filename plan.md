1. **Understand the Goal**: The user wants an extremely accurate and comprehensive markdown document detailing ALL personas, hats, their orchestrations, interconnections, external tools access, OS design flow, OS level and layer explanations, demo page current state, and the Discord MCP integration plans. We also need to add flow charts and mindmaps.

2. **Data Extraction**:
   - Extract the 14-Hat system from `.github/agents/hats.agent.md`.
   - Extract the 19 personas from `.github/agents/hats.agent.md` and `config/agent_registry.json`.
   - Extract the universal mandates from `config/personas/_shared_mandates.md`.
   - Extract the core validation dimensions (CoVE's 12-Dimension Audit Matrix).
   - Extract external tool access from `docs/UNIFIED_ECOSYSTEM_ROADMAP.md` and `docs/handoff_zai_api_integration.md`.
   - Extract the OS Master Build Plan layers and layers from `docs/Sovereign_OS_Master_Build_Plan.md`.
   - Extract the Discord MCP integration plans (actually there are none explicitly defined as MCP, only `agents/core/discord_client.py` exists, we need to explicitly note this conflict/omission).
   - Extract the current state of the demo page (e.g. `docs/index.html` and `docs/WALKTHROUGH.md`).

3. **Synthesize the Output**:
   - Format the markdown document nicely.
   - Use Mermaid charts for flowcharts, mindmaps, and interconnections (this fulfills the requirement "complete with flow charts and mindmaps").
   - Add a section on "Divergences and Conflicts" addressing the Discord MCP integration vs `discord_client.py`.
   - Add a section on the "User's Point of View".
   - Double-check all facts against the codebase.

4. **Action Items**:
   - Save the synthesized content to a new markdown file `docs/SOVEREIGN_SYSTEM_TRUTH.md`.
   - Ensure pre-commit checks are run.
