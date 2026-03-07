// plugins/openclaw-sovereign/index.js
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const yaml = require('js-yaml');

const ALIGN_LEDGER_PATH = path.join(process.env.BASE_DIR || __dirname, '..', '..', 'governance', 'ALIGN_LEDGER.yaml');
const STRATEGIES_PATH = path.join(process.env.BASE_DIR || __dirname, '..', '..', 'governance', 'openclaw_strategies.yaml');

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

      const logEntry = `\n- timestamp: ${timestamp}\n  agent_id: ${agentId}\n  tool: ${tool}\n  hash: ${hash}\n`;

      try {
        if (!fs.existsSync(ALIGN_LEDGER_PATH)) {
            fs.writeFileSync(ALIGN_LEDGER_PATH, 'ledger:\n', 'utf8');
        }
        fs.appendFileSync(ALIGN_LEDGER_PATH, logEntry, 'utf8');
        console.log(`[ALIGN AUDIT] Tool call ${tool} by ${agentId} hashed as ${hash}`);
      } catch (err) {
        console.error("Failed to append to ALIGN ledger", err);
      }

      return next();
    },

    'tool-validation': async (context, next) => {
      const { tool } = context;

      // Tier restrictions
      const tier = process.env.SOVEREIGN_TIER || 'hearth';
      const strategy = strategies.strategies && strategies.strategies.find(s => s.id === 'B'); // Strategy B is default

      if (strategy && strategy.tier_restrictions && strategy.tier_restrictions.includes(tier)) {
          if (strategy.tier_restrictions[tier] === 'deny') {
              throw new Error(`Tool ${tool} denied by tier restriction ${tier}`);
          }
      }

      // Gas budget checks
      const gasBudget = parseInt(process.env.GAS_BUDGET || "1000", 10);
      const gasCost = strategy && strategy.gas_cost_multiplier ? parseInt(strategy.gas_cost_multiplier, 10) * 10 : 10;

      if (gasCost > gasBudget) {
          throw new Error(`Gas exhausted for tool ${tool}. Cost: ${gasCost}, Budget: ${gasBudget}`);
      }

      return next();
    }
  }
};
