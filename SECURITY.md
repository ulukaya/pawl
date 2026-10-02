# Security

pawl's gates are guardrails, not a sandbox: they read tool arguments, and a
script that builds a path at run time is not fenced. Within that scope,
these are security bugs; report them privately:

*   `readonly` approving a command that writes, deletes or reaches the
    network. It is the one gate that skips a permission prompt (on Claude
    Code and Antigravity), so a false approval removes a human check.
*   A tool call or payload that makes a fail-closed gate (`git`, `poll`, the
    egress firewall) allow.
*   Anything that makes pawl itself run a command, write outside
    `PAWL_DATA`, or open a network connection.

A gate missing a destructive variant it was never meant to cover, or a false
positive, is an ordinary issue.

## Reporting

Use GitHub's private vulnerability reporting:
https://github.com/ulukaya/pawl/security/advisories/new

Include the harness, the pawl version (`.claude-plugin/plugin.json`), the
payload or command, and what pawl answered:

```bash
python3 hooks/pawl.py pre --harness claude < payload.json
```

This is a one-maintainer project; reports are handled on a best-effort
basis, and fixes ship as a new release on `main`, the only supported line.
