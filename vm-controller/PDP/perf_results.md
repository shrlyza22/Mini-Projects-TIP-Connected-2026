# PDP Performance Results

## Scenario validation

| Scenario | Expected | Attained | Trust | provision_ms | Status |
|---|---|---|---|---|---|
| FULL | FULL | FULL | 90.0 | 230.1 | 200 |
| LIMITED | LIMITED | LIMITED | 69.0 | 43.5 | 200 |
| DENIED | DENIED | DENIED | 38.0 | None | 403 |

## Provisioning / revocation latency (login-logout churn)

- **Provisioning (login)**: n=20  mean=143.5  p50=139.6  p95=197.1  min=100.8  max=226.5 ms
- **Revocation (logout)**: n=20  mean=94.3  p50=90.8  p95=138.4  min=73.9  max=167.1 ms
- **Client login round-trip**: n=20  mean=146.5  p50=143.3  p95=199.4  min=102.2  max=228.8 ms
- **Client logout round-trip**: n=20  mean=98.3  p50=92.1  p95=142.2  min=76.2  max=169.9 ms

## Concurrent revocation (controller load)

- Concurrent sessions: 4
- Flows revoked: 64
- Total revocation wall time: 248.1 ms
- Mean per session: 62.0 ms

## ODL controller CPU during churn

- CPU time consumed: 6.90 s over 4.90 s wall
- Average CPU: 140.8%

## /metrics snapshot after the run

- logins: 26
- logouts: 26
- flows_installed: 464
- flows_revoked: 448
- flow_put_samples: 464
- flow_put_ms_avg: 7.84
- flow_put_ms_p50: 6.98
- flow_put_ms_p95: 13.06
- flow_put_ms_max: 45.51
