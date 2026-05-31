def fix_file(filename, rules):
    with open(filename, 'r') as f:
        content = f.read()

    for old, new in rules:
        content = content.replace(old, new)

    with open(filename, 'w') as f:
        f.write(content)

fix_file('tests/test_external_app_dispatch.py', [
    ('"details": {"format": "", "family": "mistral3", "parameter_size": "675000000000", "quantization_level": "fp8"}}',
     '"details": {"format": "", "family": "mistral3",\n                      "parameter_size": "675000000000", "quantization_level": "fp8"}}')
])
