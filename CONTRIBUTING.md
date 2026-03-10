# Contributing

Thank you for contributing to twingate-device-trust-bridge!

## Getting started

```bash
git clone https://github.com/your-org/twingate-device-trust-bridge
cd twingate-device-trust-bridge
pip install -e ".[dev]"
```

## Running the test suite

```bash
pytest
```

All tests mock HTTP — no real MDM credentials are required.

## Code style

- Python 3.12+ syntax is fine (match statements, `X | Y` union types, etc.).
- Async throughout — `async def`, `httpx.AsyncClient`, `asyncio.gather` for parallel provider queries.
- Type hints on every function signature.

## Adding a new provider

See [docs/adding-a-provider.md](docs/adding-a-provider.md) for a step-by-step guide.

## Pull request checklist

- [ ] All existing tests pass (`pytest`)
- [ ] New code is covered by tests (mock HTTP calls with `respx`)
- [ ] New provider includes a setup doc in `docs/providers/`

## Design constraints

These are intentional and must not be relaxed:

- **Never untrust** — the bridge only sets `isTrusted: true`, never `false`.
- **Stateless** — no database, no cache files. Every run re-fetches from providers.
- **Provider failure is non-fatal** — if one provider errors, log and skip; continue with the others.
- **Serial numbers** — always normalised with `.strip().upper()` before comparison.
- **Secrets** — must live in environment variables, never hard-coded in config files.
- **Pagination** — every `list_devices()` must exhaust all pages; never assume a single page.

## License

By contributing you agree your work will be released under the [Apache 2.0 License](LICENSE).
