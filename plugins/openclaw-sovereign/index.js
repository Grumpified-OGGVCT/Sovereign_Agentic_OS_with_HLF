// plugins/openclaw-sovereign/index.js
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const yaml = require('js-yaml');

const BASE_DIR = process.env.BASE_DIR;
const AUDIT_LOG_PATH = BASE_DIR
  ? path.join(BASE_DIR, 'observability', 'openclaw_audit.log')
  : path.join(__dirname, '..', '..', 'observability', 'openclaw_audit.log');
const STRATEGIES_PATH = BASE_DIR
  ? path.join(BASE_DIR, 'governance', 'openclaw_strategies.yaml')
  : path.join(__dirname, '..', '..', 'governance', 'openclaw_strategies.yaml');

// Load strategies to validate
let strategies = {};
try {
  const fileContents = fs.readFileSync(STRATEGIES_PATH, 'utf8');
  strategies = yaml.load(fileContents);
} catch (e) {
  console.log("Could not load openclaw_strategies.yaml:", e);
}

module.exports = {
  name: "openclaw-sovereign",
  version: "0.1.0",

  hooks: {
    'boot-md': async (agent) => {
      // Inject Sovereign OS governance rules as system prompt
      const governancePrompt = `[SYSTEM GOVERNANCE] You are operating under Sovereign OS ALIGN rules. All tool calls are audited via InsAIts and recorded on the ALIGN Ledger. Do not bypass security controls.`;
      if (agent.systemPrompt) {
        agent.systemPrompt += "\n\n" + governancePrompt;
      } else {
        agent.systemPrompt = governancePrompt;
      }
      return agent;
    },

    'command-logger': async (context, next) => {
      // Log every tool call to ALIGN Ledger
      const { tool, params, agentId } = context;
      const timestamp = new Date().toISOString();

      const payload = JSON.stringify({ agentId, tool, params, timestamp });
      const hash = crypto.createHash('sha256').update(payload).digest('hex');

      const logEntry = JSON.stringify({ timestamp, agentId, tool, params, hash }) + '\n';

      try {
        const logDir = path.dirname(AUDIT_LOG_PATH);
        if (!fs.existsSync(logDir)) {
            fs.mkdirSync(logDir, { recursive: true });
        }
        fs.appendFileSync(AUDIT_LOG_PATH, logEntry, 'utf8');
        console.log(`[ALIGN AUDIT] Tool call ${tool} by ${agentId} hashed as ${hash}`);
      } catch (err) {
        console.error("Failed to append to OpenClaw audit log", err);
      }

      return next();
    },

    'tool-validation': async (context, next) => {
      const { tool } = context;

      // Tier restrictions based on openclaw_strategies.yaml
      const tier = process.env.DEPLOYMENT_TIER || 'hearth';
      const strategy =
        strategies.strategies && Array.isArray(strategies.strategies)
          ? strategies.strategies.find((s) => s && s.id === 'strategy-b')
          : null;

      if (strategy && strategy.requirements) {
        const requirements = strategy.requirements;

        // Enforce allowed tiers (requirements is an array of objects)
        const tiersEntry = Array.isArray(requirements)
          ? requirements.find((r) => r && r.tiers)
          : null;
        const allowedTiers = tiersEntry ? tiersEntry.tiers : null;

        if (Array.isArray(allowedTiers) && allowedTiers.length > 0) {
          if (!allowedTiers.includes(tier)) {
            throw new Error(
              `Tool ${tool} is not permitted for deployment tier ${tier} under strategy ${strategy.id}`
            );
          }
        }

        // Gas budget checks using requirements.gas_range
        const gasRangeEntry = Array.isArray(requirements)
          ? requirements.find((r) => r && r.gas_range)
          : null;
        const gasRange = gasRangeEntry ? gasRangeEntry.gas_range : null;
        const gasBudget = parseInt(process.env.GAS_BUDGET || '1000', 10);

        if (Array.isArray(gasRange) && gasRange.length === 2) {
          const minGas = parseInt(gasRange[0], 10);
          const maxGas = parseInt(gasRange[1], 10);

          if (Number.isFinite(minGas) && Number.isFinite(maxGas)) {
            if (gasBudget < minGas || gasBudget > maxGas) {
              throw new Error(
                `Gas budget ${gasBudget} is outside allowed range [${minGas}, ${maxGas}] for strategy ${strategy.id} and tool ${tool}`
              );
            }
          }
        }
      }

      return next();
    }
  }
};
