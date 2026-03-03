## 2026-03-05 - Agent Chat Empty State & Intent Dispatch Button State
**Learning:** Adding empty states and disabled button states significantly improves the intuitiveness of the interface, preventing users from attempting actions that won't succeed (e.g., dispatching an empty intent).
**Action:** Always consider what a user should see when a component has no data, and proactively disable buttons that require input before they can function properly.

## 2026-03-05 - Destructive Actions & Form Validation States
**Learning:** Destructive actions without confirmation dialogues frequently lead to accidental data loss. Furthermore, forms that silently ignore submission without required fields confuse users. Adding a popover confirmation for destructive actions and inline warnings for empty form submissions dramatically increases application usability and error prevention.
**Action:** Always wrap destructive actions (like clearing entire histories) in a confirmation step, and ensure all form submissions provide immediate visual feedback if requirements are not met.
