def fix_runtime(filename):
    with open(filename, 'r') as f:
        content = f.read()

    # test_registry_version
    content = content.replace('assert host_registry.version == "1.2.0"', 'assert host_registry.version == "1.4.0"')

    with open(filename, 'w') as f:
        f.write(content)

fix_runtime('tests/test_runtime.py')
