# Request-scoped transport diagnostics

Existing `rpc_calls` fields count logical provider calls. An HTTP 429 retry can
make several HTTP requests for one logical call. Use an
explicit recorder when actual transport work matters:

```python
from dataclasses import asdict
import swoop

recorder = swoop.DiagnosticsRecorder()
transport = swoop.TransportConfig(retries=1, diagnostics=recorder)
try:
    result = swoop.search("JFK", "LAX", "2026-11-15", transport=transport)
finally:
    measurements = asdict(recorder.snapshot())
```

Create a fresh recorder for every independent request. Reuse that recorder for
caller-controlled retries or proxy failover to retain failed attempts. A single
request's concurrent workers may share its recorder; each query receives a
separate ID through a derived transport config. No global request context is
used and the original transport config is unchanged.

Snapshots expose:

- Actual HTTP attempts, RPC/page attempts, and HTTP retry attempts. External
  caller retries remain separate query records; their HTTP retry index starts
  at zero.
- Summed network/body-read time, decode time, retry wait, and wall time from
  the first measured dispatch through the last measured completion. Concurrent
  network times can sum to more than elapsed wall time.
- Decoded UTF-8 response bytes. These are **not compressed wire bytes**. Failed
  reads and unread non-200 bodies have unknown byte counts, reported separately.
- Exact airport/date bounds, requested cabin, success/empty/error/pending
  outcome, decoded count, and raw count/list scope where the provider adapter
  supplies them. Missing raw count or scope stays unknown.

These measurements cover the shared Flights RPC POST transport and shopping
and booking decoding. They do not claim that session/bootstrap requests or
other Google products are fully measured. A runtime with the public search-page
fallback can instrument its GET using the same recorder and `page` attempt
kind. No additional Google requests are made for diagnostics.

Query coverage describes individual provider responses. It does not declare a
staged journey resolved, remove a truncation reason, or change `is_complete`.
Empty inventory and a failed or unresolved query remain distinct. Typed
`SwoopError` exceptions carry the same immutable snapshot as `.diagnostics`;
the recorder also remains available for other transport exceptions.

The default bounds retain at most 1,024 attempt samples and 256 query samples.
Aggregate attempt counts, byte totals, and timings remain exact after samples
are omitted. `omitted_attempts` and `omitted_queries` make truncation visible.
Bounds too large or malformed to safely retain are represented by empty bounds
and `bounds_available=False`, never partial airport sets.

Snapshots contain no URLs, bodies, headers, cookies, proxy addresses, selectors,
booking tokens, or exception messages. They retain only fixed operation labels,
airport/date/cabin identities, numeric measurements, and exception class names.
