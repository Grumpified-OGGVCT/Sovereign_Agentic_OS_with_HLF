# HLF User Modules Directory

Place custom `.hlf` modules here. They will be discoverable by `[IMPORT]` statements.

## Convention

- Each module is a `.hlf` file or a directory with a `main.hlf`
- Module name matches the filename (without `.hlf` extension)
- Modules are loaded on-demand and cached by the `ModuleLoader`

## Example

```
hlf/modules/
├── my_utils.hlf          # [IMPORT] my_utils
└── my_package/
    └── main.hlf          # [IMPORT] my_package
```
