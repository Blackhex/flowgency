Read shared/memory.md for accepted risks and prior security decisions.

Run a security review of the codebase. Check for:
- OWASP Top 10 vulnerabilities (injection, auth issues, XSS, etc.)
- Hardcoded secrets, API keys, or credentials
- Dependencies with known CVEs
- Insecure default configurations
- Missing input validation on user-facing endpoints

Write observations in shared/observations/ for any findings. Include:
- Severity (critical/high/medium/low)
- Specific location in code
- Remediation steps
- Whether this is a new finding or a regression

Check shared/memory.md accepted risks before flagging known items — only re-flag if context has changed.
