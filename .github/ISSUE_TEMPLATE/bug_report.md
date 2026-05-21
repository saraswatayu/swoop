---
name: Bug report
about: Report a swoop behavior that looks wrong
title: ''
labels: bug
assignees: ''
---

## What happened

<!-- One sentence. What did you call, what did you expect, what did you get? -->

## Reproducer

<!-- The shortest swoop call or Python snippet that triggers it. Inline > attached. -->

```python
```

or

```bash
swoop search ...
```

## Environment

- swoop version: `<output of swoop --version, or python -c "import swoop; print(swoop.__version__)">`
- Python version: `<output of python --version>`
- OS: `<macOS 14 / Ubuntu 22.04 / Windows 11 / ...>`
- Install path: `<pip install swoop-flights | pip install -e . | other>`
- Optional extras installed: `<validation | cli | both | neither>`

## Expected vs actual

**Expected:**

**Actual:**

## Verbose output (recommended for RPC issues)

<!-- Re-run with -v / --verbose and paste the stderr output here. Redact any
proxy credentials before pasting. -->

```
$ swoop search ... -v
```

## Anything else

<!-- Linked Google Flights URL if relevant, timing (was it working last week?),
hypotheses you've already ruled out, fixture or recorded payload. -->
