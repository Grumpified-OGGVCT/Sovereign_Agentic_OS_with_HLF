# Standard Library

HLF ships with 5 built-in modules in `hlf/stdlib/`. Import them with `[IMPORT] module_name`.

---

## math

Core mathematical utilities.

```hlf
[IMPORT] math
```

### Functions

| Function | Arguments | Description |
|----------|-----------|-------------|
| `abs` | `value` | Absolute value |
| `max` | `a`, `b` | Maximum of two values |
| `min` | `a`, `b` | Minimum of two values |
| `clamp` | `value`, `lo`, `hi` | Clamp value to range |
| `floor` | `value` | Round down |
| `ceil` | `value` | Round up |
| `round` | `value` | Round to nearest integer |

### Constants

| Name | Value |
|------|-------|
| `PI` | `3.141592653589793` |
| `E` | `2.718281828459045` |
| `TAU` | `6.283185307179586` |
| `INFINITY` | `999999999999` |

---

## string

String manipulation utilities.

```hlf
[IMPORT] string
```

### Functions

| Function | Arguments | Description |
|----------|-----------|-------------|
| `upper` | `text` | Convert to uppercase |
| `lower` | `text` | Convert to lowercase |
| `trim` | `text` | Strip whitespace |
| `contains` | `text`, `substring` | Check if substring exists |
| `starts_with` | `text`, `prefix` | Check prefix |
| `ends_with` | `text`, `suffix` | Check suffix |
| `replace` | `text`, `old`, `new` | Replace occurrences |
| `split` | `text`, `delimiter` | Split into list |
| `join` | `items`, `delimiter` | Join list into string |
| `length` | `text` | String length |
| `substring` | `text`, `start`, `end` | Extract substring |
| `repeat` | `text`, `count` | Repeat string |

### Constants

| Name | Value |
|------|-------|
| `EMPTY` | `""` |
| `NEWLINE` | `"LF"` |
| `TAB` | `"HT"` |

---

## io

File and stream I/O utilities.

```hlf
[IMPORT] io
```

### Functions

| Function | Arguments | Description |
|----------|-----------|-------------|
| `read_file` | `path` | Read file contents |
| `write_file` | `path`, `content` | Write to file |
| `append_file` | `path`, `content` | Append to file |
| `file_exists` | `path` | Check if file exists |
| `list_dir` | `path` | List directory contents |
| `delete_file` | `path` | Delete a file |
| `copy_file` | `source`, `destination` | Copy a file |
| `file_size` | `path` | Get file size in bytes |

### Constants

| Name | Value |
|------|-------|
| `STDIN` | `"stdin"` |
| `STDOUT` | `"stdout"` |
| `STDERR` | `"stderr"` |

---

## crypto

Cryptographic hashing and verification.

```hlf
[IMPORT] crypto
```

### Functions

| Function | Arguments | Description |
|----------|-----------|-------------|
| `sha256` | `data` | SHA-256 hash |
| `sha512` | `data` | SHA-512 hash |
| `md5` | `data` | MD5 hash (non-cryptographic) |
| `hmac_sign` | `key`, `data` | HMAC signature |
| `hmac_verify` | `key`, `data`, `signature` | Verify HMAC |
| `uuid4` | `prefix` | Generate UUID v4 |
| `random_bytes` | `count` | Random byte string |
| `base64_encode` | `data` | Base64 encode |
| `base64_decode` | `data` | Base64 decode |

---

## collections

Data structure utilities — lists and maps.

```hlf
[IMPORT] collections
```

### List Functions

| Function | Arguments | Description |
|----------|-----------|-------------|
| `list_new` | `items` | Create new list |
| `list_append` | `list`, `item` | Append item |
| `list_get` | `list`, `index` | Get item by index |
| `list_length` | `list` | List length |
| `list_slice` | `list`, `start`, `end` | Slice sublist |
| `list_contains` | `list`, `item` | Check membership |
| `list_reverse` | `list` | Reverse list |
| `list_sort` | `list` | Sort list |

### Map Functions

| Function | Arguments | Description |
|----------|-----------|-------------|
| `map_new` | `entries` | Create new map |
| `map_get` | `map`, `key` | Get value by key |
| `map_set` | `map`, `key`, `value` | Set key-value pair |
| `map_has` | `map`, `key` | Check if key exists |
| `map_keys` | `map` | Get all keys |
| `map_values` | `map` | Get all values |
| `map_delete` | `map`, `key` | Delete key |
| `map_merge` | `map_a`, `map_b` | Merge two maps |
